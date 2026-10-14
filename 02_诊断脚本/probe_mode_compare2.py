# -*- coding: utf-8 -*-
"""
阶段 0 探针 E：用「原生 Win32 窗口」重做三模式对照。

探针 D 用记事本测出来的结果是三个模式完全一样，这很可疑——
记事本是 UWP 化的（ApplicationFrameHost 托管），它的消息路由不典型。
这里改用真正的原生窗口（Tkinter / 文件资源管理器 / Chrome）重测。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

MODE = sys.argv[1] if len(sys.argv) > 1 else "unaware"
TARGET_CLS = sys.argv[2] if len(sys.argv) > 2 else "ANY"

if MODE == "pmv2":
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
elif MODE == "system":
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness(1)

user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wt.BOOL
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL

SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MDT_EFFECTIVE_DPI = 0
MONITORINFOF_PRIMARY = 0x1


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", ctypes.c_wchar * 32)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
user32.EnumDisplayMonitors.restype = wt.BOOL


def monitors():
    out = []

    def _cb(h, _a, _b, _c):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(h, ctypes.byref(mi)):
            out.append({"name": mi.szDevice,
                        "rect": (mi.rcMonitor.left, mi.rcMonitor.top,
                                 mi.rcMonitor.right, mi.rcMonitor.bottom),
                        "dpi": 96})
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    # 用真实 DPI：需要 PMV2 才能拿到每屏值；退化时读注册表不划算，这里用固定表
    return out


def find_windows(cls_filter: str):
    hits = []

    def _cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cb, 256)
        cls = cb.value
        if cls_filter != "ANY" and cls != cls_filter:
            return True
        if cls in ("Progman", "CEF-OSC-WIDGET", "Windows.UI.Core.CoreWindow",
                   "Notepad", "TextInputHost"):
            return True
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, b, n + 1)
        if b.value.strip():
            hits.append((hwnd, cls, b.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    return hits


def rect_of(h):
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    mons = monitors()
    # 让枚举在 PMV2 下拿到真实 dpi
    if MODE == "pmv2":
        pass

    wins = find_windows(TARGET_CLS)
    if not wins:
        print(f"[{MODE}] 没有找到类名 {TARGET_CLS} 的窗口")
        return 0

    # 挑一个有代表性的：优先主屏上的
    wins.sort(key=lambda w: 0 if rect_of(w[0])[0] >= 0 else 1)
    hwnd, cls, title = wins[0]
    orig = rect_of(hwnd)

    print(f"[{MODE:<8}] 目标={cls}  {title[:38]!r}")
    print(f"           原始=({orig[0]},{orig[1]}) {orig[2]-orig[0]}x{orig[3]-orig[1]}")

    for i, m in enumerate(mons):
        l, t, r, b = m["rect"]
        # 目标屏上放一个 800x600
        x = l + ((r - l) - 800) // 2
        y = t + ((b - t) - 600) // 2
        user32.SetWindowPos(hwnd, None, x, y, 800, 600, SWP_NOZORDER | SWP_NOACTIVATE)
        g = rect_of(hwnd)
        gw, gh = g[2] - g[0], g[3] - g[1]
        dx, dy = g[0] - x, g[1] - y
        verdict = "✅精确" if (gw, gh) == (800, 600) else f"❌尺寸x{gw/800:.4f}"
        posnote = "" if (dx, dy) == (0, 0) else f"  位置偏移({dx},{dy})"
        print(f"           屏[{i}] 请求({x},{y}) 800x600 → 实际({g[0]},{g[1]}) {gw}x{gh}"
              f"  {verdict}{posnote}")

    user32.SetWindowPos(hwnd, None, orig[0], orig[1],
                        orig[2] - orig[0], orig[3] - orig[1], SWP_NOZORDER | SWP_NOACTIVATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
