# -*- coding: utf-8 -*-
"""确认 MSG 结构在 64 位下的真实字段偏移，为 nativeEventFilter 的写法提供依据。"""
import ctypes
from ctypes import wintypes

print("wintypes.MSG 字段偏移：")
for name, _ in wintypes.MSG._fields_:
    print(f"  {name:10} offset={getattr(wintypes.MSG, name).offset}")

print()
print("sizeof(HWND) =", ctypes.sizeof(wintypes.HWND))
print("sizeof(UINT) =", ctypes.sizeof(wintypes.UINT))
naive_msg = ctypes.sizeof(wintypes.HWND)
naive_wp = naive_msg + ctypes.sizeof(wintypes.UINT)
real_msg = getattr(wintypes.MSG, "message").offset
real_wp = getattr(wintypes.MSG, "wParam").offset
print(f"手算 message 偏移 = {naive_msg}   实际 = {real_msg}")
print(f"手算 wParam  偏移 = {naive_wp}   实际 = {real_wp}"
      f"   {'★ 不一致，手算会读错' if naive_wp != real_wp else '一致'}")
print()
print("sizeof(MSG) =", ctypes.sizeof(wintypes.MSG))
