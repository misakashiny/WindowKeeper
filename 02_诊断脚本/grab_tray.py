# -*- coding: utf-8 -*-
"""
截取任务栏托盘区域，确认 WindowKeeper 图标真的显示出来了。
只用于验证，不属于产品代码。
"""
import sys

import win32gui
import win32ui
import win32con
from ctypes import windll

from PIL import Image


def find_tray_rect():
    """托盘通知区域是 Shell_TrayWnd -> TrayNotifyWnd 的子窗口。"""
    tray = win32gui.FindWindow("Shell_TrayWnd", None)
    if not tray:
        return None
    notify = win32gui.FindWindowEx(tray, 0, "TrayNotifyWnd", None)
    if notify:
        return win32gui.GetWindowRect(notify)
    return win32gui.GetWindowRect(tray)


def grab(rect, path):
    l, t, r, b = rect
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(0)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    # 从屏幕左上角为原点抓取指定区域
    save_dc.BitBlt((0, 0), (w, h), mfc_dc, (l, t), win32con.SRCCOPY)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bits, "raw", "BGRX", 0, 1)
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(0, hwnd_dc)
    return w, h


if __name__ == "__main__":
    windll.user32.SetProcessDPIAware()
    rect = find_tray_rect()
    print("托盘区域:", rect)
    if not rect:
        sys.exit("没找到任务栏")
    out = sys.argv[1] if len(sys.argv) > 1 else "tray.png"
    w, h = grab(rect, out)
    print(f"已保存 {out}  尺寸 {w}x{h}")
