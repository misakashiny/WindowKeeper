# -*- coding: utf-8 -*-
"""
WindowKeeper 的日志。

设计目标（按重要性排序）：

1. **出问题时能自证。** 这是一个托盘常驻程序，用户看不到控制台。
   所以日志必须落到文件，而且必须记录「引擎做了什么决定、为什么」，
   不只是「出错了」。
2. **不拖慢主流程。** 还原窗口是即时操作，日志要能异步或至少廉价。
   这里是同步写，但用缓冲 + 行缓冲，单条开销在微秒级。
3. **不能无限长大。** 轮转，默认单文件 1MB，保留 3 份。
4. **方便求助。** 提供「打包诊断」，把环境信息 + 日志 + 预设摘要打成一个文件，
   用户直接发过来就能定位问题。

日志位置：`%APPDATA%\\WindowKeeper\\logs\\windowkeeper.log`

格式：
    文件里是人类可读的一行文本；同时提供 JSONL 变体给机器读。
    之所以不直接上 JSONL 单格式：用户用记事本打开日志时，
    一行 JSON 比一行文本难读得多。默认写人类可读，需要结构化时另开。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import sys
import traceback
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 自定义级别：把「引擎的业务决策」和普通 INFO 区分开。
# 还原窗口时我们希望默认就能看到「把哪个窗口从哪挪到哪」，
# 但又不想让它淹没在普通调试信息里。
# --------------------------------------------------------------------------
DECISION = 25
logging.addLevelName(DECISION, "DECISION")

_LOGGER_NAME = "windowkeeper"
_configured = False
_extra_handlers: list[logging.Handler] = []


def log_dir() -> Path:
    """日志目录。跟着预设数据目录走，保证「打开数据文件夹」能一起找到。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(base) / "WindowKeeper" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_file() -> Path:
    return log_dir() / "windowkeeper.log"


def jsonl_file() -> Path:
    return log_dir() / "windowkeeper.jsonl"


class _DecisionAdapter(logging.LoggerAdapter):
    """只在需要时算 extra，避免每次都构造字典。"""

    def decision(self, msg: str, *args: Any, **kw: Any) -> None:
        self.log(DECISION, msg, *args, **kw)


class WKLogger(logging.Logger):
    """
    支持结构化字段的 Logger。

    ★ 为什么必须有这个类：标准 `Logger.info(msg, **kwargs)` 只认
      exc_info / stack_info / stacklevel / extra 这几个关键字，
      其余的任何 `xxx=yyy` 都会被当成非法参数抛 TypeError。
      而本项目最需要的能力恰恰是「消息 + 一堆字段」。

    契约（很重要，写错会静默丢字段）：
      · `msg` 里的 %-占位符，用**位置参数**填：  info("%d 个窗口", 8, hwnd=1)
      · 想记的字段，用**关键字参数**传：        info("还原", hwnd=1, score=100)
      · 两者可以混用：                          info("还原 %s", name, hwnd=1)

      `decision()` 是同样规则。
    """

    def _log(self, level, msg, args, exc_info=None, extra=None,
             stack_info=False, stacklevel=1, **fields):  # type: ignore[override]
        if fields:
            extra = {**(extra or {}), "wk": fields}
        super()._log(level, msg, args, exc_info, extra, stack_info, stacklevel)

    # 单独声明 decision 是为了类型提示和可读性；
    # 实际转发到 _log，因此字段规则完全一致。
    def decision(self, msg: str, *args: Any, **fields: Any) -> None:  # type: ignore[override]
        self._log(DECISION, msg, args, **fields)


# ★ 必须在**任何** logging.getLogger() 之前设置 class，
#   否则已经建好的 logger 还是标准类，字段会被当非法参数丢掉。
logging.setLoggerClass(WKLogger)


