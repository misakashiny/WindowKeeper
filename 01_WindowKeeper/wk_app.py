# -*- coding: utf-8 -*-
"""
打包后的入口。

同一个 exe 有两种行为：
    WindowKeeper.exe            双击 → 无参数 → 打开托盘（最常用的用法）
    WindowKeeper.exe apply 工作 带参数 → 当命令行用，走 CLI

为什么要合并而不是打两个 exe：
    两个 exe 要各自拖一份 PySide6，加起来 200MB+。
    合并后只有一份，而且用户不需要记住"哪个是哪个"。
    命令行版 wk.exe 也仍然保留，方便在脚本里调用。
"""

import sys
from pathlib import Path

# 打包成单文件时，sys._MEIPASS 是解压出来的临时目录；
# 源码运行时就是本文件所在目录。
if getattr(sys, "frozen", False):
    _ROOT = Path(sys._MEIPASS)  # noqa: SLF001
else:
    _ROOT = Path(__file__).resolve().parent

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    from windowkeeper import win32_api as w32

    # 必须在任何窗口枚举 / 坐标读取之前声明 DPI 感知，
    # 否则在多屏不同缩放的机器上拿到的全是虚拟化后的错误坐标。
    w32.enable_per_monitor_v2()

    argv = sys.argv[1:]

    # 无参数 → 托盘（双击 exe 就是这种情况）
    if not argv:
        from windowkeeper.tray import run_tray
        return run_tray()

    # 明确的 CLI 子命令 → 走命令行
    cli_words = {
        "list", "ls", "windows", "win", "monitors", "mon", "save", "apply",
        "show", "delete", "rm", "rename", "mv", "hotkey", "export", "import",
        "tray", "ui", "gui",
    }
    if argv[0] in cli_words:
        from windowkeeper.cli import main as cli_main
        return cli_main()

    # 认不出来的参数：是 -h/--help 就给 CLI 的帮助，否则当 CLI 用让它报错，
    # 这样比默默开托盘更容易发现用法错误。
    from windowkeeper.cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
