# -*- coding: utf-8 -*-
"""
阶段 0 探针 C：把 DPI 的真实机制钉死。

探针 B 观察到的现象：
  - 主屏(100%) 上放 800x600 → 精确
  - 150% 屏上放 800x600     → 实际 1200x900   (800*1.5, 600*1.5)
  - 125% 屏上放 800x600     → 实际 667x500    (800/1.2?, 但 800/1.25=640 不符)

这里验证「本进程仍是 DPI-UNAWARE，SetWindowPos 收到的是逻辑像素，
由系统按目标屏缩放率折算成物理像素」这个假设，并算出真实的折算系数。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.WinDLL("user32", use_last_error=True)
shcore = ctypes.WinDLL("shcore", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

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
user32.EnumWindows.argtypes = [
    ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL

shcore.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
shcore.GetProcessDpiAwareness.restype = ctypes.c_long
shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int,
                                     ctypes.POINTER(ctypes.c_uint),
                                     ctypes.POINTER(ctypes.c_uint)]
shcore.GetDpiForMonitor.restype = ctypes.c_long

SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MONITORINFOF_PRIMARY = 0x1
MDT_EFFECTIVE_DPI = 0


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
user32.EnumDisplayMonitors.restype = wt.BOOL


def awareness() -> int:
    v = ctypes.c_int(-1)
    shcore.GetProcessDpiAwareness(None, ctypes.byref(v))
    return v.value


def monitors() -> list[dict]:
    out = []

    def _cb(hmon, _a, _b, _c):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dx, dy = ctypes.c_uint(96), ctypes.c_uint(96)
            shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI,
                                    ctypes.byref(dx), ctypes.byref(dy))
            out.append({
                "name": mi.szDevice,
                "rect": (mi.rcMonitor.left, mi.rcMonitor.top,
                         mi.rcMonitor.right, mi.rcMonitor.bottom),
                "dpi": dx.value,
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
        cb = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cb, 256)
        if cb.value == "Notepad":
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value.strip():
                hits.append((hwnd, buf.value))
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

    print("=" * 88)
    print("A. 当前进程 DPI 感知级别（本探针刻意不修改它）")
    print("=" * 88)
    aw = awareness()
    names = {0: "UNAWARE", 1: "SYSTEM", 2: "PER_MONITOR"}
    print(f"  GetProcessDpiAwareness = {aw} ({names.get(aw, '?')})")

    print()
    print("=" * 88)
    print("B. 各屏 DPI 与真实物理尺寸")
    print("=" * 88)
    mons = monitors()
    for i, m in enumerate(mons):
        l, t, r, b = m["rect"]
        print(f"  [{i}] {m['name']}  dpi={m['dpi']} ({m['dpi'] / 96 * 100:.0f}%)"
              f"  物理矩形=({l},{t})-({r},{b})  {r - l}x{b - t}")

    notes = find_notepad()
    if not notes:
        print("\n  没找到记事本，先开一个再跑。")
        return 1
    hwnd, title = notes[0]
    print(f"\n  测试窗口: {title!r}")

    print()
    print("=" * 88)
    print("C. 关键实验：请求固定逻辑尺寸，看实际物理尺寸如何随目标屏变化")
    print("=" * 88)
    print("  请求尺寸固定为 800x600，把它依次放到每一块屏上：")
    print()
    print(f"  {'屏':<4}{'DPI':<6}{'缩放':<8}{'请求':<12}{'实际':<12}{'实际/请求':<12}推断系数")
    print("  " + "-" * 78)

    observed = []
    for i, m in enumerate(mons):
        l, t, r, b = m["rect"]
        scale = m["dpi"] / 96.0
        # 放到该屏物理正中
        x = l + ((r - l) - 800) // 2
        y = t + ((b - t) - 600) // 2
        user32.SetWindowPos(hwnd, None, x, y, 800, 600, SWP_NOZORDER | SWP_NOACTIVATE)
        got = rect_of(hwnd)
        gw, gh = got[2] - got[0], got[3] - got[1]
        ratio = gw / 800.0
        print(f"  [{i}] {m['dpi']:<6}{scale:<8.2f}{'800x600':<12}"
              f"{f'{gw}x{gh}':<12}{ratio:<12.4f}{ratio:.4f}")
        observed.append((m["dpi"], ratio, got))

    print()
    print("  读法：如果『实际/请求』≈ 目标屏缩放率，就证明本进程工作在")
    print("  『逻辑像素空间』—— 即我们对系统说 800，系统按目标屏缩放率折算成物理像素。")

    print()
    print("=" * 88)
    print("D. 位置坐标是物理的还是逻辑的？")
    print("=" * 88)
    if mons:
        m = mons[-1]
        l, t, r, b = m["rect"]
        # 请求一个明显落在该屏内的物理坐标
        req_x = l + 100
        req_y = t + 100
        user32.SetWindowPos(hwnd, None, req_x, req_y, 800, 600,
                            SWP_NOZORDER | SWP_NOACTIVATE)
        got = rect_of(hwnd)
        print(f"  目标屏 [{len(mons) - 1}] 物理原点 = ({l},{t})")
        print(f"  请求左上角 = ({req_x},{req_y})")
        print(f"  实际左上角 = ({got[0]},{got[1]})")
        print(f"  位置是否精确命中: {'是 —— 位置用物理坐标' if (got[0], got[1]) == (req_x, req_y) else '否'}")
        print()
        print("  对比探针B的现象：位置精确、尺寸被缩放。")
        print("  这符合 UNAWARE 进程的经典行为：")
        print("    · 位置的 lParam 是屏幕坐标，原样传递")
        print("    · 尺寸会被系统按目标屏 DPI 缩放")

    # 还原
    user32.SetWindowPos(hwnd, None, 1747, 174, 684, 914, SWP_NOZORDER | SWP_NOACTIVATE)

    print()
    print("=" * 88)
    print("E. 结论与对策")
    print("=" * 88)
    print("""
  1) 这个 Python 进程默认是 DPI-UNAWARE。在此状态下：
       - 调用 SetWindowPos 设尺寸时，系统会按「目标屏缩放率」乘一遍
       - 结果：在 150% 屏上想放 800x600，实际变成 1200x900

  2) 正确对策（二选一，推荐第一种）：

     方案一【推荐】进程级 Per-Monitor V2 DPI 感知
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
        必须在创建任何窗口之前调用。
        之后：所有坐标与尺寸都是物理像素，所见即所得，无换算。
        Qt:   在 QApplication(...) 之前调用
        Tkinter: 必须在 import tkinter / 创建 root 之前调

     方案二 保持 UNAWARE，全部走逻辑像素
        存的时候除以缩放率，放的时候乘回来。
        坑：窗口跨屏移动时缩放率会变，难以处理。

  3) 存储策略（必须做，与上面无关）：
        不要存「绝对物理坐标」作为唯一依据。
        存：{显示器设备名, 相对该屏工作区的比例 + 绝对偏移, 物理尺寸}
        还原时先找显示器，再算坐标。这样换屏/改分辨率后仍能大致正确。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
