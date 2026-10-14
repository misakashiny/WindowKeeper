# -*- coding: utf-8 -*-
"""
阶段 0 探针 D：终极验证 —— 对比 DPI-UNAWARE 与 PER-MONITOR-V2 两种模式下
枚举结果与 SetWindowPos 行为，给出确定的结论。

这是整个阶段0最重要的一次实验：它直接决定了后续所有代码的坐标模型。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

# ---- 先决定模式，再碰任何窗口 ----
MODE = sys.argv[1] if len(sys.argv) > 1 else "unaware"

if MODE == "pmv2":
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
elif MODE == "system":
    shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
    shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE

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
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
shcore.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
shcore.GetProcessDpiAwareness.restype = ctypes.c_long
shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_uint),
                                    ctypes.POINTER(ctypes.c_uint)]
shcore.GetDpiForMonitor.restype = ctypes.c_long

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


def awareness_name() -> str:
    v = ctypes.c_int(-1)
    shcore.GetProcessDpiAwareness(None, ctypes.byref(v))
    return {0: "UNAWARE", 1: "SYSTEM", 2: "PER_MONITOR"}.get(v.value, f"?{v.value}")


def monitors() -> list[dict]:
    out = []

    def _cb(h, _a, _b, _c):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(h, ctypes.byref(mi)):
            dx, dy = ctypes.c_uint(96), ctypes.c_uint(96)
            shcore.GetDpiForMonitor(h, MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy))
            out.append({"name": mi.szDevice,
                        "rect": (mi.rcMonitor.left, mi.rcMonitor.top,
                                 mi.rcMonitor.right, mi.rcMonitor.bottom),
                        "work": (mi.rcWork.left, mi.rcWork.top,
                                 mi.rcWork.right, mi.rcWork.bottom),
                        "dpi": dx.value, "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY)})
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    return out


def find_notepad():
    hits = []

    def _cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cb, 256)
        if cb.value == "Notepad":
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, b, n + 1)
            if b.value.strip():
                hits.append(hwnd)
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

    print(f"模式: {MODE}   →  进程 DPI 感知 = {awareness_name()}")
    print()

    mons = monitors()
    print("  显示器（此模式下枚举到的坐标系）：")
    for i, m in enumerate(mons):
        l, t, r, b = m["rect"]
        print(f"    [{i}] {m['name']:<14} dpi={m['dpi']:<4} 矩形=({l},{t})-({r},{b})  {r-l}x{b-t}")

    notes = find_notepad()
    if not notes:
        print("  (无记事本窗口，跳过尺寸实测)")
        return 0
    hwnd = notes[0]

    print()
    print("  尺寸实测（请求 800x600，放到各屏正中）：")
    for i, m in enumerate(mons):
        l, t, r, b = m["rect"]
        x = l + ((r - l) - 800) // 2
        y = t + ((b - t) - 600) // 2
        if not user32.SetWindowPos(hwnd, None, x, y, 800, 600, SWP_NOZORDER | SWP_NOACTIVATE):
            print(f"    [{i}] 调用失败 err={ctypes.get_last_error()}")
            continue
        g = rect_of(hwnd)
        gw, gh = g[2] - g[0], g[3] - g[1]
        ok = "✅精确" if (gw, gh) == (800, 600) else f"❌被缩放 x{gw/800:.4f}"
        print(f"    [{i}] dpi={m['dpi']:<4} 请求800x600 → 实际{gw}x{gh}  {ok}")

    user32.SetWindowPos(hwnd, None, 1747, 174, 684, 914, SWP_NOZORDER | SWP_NOACTIVATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
