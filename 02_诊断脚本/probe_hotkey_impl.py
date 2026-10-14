# -*- coding: utf-8 -*-
"""
对比三种全局热键实现，找出在本机真正可靠的那一种。

候选：
  A. keyboard 库的后台线程监听           （当前实现）
  B. Win32 RegisterHotKey + Qt 原生事件过滤  （不依赖第三方库）
  C. keyboard 库但把监听线程做成 Qt 自己的线程

判据：能否收到「程序化注入」的组合键（可自动化验证），以及是否稳定。
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

# 本脚本在 02_诊断脚本\ 下，项目源码在 01_WindowKeeper\，必须显式加进 sys.path。
# 不能用 sys.path.insert(0, ".")：那只在「当前目录恰好是 01_WindowKeeper」时才对。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "01_WindowKeeper"))

user32 = ctypes.windll.user32
VK_CONTROL, VK_MENU, VK_1 = 0x11, 0x12, 0x31
UP = 0x0002


def inject():
    """注入 ctrl+alt+1。"""
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_1, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_1, 0, UP, 0)
    user32.keybd_event(VK_MENU, 0, UP, 0)
    user32.keybd_event(VK_CONTROL, 0, UP, 0)


# --------------------------------------------------------------------------
# 方案 B：RegisterHotKey + 消息循环（纯 Win32，无第三方依赖）
# --------------------------------------------------------------------------

def test_register_hotkey():
    print("=" * 60)
    print("方案 B：Win32 RegisterHotKey + 消息循环")
    print("=" * 60)

    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    HOTKEY_ID = 0xA001

    # RegisterHotKey 需要 NULL 或有效 hwnd；NULL 时消息进本线程队列
    ok = user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_1)
    if not ok:
        err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else "?"
        print(f"  RegisterHotKey 失败，GetLastError={ctypes.windll.kernel32.GetLastError()}")
        print("  ★ 可能被别的程序占用了 ctrl+alt+1")
        return False
    print("  RegisterHotKey 成功")

    inject()
    print("  已注入 ctrl+alt+1，等待消息…")

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND),
                    ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM),
                    ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD),
                    ("pt_x", wintypes.LONG),
                    ("pt_y", wintypes.LONG)]

    msg = MSG()
    got = False
    deadline = time.time() + 3.0
    while time.time() < deadline:
        # PM_REMOVE，非阻塞
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            if msg.message == WM_HOTKEY:
                print(f"  ★ 收到 WM_HOTKEY，wParam={msg.wParam}")
                got = True
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)

    user32.UnregisterHotKey(None, HOTKEY_ID)
    print(f"  结果: {'成功' if got else '未收到'}")
    return got


# --------------------------------------------------------------------------
# 方案 A：keyboard 库
# --------------------------------------------------------------------------

def test_keyboard_lib():
    print()
    print("=" * 60)
    print("方案 A：keyboard 库（当前实现）")
    print("=" * 60)
    import keyboard

    hits = []
    h = keyboard.add_hotkey("ctrl+alt+1", lambda: hits.append(1))
    inject()
    time.sleep(1.5)
    keyboard.remove_hotkey(h)
    print(f"  组合键回调次数: {len(hits)}")
    print(f"  结果: {'成功' if hits else '未收到'}")
    return bool(hits)


if __name__ == "__main__":
    b = test_register_hotkey()
    a = test_keyboard_lib()

    print()
    print("=" * 60)
    print("结论")
    print("=" * 60)
    print(f"  RegisterHotKey: {'可用' if b else '不可用'}")
    print(f"  keyboard 库:    {'可用' if a else '不可用'}")
    if b and not a:
        print("\n  → 应该改用 RegisterHotKey。它是 Win32 原生 API，")
        print("    不需要额外线程泵消息，也不依赖第三方库。")
    elif b and a:
        print("\n  → 两者都可用。RegisterHotKey 依赖更少，优先选它。")
    else:
        print("\n  → 都不可用，需要另想办法。")
