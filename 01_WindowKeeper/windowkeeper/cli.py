# -*- coding: utf-8 -*-
"""
WindowKeeper 命令行接口。

设计意图：界面上能做的事，命令行都能做。
这样用户可以先把它接进自己的脚本/快捷方式，不必等界面做完。
也让整套逻辑在没有 Qt 的环境下可测。

用法示例：
    wk list                          列出所有预设
    wk windows                       列出当前可用窗口
    wk save 工作                     把当前布局存为「工作」
    wk apply 工作                    还原「工作」
    wk apply 工作 --dry-run          只看会怎么动，不真动
    wk show 工作                     看某个预设的内容
    wk delete 工作                   删除
    wk rename 工作 上班              改名
    wk export 布局.json              导出全部预设
    wk import 布局.json              导入
    wk monitors                      列出显示器
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from . import engine
from . import log
from . import store
from . import win32_api as w32


# --------------------------------------------------------------------------
# 输出辅助
# --------------------------------------------------------------------------

def _out(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("utf-8", "replace").decode("utf-8", "replace"))


def _setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


# 不同结果用不同前缀，控制台也能一眼看出区别
_MARK = {
    "ok": "[OK]   ",
    "constrained": "[LIMIT]",
    "acceptable": "[WARN] ",
    "not_found": "[MISS] ",
    "refused": "[DENY] ",
    "error": "[FAIL] ",
}


# --------------------------------------------------------------------------
# 各子命令
# --------------------------------------------------------------------------

def cmd_list(args) -> int:
    st = store.PresetStore()
    presets = st.load()
    if st.load_error:
        _out(f"注意：{st.load_error}")
    if not presets:
        _out("还没有任何预设。")
        _out("")
        _out("先摆好窗口，然后执行：")
        _out("    wk save 我的布局")
        return 0

    _out(f"共 {len(presets)} 个预设")
    _out("")
    _out(f"  {'序号':<6}{'名称':<22}{'窗口数':<8}{'热键':<12}{'显示器'}")
    _out("  " + "-" * 68)
    for i, p in enumerate(presets, 1):
        hotkey = p.hotkey or "—"
        nmon = len(p.monitor_snapshot)
        _out(f"  {i:<6}{p.name[:21]:<22}{p.window_count:<8}{hotkey:<12}{nmon} 块")
    _out("")
    _out("还原：wk apply <名称>")
    return 0


def cmd_windows(args) -> int:
    mons = w32.enum_monitors()
    from .matcher import enumerate_live_windows
    live = enumerate_live_windows(mons, include_minimized=args.all)
    if not live:
        _out("没有找到可用窗口。")
        return 0

    _out(f"共 {len(live)} 个可用窗口")
    _out("")
    _out(f"  {'进程':<24}{'屏':<5}{'位置':<18}{'尺寸':<14}标题")
    _out("  " + "-" * 88)
    for lw in sorted(live, key=lambda w: (w.monitor_index, w.exe_name)):
        pos = f"{lw.rect[0]},{lw.rect[1]}"
        size = f"{lw.width}x{lw.height}"
        flag = " [最小化]" if lw.minimized else ""
        _out(f"  {lw.exe_name[:23]:<24}[{lw.monitor_index}]{'':<3}"
             f"{pos:<18}{size:<14}{lw.title[:34]}{flag}")
    return 0


def cmd_monitors(args) -> int:
    mons = w32.enum_monitors()
    _out(f"共 {len(mons)} 块显示器")
    _out("")
    _out(f"  {'序号':<6}{'设备名':<18}{'分辨率':<16}{'缩放':<8}{'主屏':<8}工作区")
    _out("  " + "-" * 84)
    for m in mons:
        res = f"{m.width}x{m.height}"
        scale = f"{m.scale * 100:.0f}%"
        pri = "是" if m.primary else ""
        _out(f"  {m.index:<6}{m.name:<18}{res:<16}{scale:<8}{pri:<8}{m.work}")
    return 0


def cmd_save(args) -> int:
    name = args.name
    st = store.PresetStore()
    presets = st.load()

    refs = engine.capture_current_layout(include_minimized=args.all)

    # 允许用 --only 只挑一部分进程
    if args.only:
        want = {s.lower() for s in args.only}
        refs = [r for r in refs if r.exe_name.lower() in want]
        _out(f"按 --only 过滤后剩 {len(refs)} 个窗口")

    if not refs:
        _out("没有捕获到任何窗口。")
        _out("如果窗口是最小化的，加 --all 试试。")
        return 1

    _out(f"捕获到 {len(refs)} 个窗口：")
    for r in refs:
        _out(f"  · {r.exe_name:<24}({r.x},{r.y}) {r.w}x{r.h}  @ {r.monitor_name}")

    # 同名则覆盖（这是用户最可能的意图：更新现有布局）
    existing = next((p for p in presets if p.name == name), None)
    preset = store.Preset(
        name=name,
        windows=refs,
        monitor_snapshot=store.snapshot_monitors(),
        hotkey=existing.hotkey if existing else "",
    )

    if existing:
        if not args.force:
            _out("")
            _out(f"「{name}」已存在（{existing.window_count} 个窗口）。")
            _out("加 --force 覆盖它。")
            return 1
        # ★ 必须沿用旧 id 与创建时间：update() 是按 id 定位的，
        #   否则会找不到旧记录、退化成新增，预设数量悄悄变多。
        preset.id = existing.id
        preset.created_at = existing.created_at
        preset.notes = existing.notes
        st.update(preset)          # update 内部已 save
        _out("")
        _out(f"已覆盖预设「{name}」。")
    else:
        st.add(preset)             # add 内部已 save
        _out("")
        _out(f"已保存预设「{name}」。")

    _out(f"文件：{st.path}")
    return 0


def _print_report(report: engine.ApplyReport) -> None:
    _out(f"  {report.summary()}    耗时 {report.elapsed * 1000:.0f} 毫秒")
    if report.monitor_warning:
        _out(f"  注意：{report.monitor_warning}")
    _out("")
    for o in report.outcomes:
        mark = _MARK.get(o.kind.value, "      ")
        name = o.ref.title_fp or o.ref.exe_name or "(未知)"
        _out(f"  {mark} {name[:36]:<38}{o.status_text}")
        if o.message:
            _out(f"          {o.message}")
        if o.min_track[0] and o.kind.value == "constrained":
            _out(f"          该程序自报最小尺寸 {o.min_track[0]}x{o.min_track[1]}")


def cmd_apply(args) -> int:
    st = store.PresetStore()
    presets = st.load()
    if not presets:
        _out("还没有任何预设。先用 wk save 保存一个。")
        return 1

    # 允许用序号，也允许用名称
    target = None
    if args.name.isdigit():
        idx = int(args.name) - 1
        if 0 <= idx < len(presets):
            target = presets[idx]
    if target is None:
        target = next((p for p in presets if p.name == args.name), None)
    if target is None:
        # 退一步：做前缀/包含匹配，省得用户打全名
        cands = [p for p in presets if args.name in p.name]
        if len(cands) == 1:
            target = cands[0]
        elif len(cands) > 1:
            _out(f"「{args.name}」匹配到多个预设：")
            for p in cands:
                _out(f"  · {p.name}")
            return 1

    if target is None:
        _out(f"找不到预设「{args.name}」。")
        _out("可用的有：")
        for p in presets:
            _out(f"  · {p.name}")
        return 1

    _out(f"预设：{target.name}（{target.window_count} 个窗口）")
    if args.dry_run:
        _out("模式：预演（不会真的移动窗口）")
    _out("")

    report = engine.apply_preset(
        target,
        activate=not args.no_activate,
        dry_run=args.dry_run,
    )
    _print_report(report)

    if report.failed:
        _out("")
        _out(f"有 {len(report.failed)} 个窗口没能还原。")
        return 2
    return 0


def cmd_show(args) -> int:
    st = store.PresetStore()
    presets = st.load()
    p = next((x for x in presets if x.name == args.name), None)
    if p is None:
        _out(f"找不到预设「{args.name}」。")
        return 1

    _out(f"预设：{p.name}")
    _out(f"热键：{p.hotkey or '(未设置)'}")
    _out(f"窗口：{p.window_count} 个")
    _out(f"保存时的显示器：")
    for m in p.monitor_snapshot:
        r = m.get("rect", [0, 0, 0, 0])
        _out(f"  · [{m.get('index')}] {m.get('name')} "
             f"{r[2] - r[0]}x{r[3] - r[1]} @{m.get('dpi')}dpi")
    _out("")
    _out(f"  {'进程':<24}{'位置':<18}{'尺寸':<14}标题")
    _out("  " + "-" * 88)
    for r in p.windows:
        _out(f"  {r.exe_name[:23]:<24}{f'{r.x},{r.y}':<18}"
             f"{f'{r.w}x{r.h}':<14}{r.title_fp[:34]}")
    return 0


def cmd_delete(args) -> int:
    st = store.PresetStore()
    if not st.load():
        _out("还没有任何预设。")
        return 1
    # remove() 收的是 id，这里要按名字找
    p = st.by_name(args.name)
    if p is None:
        _out(f"找不到预设「{args.name}」。")
        return 1
    st.remove(p.id)
    _out(f"已删除预设「{args.name}」。")
    return 0


def cmd_rename(args) -> int:
    st = store.PresetStore()
    if not st.load():
        _out("还没有任何预设。")
        return 1
    p = next((x for x in st.presets if x.name == args.old), None)
    if p is None:
        _out(f"找不到预设「{args.old}」。")
        return 1
    if any(x.name == args.new for x in st.presets):
        _out(f"已经有一个叫「{args.new}」的预设了。")
        return 1
    old = p.name
    p.name = args.new
    st.update(p)
    _out(f"「{old}」→「{args.new}」。")
    return 0


def cmd_hotkey(args) -> int:
    st = store.PresetStore()
    if not st.load():
        _out("还没有任何预设。")
        return 1
    p = next((x for x in st.presets if x.name == args.name), None)
    if p is None:
        _out(f"找不到预设「{args.name}」。")
        return 1
    p.hotkey = args.keys or ""
    st.update(p)
    if args.keys:
        _out(f"已把「{p.name}」的热键设为 {args.keys}。")
    else:
        _out(f"已清除「{p.name}」的热键。")
    return 0


def cmd_export(args) -> int:
    st = store.PresetStore()
    st.load()
    n = st.export_to(Path(args.path))
    if n == 0:
        _out("没有可导出的预设。")
        return 1
    _out(f"已导出 {n} 个预设到 {args.path}")
    return 0


def cmd_import(args) -> int:
    st = store.PresetStore()
    st.load()
    src = Path(args.path)
    if not src.exists():
        _out(f"文件不存在：{args.path}")
        return 1
    count, warnings = st.import_from(src)
    if count == 0:
        _out("没有导入任何预设。")
    else:
        _out(f"已导入 {count} 个预设。")
    for wmsg in warnings:
        _out(f"  · {wmsg}")
    if count:
        st.save()
    return 0


def cmd_tray(args) -> int:
    from .tray import run_tray
    return run_tray()


def cmd_ui(args) -> int:
    # 注意：run_ui 定义在 tray.py 里（它和 run_tray 共用 _prepare_app），
    # 不在 ui.py。写错过一次，源码模式下 CLI 的 ui 分支没人跑，打包后才炸。
    from .tray import run_ui
    return run_ui()


# --------------------------------------------------------------------------
# 参数解析
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wk",
        description="WindowKeeper —— 把窗口布局变成一次点击",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用示例：
  wk save 工作                 把当前窗口布局存成「工作」
  wk apply 工作                一键还原
  wk apply 工作 --dry-run      只看会怎么动
  wk list                      看看存了哪些
  wk windows                   看看现在有哪些窗口
  wk monitors                  看看显示器配置
""")

    ap.add_argument("--version", action="version",
                    version=f"WindowKeeper {__version__}")

    sub = ap.add_subparsers(dest="command", metavar="<命令>")

    p = sub.add_parser("list", aliases=["ls"], help="列出所有预设")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("windows", aliases=["win"], help="列出当前可用窗口")
    p.add_argument("-a", "--all", action="store_true", help="包含最小化的窗口")
    p.set_defaults(func=cmd_windows)

    p = sub.add_parser("monitors", aliases=["mon"], help="列出显示器")
    p.set_defaults(func=cmd_monitors)

    p = sub.add_parser("save", help="把当前布局保存为预设")
    p.add_argument("name", help="预设名称")
    p.add_argument("-a", "--all", action="store_true", help="包含最小化的窗口")
    p.add_argument("-f", "--force", action="store_true", help="同名时覆盖")
    p.add_argument("--only", nargs="+", metavar="进程名",
                   help="只捕获指定进程，如 --only msedge.exe notepad.exe")
    p.set_defaults(func=cmd_save)

    p = sub.add_parser("apply", help="还原一个预设")
    p.add_argument("name", help="预设名称或序号")
    p.add_argument("-n", "--dry-run", action="store_true", help="只预演，不真动")
    p.add_argument("--no-activate", action="store_true",
                   help="不把窗口带到前台")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("show", help="查看预设内容")
    p.add_argument("name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("delete", aliases=["rm"], help="删除预设")
    p.add_argument("name")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("rename", aliases=["mv"], help="重命名预设")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=cmd_rename)

    p = sub.add_parser("hotkey", help="设置某个预设的全局热键")
    p.add_argument("name")
    p.add_argument("keys", nargs="?", default="",
                   help="热键写法，如 ctrl+alt+1；留空则清除")
    p.set_defaults(func=cmd_hotkey)

    p = sub.add_parser("export", help="导出所有预设")
    p.add_argument("path")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", help="导入预设")
    p.add_argument("path")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("tray", help="启动托盘常驻（含热键）")
    p.set_defaults(func=cmd_tray)

    p = sub.add_parser("ui", aliases=["gui"], help="打开图形界面")
    p.set_defaults(func=cmd_ui)

    # 全局选项：--verbose 让日志实时打到控制台，方便当场排障。
    # 放在顶层，所以 `wk list -v` 和 `wk -v list` 都能用。
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="把日志实时打到控制台（同时照常写入日志文件）")

    return ap


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    # ★ 必须在任何窗口操作之前
    w32.enable_per_monitor_v2()

    ap = build_parser()
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    if not getattr(args, "command", None):
        ap.print_help()
        return 0

    # 命令行也记日志，但**不往控制台打**：控制台的输出是给用户看结果的，
    # 混进日志行会让人分不清哪句是结果、哪句是痕迹。要实时看就加 --verbose。
    verbose = bool(getattr(args, "verbose", False))
    log.setup(logging.DEBUG if verbose else logging.INFO, console=verbose)
    log.get("cli").info("执行命令", command=args.command,
                        argv=sys.argv[1:] if argv is None else argv)

    try:
        code = int(args.func(args))
        log.get("cli").info("命令结束", command=args.command, exit_code=code)
        return code
    except KeyboardInterrupt:
        _out("")
        _out("已取消。")
        log.get("cli").warning("命令被用户中断", command=args.command)
        return 130
    except Exception as exc:  # noqa: BLE001
        _out(f"出错了：{exc!r}")
        log.exception(f"命令执行出错：{args.command}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
