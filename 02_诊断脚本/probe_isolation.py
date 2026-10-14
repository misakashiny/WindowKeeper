# -*- coding: utf-8 -*-
"""
阶段 0 探针 F：决定性实验 —— 把「跨屏移动」和「改尺寸」两个变量彻底分开。

前面所有实验都犯了一个方法学错误：把窗口「移动到另一块屏」的同时「改尺寸」，
两个变量混在一起，无法判断到底是哪个环节产生的缩放。

本实验分三组：
  组1 同屏原地改尺寸          (不移动)
  组2 同屏内移动 + 改尺寸      (移动但不跨屏)
  组3 跨屏移动 + 改尺寸        (移动且跨屏)

每一组都在三种 DPI 模式下重复，得到干净的因果结论。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)

MODE = sys.argv[1] if len(sys.argv) > 1 else "unaware"

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
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
shcore.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
shcore.GetProcessDpiAwareness.restype = ctypes.c_long

SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MONITORINFOF_PRIMARY = 0x1
MDT_EFFECTIVE_DPI = 0


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT),
                ("dwFlags", wt.DWORD), ("szDevice", ctypes.c_wchar * 32)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
user32.EnumDisplayMonitors.restype = wt.BOOL
shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_uint),
                                    ctypes.POINTER(ctypes.c_uint)]
shcore.GetDpiForMonitor.restype = ctypes.c_long


def monitors():
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
                        "dpi": dx.value, "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY)})
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    return out


def find_target():
    """找一个稳定的原生窗口：文件资源管理器 > Chrome > 微信。"""
    found = []

    def _cb(hwnd, _l):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cb, 256)
        cls = cb.value
        if cls in ("CabinetWClass", "Chrome_WidgetWin_1", "Qt51514QWindowIcon"):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            # 排除最小化（-32000）
            if r.left > -30000:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                found.append((cls, hwnd, b.value))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(_cb), 0)
    prio = {"CabinetWClass": 0, "Qt51514QWindowIcon": 1, "Chrome_WidgetWin_1": 2}
    found.sort(key=lambda x: prio.get(x[0], 9))
    return found[0] if found else None


def rect_of(h):
    r = wt.RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def do(h, x, y, w, hh):
    user32.SetWindowPos(h, None, x, y, w, hh, SWP_NOZORDER | SWP_NOACTIVATE)
    return rect_of(h)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    mons = monitors()
    t = find_target()
    if not t:
        print("找不到可测窗口")
        return 0
    cls, hwnd, title = t

    print(f"模式={MODE}  目标={cls} {title[:32]!r}")
    print(f"显示器: " + "  ".join(
        f"[{i}]dpi{m['dpi']}({m['dpi']//96*100 if m['dpi']%96==0 else m['dpi']/96*100:.0f}%)" 
        f"@({m['rect'][0]},{m['rect'][1]})" for i, m in enumerate(mons)))

    orig = rect_of(hwnd)
    print(f"原始: ({orig[0]},{orig[1]}) {orig[2]-orig[0]}x{orig[3]-orig[1]}")
    print()
    print(f"  {'组':<26}{'请求位置':<20}{'请求尺寸':<12}{'实际尺寸':<14}{'倍率'}")
    print("  " + "-" * 82)

    def report(label, x, y, w, hh):
        g = do(hwnd, x, y, w, hh)
        gw, gh = g[2] - g[0], g[3] - g[1]
        ratio = gw / w if w else 0
        mark = "✅" if (gw, gh) == (w, hh) else "❌"
        print(f"  {label:<26}{f'({g[0]},{g[1]})':<20}{f'{w}x{hh}':<12}"
              f"{f'{gw}x{gh}':<14}{ratio:.4f} {mark}")
        return g

    # 定位一个主屏上的稳定落点
    m0 = mons[0]
    l0, t0, r0, b0 = m0["rect"]
    home_x = l0 + 200
    home_y = t0 + 200

    # 先把窗口归位到主屏
    do(hwnd, home_x, home_y, 900, 700)

    # ---- 组1：同屏原地改尺寸（位置完全不动）----
    print("  组1 同屏原地改尺寸（位置不变）")
    for w, hh in [(800, 600), (1000, 800), (900, 700)]:
        report(f"    原地 {w}x{hh}", home_x, home_y, w, hh)

    print()
    # ---- 组2：同屏内移动 + 改尺寸 ----
    print("  组2 同屏内移动 + 改尺寸（不跨屏）")
    for dx in (0, 300, 600):
        report(f"    移动dx={dx} 800x600", home_x + dx, home_y, 800, 600)

    print()
    # ---- 组3：跨屏移动 + 改尺寸 ----
    print("  组3 跨屏移动 + 改尺寸")
    for i, m in enumerate(mons):
        l, t_, r, b = m["rect"]
        cx = l + ((r - l) - 800) // 2
        cy = t_ + ((b - t_) - 600) // 2
        report(f"    跨到屏[{i}] dpi={m['dpi']}", cx, cy, 800, 600)

    print()
    # ---- 组4：先跨屏移动，再原地改尺寸（两步分离）----
    print("  组4 先跨屏移动 → 再同屏改尺寸（分离两步）")
    for i, m in enumerate(mons):
        l, t_, r, b = m["rect"]
        cx = l + ((r - l) - 800) // 2
        cy = t_ + ((b - t_) - 600) // 2
        do(hwnd, cx, cy, 800, 600)          # 第一步：跨屏定位
        g = report(f"    屏[{i}] 第二步改尺寸", cx, cy, 750, 550)
        _ = g

    # 还原
    user32.SetWindowPos(hwnd, None, orig[0], orig[1],
                        orig[2] - orig[0], orig[3] - orig[1],
                        SWP_NOZORDER | SWP_NOACTIVATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
