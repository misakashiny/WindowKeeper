# -*- coding: utf-8 -*-
"""
专项诊断：为什么 Chrome 系窗口不响应 SetWindowPos 的尺寸参数。

现象（T6）：msedge 窗口 rect=(-8,-8,2552,1400)，IsZoomed 返回 False，
            但 SetWindowPos 设 900x650 后尺寸变成 2582x1550 —— 像被最大化了。

假设：Chromium 用「无边框最大化」实现，几何等于显示器工作区外扩 8px，
      但 IsZoomed 检测不到。此时窗口处于「自认最大化」状态，
      尺寸参数被忽略，先要显式 SW_RESTORE 才能接受新尺寸。

本脚本验证：
  1. 这个几何是否等于「工作区外扩 8px」
  2. 调 SW_RESTORE 之后再 SetWindowPos 是否能生效
  3. 用 GetWindowPlacement 能否更准确识别最大化状态
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowkeeper import win32_api as w32  # noqa: E402
from windowkeeper.matcher import enumerate_live_windows  # noqa: E402


# ---- GetWindowPlacement：比 IsZoomed 更可靠的窗口状态查询 ----
class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
    ]


user32 = w32.user32
user32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = wt.BOOL

SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOWNORMAL = 1

SHOWCMD_NAME = {0: "SW_HIDE", 1: "SW_SHOWNORMAL", 2: "SW_SHOWMINIMIZED",
                3: "SW_SHOWMAXIMIZED", 4: "SW_SHOWNOACTIVATE",
                5: "SW_SHOW", 6: "SW_MINIMIZE", 7: "SW_SHOWMINNOACTIVE",
                8: "SW_SHOWNA", 9: "SW_RESTORE", 10: "SW_SHOWDEFAULT"}


def get_placement(hwnd) -> WINDOWPLACEMENT:
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
    return wp


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    w32.enable_per_monitor_v2()
    mons = w32.enum_monitors()

    print("=" * 88)
    print("显示器工作区（用于验证 -8 外扩假设）")
    print("=" * 88)
    for m in mons:
        wl, wt_, wr, wb = m.work
        print(f"  [{m.index}] {m.name}  工作区=({wl},{wt_})-({wr},{wb})")
        print(f"      工作区外扩 8px → ({wl - 8},{wt_ - 8})-({wr + 8},{wb + 8})"
              f"  = {wr - wl + 16}x{wb - wt_ + 16}")

    live = enumerate_live_windows(mons, include_minimized=True)
    targets = [w for w in live if w.cls == "Chrome_WidgetWin_1" and not w.minimized]

    print()
    print("=" * 88)
    print("Chrome 系窗口的真实状态（对比 IsZoomed 与 GetWindowPlacement）")
    print("=" * 88)

    for w in targets:
        l, t, r, b = w.rect
        wp = get_placement(w.hwnd)
        mon = next((m for m in mons if m.index == w.monitor_index), None)

        print(f"\n  {w.exe_name}  hwnd=0x{w.hwnd:X}")
        print(f"    rect           = ({l},{t})-({r},{b})  {w.width}x{w.height}")
        print(f"    IsZoomed       = {bool(user32.IsZoomed(w.hwnd))}")
        print(f"    IsIconic       = {bool(user32.IsIconic(w.hwnd))}")
        print(f"    showCmd        = {wp.showCmd} ({SHOWCMD_NAME.get(wp.showCmd, '?')})")
        print(f"    rcNormalPosition= ({wp.rcNormalPosition.left},{wp.rcNormalPosition.top})"
              f"-({wp.rcNormalPosition.right},{wp.rcNormalPosition.bottom})")
        if mon is not None:
            wl, wt_, wr, wb = mon.work
            expanded = (wl - 8, wt_ - 8, wr + 8, wb + 8)
            match = (l, t, r, b) == expanded
            print(f"    所属显示器 [{mon.index}] 工作区外扩8px = {expanded}")
            print(f"    ★ 是否等于「工作区外扩8px」: {'是 —— 确认是无边框最大化' if match else '否'}")
        if wp.showCmd == SW_SHOWMAXIMIZED:
            print("    ★ GetWindowPlacement 报告为【最大化】—— IsZoomed 漏检了！")

    # ---- 实验：SW_RESTORE 之后再设尺寸 ----
    print()
    print("=" * 88)
    print("实验：先 SW_RESTORE 再 SetWindowPos，尺寸能否生效")
    print("=" * 88)

    if not targets:
        print("  没有可测的 Chrome 窗口")
        return 0

    w = targets[0]
    orig = w32.get_window_rect(w.hwnd)
    print(f"  目标 {w.exe_name} 0x{w.hwnd:X}  原始 {orig}")

    # 换一块屏做测试
    cur = next((m for m in mons if m.index == w.monitor_index), mons[0])
    other = next((m for m in mons if m.index != cur.index), mons[0])
    wl, wt_, wr, wb = other.work
    tw, th = 900, 650
    tx = wl + ((wr - wl) - tw) // 2
    ty = wt_ + ((wb - wt_) - th) // 2

    print(f"  目标: 移到显示器 [{other.index}] → ({tx},{ty}) {tw}x{th}")

    print()
    print("  --- 方案A：不做任何预处理，直接两步 SetWindowPos ---")
    w32.apply_geometry(w.hwnd, tx, ty, tw, th, mons)
    got = w32.get_window_rect(w.hwnd)
    print(f"      结果 {got}  {got[2] - got[0]}x{got[3] - got[1]}"
          f"  {'✅' if (got[2] - got[0], got[3] - got[1]) == (tw, th) else '❌'}")

    print()
    print("  --- 方案B：先 SW_RESTORE，再两步 SetWindowPos ---")
    w32.apply_geometry(w.hwnd, orig[0], orig[1],
                       orig[2] - orig[0], orig[3] - orig[1], mons)
    import time
    time.sleep(0.15)
    user32.ShowWindow(w.hwnd, w32.SW_RESTORE)
    time.sleep(0.25)
    after_restore = w32.get_window_rect(w.hwnd)
    wp = get_placement(w.hwnd)
    print(f"      SW_RESTORE 后 rect={after_restore}"
          f"  showCmd={SHOWCMD_NAME.get(wp.showCmd, '?')}")
    w32.apply_geometry(w.hwnd, tx, ty, tw, th, mons)
    time.sleep(0.2)
    got2 = w32.get_window_rect(w.hwnd)
    gw, gh = got2[2] - got2[0], got2[3] - got2[1]
    ok = (gw, gh) == (tw, th) and abs(got2[0] - tx) <= 2 and abs(got2[1] - ty) <= 2
    print(f"      结果 {got2}  {gw}x{gh}  {'✅ 精确命中' if ok else '❌ 仍有偏差'}")

    print()
    print("  --- 方案C：先移到目标屏(不动尺寸)，再 SW_RESTORE，再设尺寸 ---")
    w32.apply_geometry(w.hwnd, orig[0], orig[1],
                       orig[2] - orig[0], orig[3] - orig[1], mons)
    time.sleep(0.15)
    user32.ShowWindow(w.hwnd, w32.SW_RESTORE)
    time.sleep(0.25)
    w32.apply_geometry(w.hwnd, tx, ty, tw, th, mons)
    time.sleep(0.2)
    got3 = w32.get_window_rect(w.hwnd)
    gw3, gh3 = got3[2] - got3[0], got3[3] - got3[1]
    ok3 = (gw3, gh3) == (tw, th)
    print(f"      结果 {got3}  {gw3}x{gh3}  {'✅' if ok3 else '❌'}")

    # 还原
    user32.ShowWindow(w.hwnd, w32.SW_RESTORE)
    time.sleep(0.2)
    w32.apply_geometry(w.hwnd, orig[0], orig[1],
                       orig[2] - orig[0], orig[3] - orig[1], mons)
    print()
    print(f"  已尝试恢复（原几何 {orig}）")

    print()
    print("=" * 88)
    print("结论")
    print("=" * 88)
    print("""
  Chromium 系窗口（Chrome / Edge / VS Code / Electron 应用）使用「无边框最大化」：
    · 几何 = 所属显示器工作区 外扩 8px
    · IsZoomed() 返回 False —— 传统检测方法完全失效
    · GetWindowPlacement().showCmd == SW_SHOWMAXIMIZED —— 可靠检测方法

  处于该状态时，SetWindowPos 的尺寸参数会被忽略。

  ★ 对策：还原前必须用 GetWindowPlacement 检测状态，
         若为最大化则先 ShowWindow(SW_RESTORE)，等待窗口状态更新后再设置几何。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
