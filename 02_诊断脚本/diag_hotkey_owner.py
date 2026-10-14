# -*- coding: utf-8 -*-
"""
诊断：托盘进程到底有没有注册上热键？

思路：新开一个进程尝试注册同一个热键 ctrl+alt+1。
      如果注册失败且错误码 1409（已被占用），说明托盘进程确实占着它。
      如果注册成功，说明托盘那边根本没注册上。
"""
import ctypes
import time

user32 = ctypes.windll.user32
MOD_CONTROL, MOD_ALT, MOD_NOREPEAT = 0x0002, 0x0001, 0x4000
VK_1 = 0x31

print("测试 1：ctrl+alt+1 是否已被别的进程占用")
ok = user32.RegisterHotKey(None, 0xB001, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_1)
if ok:
    print("  ★ 注册成功 —— 说明托盘进程【没有】占着这个热键")
    user32.UnregisterHotKey(None, 0xB001)
else:
    err = ctypes.windll.kernel32.GetLastError()
    print(f"  注册失败，错误码 {err}", end="  ")
    if err == 1409:
        print("(1409 = 已被占用 → 托盘进程确实占用着它)")
    else:
        print("(不是 1409，另有原因)")

print()
print("测试 2：换一个肯定没人用的热键，验证本进程机制正常")
ok2 = user32.RegisterHotKey(None, 0xB002, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, 0x7A)  # F11
print(f"  ctrl+alt+F11 注册: {'成功' if ok2 else '失败'}")
if ok2:
    user32.UnregisterHotKey(None, 0xB002)
