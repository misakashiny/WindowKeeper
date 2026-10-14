# -*- coding: utf-8 -*-
"""
验证全链路：把窗口挪走 → 发送全局热键 ctrl+alt+1 → 检查窗口是否被拉回。

这是「不用打开界面就能用」这个承诺的最终验证。
发送真实按键事件（keybd_event），而不是直接调 apply_preset，
这样连 keyboard 库的钩子也一并验证了。
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

# 本脚本在 02_诊断脚本\ 下，项目源码在 01_WindowKeeper\，必须显式加进 sys.path。
# 不能用 sys.path.insert(0, ".")：那只在「当前目录恰好是 01_WindowKeeper」时才对。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "01_WindowKeeper"))

from windowkeeper import win32_api as w32
from windowkeeper.matcher import enumerate_live_windows

user32 = ctypes.windll.user32

VK_CONTROL = 0x11
VK_MENU = 0x12      # Alt
VK_1 = 0x31
KEYEVENTF_KEYUP = 0x0002


def press_combo():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_1, 0, 0, 0)
    time.sleep(0.08)
    user32.keybd_event(VK_1, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def find(exe):
    mons = w32.enum_monitors()
    for w in enumerate_live_windows(mons):
        if w.exe_name.lower() == exe.lower():
            return w
    return None


def main():
    w32.enable_per_monitor_v2()

    w = find("weixin.exe")
    if not w:
        print("没找到微信窗口，无法测试")
        return 1

    orig = w32.get_window_rect(w.hwnd)
    print(f"微信原始位置: {orig}")

    # 挪到一个明显不对的位置
    w32.apply_geometry(w.hwnd, 300, 900, 800, 400, w32.enum_monitors())
    time.sleep(0.4)
    moved = w32.get_window_rect(w.hwnd)
    print(f"已挪到:       {moved}")

    if moved[:2] == orig[:2]:
        print("挪动失败，测试无效")
        return 1

    print("发送热键 ctrl+alt+1 …")
    press_combo()
    time.sleep(2.5)          # 给托盘程序轮询+还原留时间

    after = w32.get_window_rect(w.hwnd)
    print(f"热键之后:     {after}")
    print(f"期望:         {orig}")

    if after == orig:
        print("\n[OK] 热键触发还原成功，位置尺寸完全一致")
        return 0
    if after[:2] == orig[:2]:
        print(f"\n[OK] 位置已还原（尺寸 {after[2]}x{after[3]} vs {orig[2]}x{orig[3]}）")
        return 0
    print("\n[FAIL] 窗口没有被拉回")
    return 1


if __name__ == "__main__":
    sys.exit(main())