class _HumanFormatter(logging.Formatter):
    """
    人类可读格式。

        12:34:56.789  INFO     启动 WindowKeeper 0.3.0
        12:34:57.001  决定     开始还原预设  [preset=工作  windows=8  dry_run=0]
        12:34:57.030  决定     精确还原：Notepad.exe  [requested=320,260 760x520  actual=320,260 760x520  score=100]
        12:34:57.040  错误     移动窗口失败 hwnd=12345 (msedge.exe)

    ★ 结构化字段（wk 字典）会以 [k=v k=v] 的形式附在行尾。
      这一点很关键：用户发回来的就是这份人类可读日志，
      如果字段只进 JSONL，那排障时最关键的信息反而看不到。
    """

    LEVEL_ABBR = {
        "DEBUG": "DEBUG", "INFO": "INFO", "DECISION": "决定",
        "WARNING": "警告", "ERROR": "错误", "CRITICAL": "致命",
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        lvl = self.LEVEL_ABBR.get(record.levelname, record.levelname)
        msg = record.getMessage()
        line = f"{ts}  {lvl:<5} {msg}"

        fields = getattr(record, "wk", None)
        if fields:
            parts = [f"{k}={_short(v)}" for k, v in fields.items()
                     if v is not None and v != ""]
            if parts:
                line += "  [" + "  ".join(parts) + "]"

        if record.exc_info:
            line += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return line


def _short(v: Any, limit: int = 60) -> str:
    """字段值太长就截断，避免一行日志爆掉。"""
    s = str(v)
    return s if len(s) <= limit else s[:limit - 1] + "…"


class _JsonlFormatter(logging.Formatter):
    """机器可读格式。给「打包诊断」和自动化分析用。"""

    def format(self, record: logging.LogRecord) -> str:
        obj: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
            "mod": record.module,
            "line": record.lineno,
        }
        # 引擎塞进来的结构化字段（见 engine.py 的 log_decision）
        for k, v in getattr(record, "wk", {}).items():
            obj[k] = v
        if record.exc_info:
            obj["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()
        return json.dumps(obj, ensure_ascii=False)


def _resolve_level(level: Any) -> int:
    """
    把级别参数统一成 int。

    ★ 踩过的坑：这里原来直接写 `level.upper()`，签名却标注成 `str`。
      CLI 和托盘传的是 `logging.INFO`（int），一进来就炸
      `AttributeError("'int' object has no attribute 'upper'")`，
      而且是在 try 里被吞掉、只打一行 stderr，表现为「日志文件建不出来」。
      所以现在显式接受 str / int 两种写法。
    """
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        got = getattr(logging, level.strip().upper(), None)
        if isinstance(got, int):
            return got
    return logging.INFO


def setup(level: Any = "INFO",
          console: bool = False,
          max_bytes: int = 1024 * 1024,
          backup_count: int = 3) -> logging.Logger:
    """
    配置日志。重复调用是安全的（幂等）。

    `level` 既接受 `"INFO"` 这样的字符串，也接受 `logging.INFO` 这样的整数。

    console=True 时同时输出到 stderr——CLI 场景下用，托盘场景下要关掉，
    因为打包成 console=False 的 exe 时 stderr 无处可去。

    ★ 幂等是按「首次配置」生效的：第一次 setup() 之后，
      再调用不会重复挂 handler（否则同一条日志会写 N 遍），
      也不会改变已有 handler 的级别。要改级别请显式 setLevel()。
    """
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    lvl = _resolve_level(level)
    # 容器级别留 DEBUG，过滤交给各 handler——这样 JSONL 能始终收全量。
    logger.setLevel(logging.DEBUG)

    if _configured:
        # 不重复挂 handler，但允许后续调用调整可见级别，
        # 否则 CLI 的 --verbose 就没法在托盘已启动的进程里生效。
        for h in logger.handlers:
            if isinstance(h, logging.handlers.RotatingFileHandler) \
                    and isinstance(h.formatter, _HumanFormatter):
                h.setLevel(lvl)
        return logger

    logger.propagate = False

    # --- 文件：人类可读 ---
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file(), maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8", delay=True)
        fh.setFormatter(_HumanFormatter())
        fh.setLevel(lvl)
        logger.addHandler(fh)
    except Exception as exc:  # noqa: BLE001
        # 日志系统自身不能成为崩溃源
        print(f"[WindowKeeper] 无法创建日志文件: {exc!r}", file=sys.stderr)

    # --- 文件：JSONL，给机器读 ---
    try:
        jh = logging.handlers.RotatingFileHandler(
            jsonl_file(), maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8", delay=True)
        jh.setFormatter(_JsonlFormatter())
        jh.setLevel(logging.DEBUG)      # JSONL 收全量，方便事后追
        logger.addHandler(jh)
    except Exception:  # noqa: BLE001
        pass

    # --- 控制台（可选）---
    if console:
        try:
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(_HumanFormatter())
            sh.setLevel(lvl)
            logger.addHandler(sh)
        except Exception:  # noqa: BLE001
            pass

    # ★ 只有真正挂上了 handler 才算配置完成。
    #   否则启动早期的一次失败会把「已配置」写死，
    #   之后再调 setup() 直接 return，日志永远建不起来。
    _configured = bool(logger.handlers)
    return logger


def get(name: str = "") -> WKLogger:
    """
    拿一个子 logger。返回的是 WKLogger，**支持直接传结构化字段**：

        log = get("engine")
        log.info("枚举到 %d 个窗口", 9, monitors=3)   # 位置参数填 %d，关键字参数当字段
        log.warning("热键被占用", hotkey="ctrl+alt+1")  # 全字段、无占位符

    ★ 这两条规则必须分清：想记的字段一律用**关键字**传；
      要填进消息文本的，才用位置参数。混了就会被日志格式当成
      「格式化参数」而报 TypeError，或被静默丢弃。
    """
    full = f"{_LOGGER_NAME}.{name}" if name else _LOGGER_NAME
    return logging.getLogger(full)  # type: ignore[return-value]


def decision(msg: str, *args: Any, **fields: Any) -> None:
    """
    记录一条「引擎的业务决策」。

    比 info 高一级，默认可见，用于还原窗口这类关键动作：
        decision("还原窗口", exe="notepad.exe", frm=(300, 300), to=(320, 260))

    ★ 和其他调用一样：%-占位符用位置参数，想记的字段用关键字参数。
    """
    logging.getLogger(f"{_LOGGER_NAME}.engine").decision(msg, *args, **fields)


def exception(msg: str, **fields: Any) -> None:
    """记录当前异常（含堆栈）。只在 except 块里调用。"""
    logging.getLogger(f"{_LOGGER_NAME}.error").error(
        msg, exc_info=True, **fields)


def swallow(what: str, exc: BaseException, **fields: Any) -> None:
    """
    记录一个被有意吞掉的异常。

    ★ 这个函数的存在本身就是设计决定：项目里有不少
      `except Exception: pass`，它们让「出问题时什么都看不到」。
      改成 `except Exception as e: log.swallow("xxx", e)` 之后，
      控制流没变（照样继续跑），但痕迹留下了。
    """
    logging.getLogger(f"{_LOGGER_NAME}.swallow").warning(
        "%s 失败（已忽略继续）: %r", what, exc,
        swallowed=what, **fields)


# --------------------------------------------------------------------------
# 诊断打包
# --------------------------------------------------------------------------

def _env_report() -> str:
    """环境快照。排查问题时最需要这些。"""
    lines = [
        "=" * 70,
        "WindowKeeper 诊断报告",
        "=" * 70,
        f"生成时间      {datetime.now().isoformat(timespec='seconds')}",
        "",
        "--- 程序 ---",
    ]
    try:
        from . import __version__
        lines.append(f"版本          {__version__}")
    except Exception:  # noqa: BLE001
        lines.append("版本          (读取失败)")
    lines.append(f"打包运行      {getattr(sys, 'frozen', False)}")
    if getattr(sys, "frozen", False):
        lines.append(f"解压目录      {getattr(sys, '_MEIPASS', '?')}")
    lines.append(f"可执行文件    {sys.executable}")
    lines.append(f"Python        {sys.version.split()[0]}  ({platform.architecture()[0]})")

    lines += ["", "--- 系统 ---"]
    lines.append(f"系统          {platform.system()} {platform.release()} "
                 f"build {platform.version()}")
    lines.append(f"机器          {platform.machine()}")

    # DPI 感知状态是这台机器上最容易出问题的东西，必须记
    lines += ["", "--- DPI 与显示器 ---"]
    try:
        from . import win32_api as w32
        # ★ 必须对比「声明前后」，而不是断言必须是 V2：
        #   老 API 只有三档，无法区分 V1/V2，容易误报。
        #   真正要回答的问题是「显示器数据是不是真的」——
        #   只要感知模式不是 UNAWARE，拿到的就是物理坐标，可以信。
        try:
            w32.enable_per_monitor_v2()
        except Exception as exc:  # noqa: BLE001
            lines.append(f"声明 DPI 感知失败: {exc!r}")

        ctx = w32.dpi_awareness_context()
        old = w32.current_dpi_awareness()
        lines.append(f"DPI 感知上下文  {ctx}   (目标 PER_MONITOR_AWARE_V2)")
        lines.append(f"DPI 感知(旧API) {old}   (只有三档，区分不出 V1/V2)")
        if old == "UNAWARE":
            lines.append("  << 警告：仍是 UNAWARE，下面的显示器数据是虚拟化值，不可信")
        mons = w32.enum_monitors()
        lines.append(f"显示器数量    {len(mons)}")
        for m in mons:
            lines.append(f"  [{m.index}] {m.name:<16} {m.width}x{m.height} "
                         f"@{m.scale:.0%}  dpi={m.dpi}  "
                         f"{'主屏' if m.primary else '  '}  "
                         f"rect={m.rect}  work={m.work}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  读取显示器失败: {exc!r}")

    lines += ["", "--- 预设 ---"]
    try:
        from . import store
        st = store.PresetStore()
        st.load()
        lines.append(f"数据目录      {st.dir}")
        lines.append(f"预设数量      {len(st.presets)}")
        for p in st.presets:
            lines.append(f"  · {p.name:<20} {p.window_count} 个窗口  "
                         f"热键={p.hotkey or '(无)'}  "
                         f"创建={datetime.fromtimestamp(p.created_at).strftime('%Y-%m-%d %H:%M')}"
                         if p.created_at else f"  · {p.name}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  读取预设失败: {exc!r}")

    lines += ["", "--- 日志文件 ---"]
    for f in (log_file(), jsonl_file()):
        if f.exists():
            lines.append(f"{f.name:<20} {f.stat().st_size / 1024:.1f} KB")
        else:
            lines.append(f"{f.name:<20} (不存在)")
    return "\n".join(lines)


def make_diagnostic_zip(out_path: str | Path | None = None) -> Path:
    """
    把环境信息 + 日志 + 预设摘要打包成一个 zip，方便用户发回来。

    里面**不含**预设里的窗口标题以外的隐私内容——标题本来就是用户自己的
    窗口名，不算额外泄露。但不含任何账号、路径以外的个人信息。
    """
    out = Path(out_path) if out_path else (
        Path.home() / "Desktop" /
        f"WindowKeeper诊断_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")

    report = _env_report()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("诊断报告.txt", report)
        for f in (log_file(), jsonl_file()):
            if f.exists():
                z.write(f, f.name)
        # 轮转出来的历史日志也带上，问题可能发生在上一次运行
        d = log_dir()
        for f in sorted(d.glob("windowkeeper.log.*")):
            z.write(f, f.name)
        for f in sorted(d.glob("windowkeeper.jsonl.*")):
            z.write(f, f.name)
    return out


def tail(n: int = 200) -> list[str]:
    """读最后 n 行人类可读日志。给界面上的「查看日志」用。"""
    f = log_file()
    if not f.exists():
        return []
    try:
        with f.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.readlines()[-n:]
    except Exception:  # noqa: BLE001
        return []


def clear() -> None:
    """
    清空日志（轮转出来的也一起清）。

    ★ 必须先把 handler 关掉再删文件。原因：`RotatingFileHandler` 一旦
      打开过文件就长期持有句柄，Windows 上直接 unlink 会失败（文件被占用），
      而 POSIX 式的「删掉但句柄还在」会让后续写入落进一个已经不可见的文件——
      表现为「点了清空之后，日志再也不更新」。先 close 再删，再重开。
    """
    logger = logging.getLogger(_LOGGER_NAME)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:  # noqa: BLE001
            pass
    # 清掉已关闭的 handler，避免 _configured 状态下残留死 handler
    logger.handlers.clear()

    d = log_dir()
    for pat in ("windowkeeper.log*", "windowkeeper.jsonl*"):
        for f in d.glob(pat):
            for attempt in range(3):
                try:
                    f.unlink()
                    break
                except FileNotFoundError:
                    break
                except PermissionError:
                    # Windows 上句柄释放有延迟，稍等再试
                    import time as _t
                    _t.sleep(0.05)
                except Exception:  # noqa: BLE001
                    break

    # 重新挂一套干净的 handler，让 clear() 之后日志照常写
    global _configured
    _configured = False
    setup()
