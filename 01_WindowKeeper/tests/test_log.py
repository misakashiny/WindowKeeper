# -*- coding: utf-8 -*-
"""
T13：日志模块。

这个测试的重点**不是**「日志能写出来」，而是把踩过的坑固化下来，
防止以后改 log.py 时又踩回去。每一条断言对应一个真实发生过的故障。

运行：python tests\test_log.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from windowkeeper import log  # noqa: E402

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}  {detail}")
        print(f"  [FAIL] {name}  {detail}")


def section(title: str) -> None:
    print(f"\n{title}")


def read_human() -> str:
    """读人类可读日志（就是用户会发回来的那一份）。"""
    f = log.log_file()
    if not f.exists():
        return ""
    # handler 有缓冲，先 flush 才能读到
    for h in logging.getLogger("windowkeeper").handlers:
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass
    return f.read_text(encoding="utf-8", errors="replace")


def read_jsonl() -> str:
    f = log.jsonl_file()
    if not f.exists():
        return ""
    for h in logging.getLogger("windowkeeper").handlers:
        try:
            h.flush()
        except Exception:  # noqa: BLE001
            pass
    return f.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# T13.1 ★ 级别参数必须同时接受 str 和 int
#   曾经的故障：setup() 签名写 str，内部却做 level.upper()，
#   CLI/托盘传 logging.INFO（int）直接炸
#   AttributeError("'int' object has no attribute 'upper'")，
#   而且被 try 吞掉，只表现为「日志文件建不出来」。
# --------------------------------------------------------------------------
section("T13.1 级别参数：str 与 int 都要接受")
for lv, label in ((logging.INFO, "int INFO"), (logging.DEBUG, "int DEBUG"),
                  ("INFO", "str INFO"), ("debug", "小写 debug"),
                  ("WARNING", "str WARNING")):
    try:
        got = log._resolve_level(lv)
        check(f"_resolve_level({label}) 不抛异常且返回 int",
              isinstance(got, int), f"得到 {got!r}")
    except Exception as exc:  # noqa: BLE001
        check(f"_resolve_level({label}) 不抛异常", False, repr(exc))

check("未知级别字符串退化为 INFO",
      log._resolve_level("不存在的级别") == logging.INFO)
check("None 退化为 INFO", log._resolve_level(None) == logging.INFO)

# 真实调用路径：传 int 级别不能崩
try:
    log.setup(logging.DEBUG)
    check("setup(logging.DEBUG) 可正常调用（int 级别）", True)
except Exception as exc:  # noqa: BLE001
    check("setup(logging.DEBUG) 可正常调用（int 级别）", False, repr(exc))


# --------------------------------------------------------------------------
# T13.2 ★ 结构化字段必须同时进「人类可读」和「JSONL」两份
#   曾经的故障：_HumanFormatter 没读 record.wk，
#   字段只进了 JSONL。而用户发回来的恰恰是人类可读那份，
#   于是排障时最关键的信息反而看不到。
# --------------------------------------------------------------------------
section("T13.2 结构化字段两份都要有")
log.clear()
lg = log.get("test")
lg.info("字段检查", preset="工作", windows=8, dry_run=True)

human = read_human()
jsonl = read_jsonl()

for field in ("preset=工作", "windows=8", "dry_run=True"):
    check(f"人类可读日志含 {field}", field in human, human.strip()[:120])

for key in ('"preset": "工作"', '"windows": 8', '"dry_run": true'):
    check(f"JSONL 含 {key}", key in jsonl, jsonl.strip()[:120])

check("人类可读行以时间戳开头",
      bool(human) and human[0].isdigit() and ":" in human[:3], human[:40])


# --------------------------------------------------------------------------
# T13.3 ★ 占位符与字段必须能混用
#   契约：%-占位符用位置参数，想记的字段用关键字参数。
#   这条最容易写错，写错了要么 TypeError，要么字段静默丢失。
# --------------------------------------------------------------------------
section("T13.3 占位符 + 字段混用")
log.clear()
lg.info("枚举到 %d 个窗口，完成 %d 项匹配", 9, 8, monitors=3, elapsed_ms=5)
human = read_human()
check("占位符被正确替换（9 / 8）", "枚举到 9 个窗口，完成 8 项匹配" in human,
      human.strip()[:120])
check("字段 monitors 仍在（未被当成格式化参数）", "monitors=3" in human,
      human.strip()[:120])
check("字段 elapsed_ms 仍在", "elapsed_ms=5" in human, human.strip()[:120])
check("消息里不留 %d 原始占位符", "%d" not in human, human.strip()[:120])


# --------------------------------------------------------------------------
# T13.4 自定义 DECISION 级别
# --------------------------------------------------------------------------
section("T13.4 DECISION 级别")
check("DECISION == 25", log.DECISION == 25, str(log.DECISION))
check("级别名已注册为 DECISION",
      logging.getLevelName(log.DECISION) == "DECISION",
      logging.getLevelName(log.DECISION))
log.clear()
lg.decision("还原决定", exe="notepad.exe", score=100)
human = read_human()
check("decision() 写进了日志", "还原决定" in human, human.strip()[:120])
check("decision() 带上了字段", "exe=notepad.exe" in human and "score=100" in human,
      human.strip()[:120])
check("DECISION 显示为中文「决定」", "决定" in human, human.strip()[:120])


# --------------------------------------------------------------------------
# T13.5 swallow()：吞掉异常但留下痕迹
# --------------------------------------------------------------------------
section("T13.5 swallow 记录被吞的异常")
log.clear()
log.swallow("查询窗口最小尺寸约束", ValueError("boom"), hwnd=12345, exe="msedge.exe")
human = read_human()
check("记下了哪个动作失败", "查询窗口最小尺寸约束" in human, human.strip()[:150])
check("记下了异常内容", "ValueError" in human and "boom" in human,
      human.strip()[:150])
check("记下了上下文 hwnd", "hwnd=12345" in human, human.strip()[:150])
check("级别是警告", "警告" in human, human.strip()[:150])


# --------------------------------------------------------------------------
# T13.6 exception()：含堆栈
# --------------------------------------------------------------------------
section("T13.6 exception 带堆栈")
log.clear()


def _boom() -> None:
    raise RuntimeError("故意炸一下")


try:
    _boom()
except RuntimeError:
    log.exception("还原预设时发生未预期错误", preset="测试布局")

human = read_human()
check("写下了消息", "还原预设时发生未预期错误" in human, human.strip()[:150])
check("带上了堆栈（Traceback）", "Traceback" in human, human.strip()[:200])
check("堆栈里能看到函数名 _boom", "_boom" in human, human.strip()[:200])
check("字段 preset 仍在", "preset=测试布局" in human, human.strip()[:200])


# --------------------------------------------------------------------------
# T13.7 ★ 幂等：重复 setup() 不能重复挂 handler
#   曾经的隐患：_configured 在异常时不会置位，
#   修好级别转换之后每次调用都会再挂一组 handler，
#   同一条日志就会被写 N 遍。
# --------------------------------------------------------------------------
section("T13.7 setup 幂等性")
logger = logging.getLogger("windowkeeper")
n_before = len(logger.handlers)
log.setup(logging.INFO)
log.setup("DEBUG")
log.setup(logging.WARNING)
n_after = len(logger.handlers)
check("重复 setup() 不增加 handler 数量", n_before == n_after,
      f"{n_before} -> {n_after}")

log.clear()
lg.info("只应出现一次")
human = read_human()
check("同一条日志只写一遍", human.count("只应出现一次") == 1,
      f"出现 {human.count('只应出现一次')} 次")


# --------------------------------------------------------------------------
# T13.8 敏感性与健壮性
# --------------------------------------------------------------------------
section("T13.8 健壮性")
check("log_dir() 存在", log.log_dir().exists())
check("log_file() 在 log_dir() 下",
      log.log_file().parent == log.log_dir())

# 长字段要截断，不能让一行日志爆掉
log.clear()
lg.info("长字段", blob="x" * 500)
human = read_human()
check("超长字段被截断（行不会失控）", len(human) < 400,
      f"行长 {len(human)}")
check("截断处有省略号", "…" in human, human[:120])

# 空字段、None 字段不该渲染成空壳
log.clear()
lg.info("空字段检查", empty="", none_val=None, real="ok")
human = read_human()
check("空串字段被跳过", "empty=" not in human, human.strip()[:150])
check("None 字段被跳过", "none_val=" not in human, human.strip()[:150])
check("正常字段保留", "real=ok" in human, human.strip()[:150])

# 没有 % 占位符时传位置参数，logging 自己会处理，不能崩
try:
    lg.info("没有占位符", 1, 2, 3)
    check("无占位符时传位置参数不崩", True)
except Exception as exc:  # noqa: BLE001
    check("无占位符时传位置参数不崩", False, repr(exc))

check("tail() 返回列表", isinstance(log.tail(10), list))
check("tail() 能读到刚写的内容",
      any("空字段检查" in ln for ln in log.tail(50)))


# --------------------------------------------------------------------------
# T13.9 诊断包
# --------------------------------------------------------------------------
section("T13.9 诊断包")
log.clear()
lg.info("诊断包测试", preset="测试布局")
try:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        out = log.make_diagnostic_zip(Path(td) / "diag.zip")
        check("生成了 zip", out.exists() and out.stat().st_size > 0,
              f"{out} size={out.stat().st_size if out.exists() else 0}")
        import zipfile
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            check("含 诊断报告.txt", "诊断报告.txt" in names, str(names))
            check("含人类可读日志", "windowkeeper.log" in names, str(names))
            report = z.read("诊断报告.txt").decode("utf-8")
            check("报告含版本", "版本" in report)
            # 这里断言两行**都**要在，而不是只断言其中一行：
            #   上下文行 = 能区分 V1/V2 的准确值（排障要靠它）
            #   旧API行  = 只有三档，留作交叉校验
            # 曾经因为「2 被误读成 V2」而在这台机器上制造过一次假警报。
            check("报告含 DPI 感知上下文（能区分 V1/V2）",
                  "DPI 感知上下文" in report, report[:400])
            check("报告含 DPI 感知旧API值（交叉校验用）",
                  "DPI 感知(旧API)" in report, report[:400])
            check("报告含显示器清单", "显示器数量" in report, report[:200])
            check("报告含预设摘要", "预设数量" in report, report[:200])
except Exception as exc:  # noqa: BLE001
    check("生成诊断包不抛异常", False, repr(exc))


# --------------------------------------------------------------------------
section("结果")
print(f"\n通过 {PASS} / 共 {PASS + FAIL}")
if FAILURES:
    print("\n失败项：")
    for f in FAILURES:
        print(f"  · {f}")
    sys.exit(1)
print("全部通过。")
