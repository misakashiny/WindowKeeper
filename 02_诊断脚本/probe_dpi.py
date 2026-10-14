# -*- coding: utf-8 -*-
"""
阶段 0 探针 B：多显示器 / DPI 实测。

回答三个问题：
  1. 本程序拿到的坐标是「物理像素」还是「逻辑像素」？（进程 DPI 感知级别的影响）
  2. 能不能把窗口精确移动到 2 号、3 号屏的指定位置？（含负坐标）
  3. 各显示器的缩放率是多少？混合缩放下坐标会不会漂？

零依赖，纯 ctypes。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

# ---- DPI 感知级别 ----
DPI_AWARE_INVALID = -1
DPI_AWARE_UNAWARE = 0
DPI_AWARE_SYSTEM = 1
DPI_AWARE_PER_MONITOR = 2

get_awareness = getattr(user32, "GetAwarenessFromDpiAwarenessContext", None)

shcore.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
shcore.GetProcessDpiAwareness.restype = ctypes.c_long
shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                                     ctypes.POINTER(ctypes.c_uint)]
shcore.GetDpiForMonitor.restype = ctypes.c_long

MDT_EFFECTIVE_DPI = 0

MONITORINFOF_PRIMARY = 0x1


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
)
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
user32.EnumDisplayMonitors.restype = wt.BOOL

user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wt.BOOL

user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
DPI_CTX_PMV2 = ctypes.c_void_p(-4)
DPI_CTX_SYSTEM = ctypes.c_void_p(-2)


def current_awareness() -> str:
    """报告当前进程的 DPI 感知级别。"""
    try:
        val = ctypes.c_int(DPI_AWARE_INVALID)
        hr = shcore.GetProcessDpiAwareness(None, ctypes.byref(val))
        if hr == 0:
            return {
                0: "UNAWARE (未感知，坐标会被系统缩放!)",
                1: "SYSTEM (系统级，单一缩放率)",
                2: "PER_MONITOR (每屏独立缩放，推荐)",
            }.get(val.value, f"未知({val.value})")
    except Exception as exc:  # noqa: BLE001
        return f"查询失败: {exc!r}"
    return "查询失败"


def set_per_monitor_v2() -> bool:
    """在创建任何窗口之前调用，让进程进入 Per-Monitor V2 DPI 感知。"""
    try:
        return bool(user32.SetProcessDpiAwarenessContext(DPI_CTX_PMV2))
    except Exception:  # noqa: BLE001
        return False


def enum_monitors() -> list[dict]:
    out: list[dict] = []

    def _cb(hmon, _hdc, _lprc, _lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dpi_x = ctypes.c_uint(96)
            dpi_y = ctypes.c_uint(96)
            hr = shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI,
                                         ctypes.byref(dpi_x), ctypes.byref(dpi_y))
            r = mi.rcMonitor
            w = mi.rcWork
            out.append({
                "handle": hmon,
                "name": mi.szDevice,
                "rect": (r.left, r.top, r.right, r.bottom),
                "work": (w.left, w.top, w.right, w.bottom),
                "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY),
                "dpi": dpi_x.value if hr == 0 else -1,
                "scale": round(dpi_x.value / 96.0, 4) if hr == 0 else -1.0,
            })
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
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cb, 256)
        if cb.value == "Notepad" and buf.value.strip():
            hits.append((hwnd, buf.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    return hits


def rect_of(hwnd) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    print("=" * 84)
    print("【1】进程 DPI 感知级别")
    print("=" * 84)
    print(f"  设定前: {current_awareness()}")
    ok = set_per_monitor_v2()
    print(f"  SetProcessDpiAwarenessContext(PER_MONITOR_V2) -> {ok}")
    print(f"  设定后: {current_awareness()}")
    print()
    print("  ⚠️  这必须在创建任何窗口之前调用，否则无效。")
    print("     探针进程没有窗口，所以这里能改成功；")
    print("     Tkinter 程序要先调再建 root，Qt 要在 QApplication 之前调。")

    print()
    print("=" * 84)
    print("【2】显示器清单（含各自缩放率）")
    print("=" * 84)
    monitors = enum_monitors()
    for i, m in enumerate(monitors):
        l, t, r, b = m["rect"]
        wl, wt_, wr, wb = m["work"]
        print(f"  [{i}] {m['name']}{'  <主屏>' if m['primary'] else ''}")
        print(f"      完整区域: ({l},{t}) - ({r},{b})   {r - l}x{b - t}")
        print(f"      工作区域: ({wl},{wt_}) - ({wr},{wb})   {wr - wl}x{wb - wt_}")
        print(f"      DPI: {m['dpi']}  →  缩放 {m['scale'] * 100:.0f}%"
              f"   (逻辑尺寸 {round((r - l) / m['scale'])}x{round((b - t) / m['scale'])})")
    scales = {m["scale"] for m in monitors}
    print()
    print(f"  缩放率是否统一: {'是' if len(scales) <= 1 else f'否 —— 混合缩放 {sorted(scales)}'}")
    if len(scales) > 1:
        print("  ⚠️  混合缩放是坐标漂移的重灾区，必须用物理像素存取。")

    print()
    print("=" * 84)
    print("【3】跨屏精确移动实测")
    print("=" * 84)

    notes = find_notepad()
    if not notes:
        print("  没找到记事本窗口，跳过。请先打开一个记事本再跑这个探针。")
        return 0

    hwnd, title = notes[0]
    print(f"  目标窗口: {title!r}  hwnd=0x{hwnd:X}")

    original = rect_of(hwnd)
    print(f"  原始矩形: {original}  尺寸 {original[2]-original[0]}x{original[3]-original[1]}")

    # 每个显示器上取一个「工作区正中放置 800x600」的目标位
    print()
    print("  逐个显示器测试：把窗口摆到该屏工作区正中的 800x600")
    results = []
    for i, m in enumerate(monitors):
        wl, wt_, wr, wb = m["work"]
        tw, th = 800, 600
        x = wl + ((wr - wl) - tw) // 2
        y = wt_ + ((wb - wt_) - th) // 2

        ok = user32.SetWindowPos(hwnd, None, x, y, tw, th,
                                 SWP_NOZORDER | SWP_NOACTIVATE)
        err = ctypes.get_last_error()
        got = rect_of(hwnd)
        exact = got == (x, y, x + tw, y + th)

        print(f"    [{i}] 期望放置 ({x},{y}) {tw}x{th}")
        print(f"        实际结果 ({got[0]},{got[1]}) {got[2]-got[0]}x{got[3]-got[1]}"
              f"   调用={bool(ok)} err={err}")
        print(f"        {'✅ 精确命中' if exact else '❌ 有偏差'}"
              f"{'  （该屏缩放在起作用）' if not exact else ''}")
        results.append(exact)

    # 还原
    l, t, r, b = original
    user32.SetWindowPos(hwnd, None, l, t, r - l, b - t, SWP_NOZORDER | SWP_NOACTIVATE)
    back = rect_of(hwnd)
    print()
    print(f"  已还原到 {back}  完全一致: {'是' if back == original else '否'}")

    print()
    print("=" * 84)
    print("结论")
    print("=" * 84)
    if all(results):
        print("  ✅ 所有显示器上的放置都精确命中。")
        print("     说明本进程已在物理像素空间工作，跨屏（含负坐标）坐标可直接存取。")
    else:
        print("  ⚠️  存在偏差，说明存在逻辑/物理像素换算问题。")
        print("     对策：程序内一律使用物理像素，并在 UI 上按缩放率换算显示。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
