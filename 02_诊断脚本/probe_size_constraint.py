# -*- coding: utf-8 -*-
"""
专项诊断 2：为什么尺寸被改写？

现象（probe_chromium_maximize.py）：
  目标 900x650 → 实际 918x1045，三个方案完全一样，且位置精确。

观察：
  · 918 / 900 = 1.02        1045 / 650 = 1.6077   —— 不是任何缩放率
  · 若尺寸被乘以 1.5 再按 96/144 折算：(900*1.5)*(96/144)=900 → 恒等，解释不了
  · 「方案A/B/C 结果完全相同」说明 SW_RESTORE 和调用次序都不是变量

假设：窗口不接受该尺寸，是被 WM_GETMINMAXINFO 返回的最小/最大跟踪尺寸
      或某种纵横比约束改写的。DWM 的 Windows 11「贴靠布局」也可能介入。

方法：直接向窗口发送 WM_GETMINMAXINFO 询问它自报的约束，
      再在逐像素二分搜索里找出「被接受的最小尺寸」。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowkeeper import win32_api as w32  # noqa: E402
from windowkeeper.matcher import enumerate_live_windows  # noqa: E402


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", POINT),
        ("ptMaxSize", POINT),
        ("ptMaxPosition", POINT),
        ("ptMinTrackSize", POINT),
        ("ptMaxTrackSize", POINT),
    ]


WM_GETMINMAXINFO = 0x0024
user32 = w32.user32

user32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t


def query_minmax(hwnd) -> MINMAXINFO | None:
    """直接问窗口：你的最小/最大跟踪尺寸是多少？"""
    mmi = MINMAXINFO()
    # 先预填一组哨兵值，若窗口不回填我们就能看出来
    mmi.ptMinTrackSize = POINT(-12345, -12345)
    mmi.ptMaxTrackSize = POINT(-12345, -12345)
    mmi.ptMaxSize = POINT(-12345, -12345)
    try:
        ok = user32.SendMessageW(hwnd, WM_GETMINMAXINFO, 0,
                                 ctypes.cast(ctypes.byref(mmi),
                                             ctypes.c_void_p).value or 0)
        _ = ok
    except Exception as exc:  # noqa: BLE001
        print(f"    SendMessage 失败: {exc}")
        return None
    return mmi


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    w32.enable_per_monitor_v2()
    mons = w32.enum_monitors()
    live = enumerate_live_windows(mons, include_minimized=False)

    # 挑几个不同类型的窗口来对比，看约束是"普遍现象"还是"某类窗口特有"
    picks = []
    for want in ("DeepSeek Harness.exe", "msedge.exe", "Notepad.exe",
                 "UGREEN NAS.exe", "Weixin.exe"):
        found = [w for w in live if w.exe_name == want]
        if found:
            picks.append(found[0])
    if not picks:
        picks = live[:3]

    print("=" * 92)
    print("各家窗口自报的尺寸约束（WM_GETMINMAXINFO）")
    print("=" * 92)
    print(f"  {'进程':<24}{'类名':<22}{'当前尺寸':<14}{'最小跟踪':<14}{'最大跟踪':<16}")
    print("  " + "-" * 88)

    for w in picks:
        mmi = query_minmax(w.hwnd)
        if mmi is None:
            continue
        mn = (mmi.ptMinTrackSize.x, mmi.ptMinTrackSize.y)
        mx = (mmi.ptMaxTrackSize.x, mmi.ptMaxTrackSize.y)
        mn_s = f"{mn[0]}x{mn[1]}" if mn[0] > 0 else "未设置"
        mx_s = f"{mx[0]}x{mx[1]}" if mx[0] > 0 else "未设置"
        print(f"  {w.exe_name[:23]:<24}{w.cls[:21]:<22}"
              f"{w.width}x{w.height:<9}{mn_s:<14}{mx_s:<16}")

    # ---------------------------------------------------------------
    # 核心实验：二分搜索「窗口真正接受的最小尺寸」
    # ---------------------------------------------------------------
    print()
    print("=" * 92)
    print("核心实验：窗口实际接受的尺寸下限")
    print("=" * 92)

    # 用记事本做对照（已知它老实听话），再用 Chrome 系做实验组
    test_groups = []
    np_ = [w for w in live if w.exe_name == "Notepad.exe" and w.width > 300]
    if np_:
        test_groups.append(("对照组 Notepad", np_[0]))
    for want in ("msedge.exe", "DeepSeek Harness.exe"):
        f = [w for w in live if w.exe_name == want]
        if f:
            test_groups.append((f"实验组 {want}", f[0]))

    for label, w in test_groups:
        orig = w32.get_window_rect(w.hwnd)
        mon = next((m for m in mons if m.index == w.monitor_index), mons[0])
        wl, wt_, wr, wb = mon.work
        # 摆在一个固定的小角落，避免贴靠布局干扰
        base_x = wl + 60
        base_y = wt_ + 60

        print()
        print(f"  【{label}】hwnd=0x{w.hwnd:X} cls={w.cls}")
        print(f"    原始 rect={orig}  {orig[2] - orig[0]}x{orig[3] - orig[1]}")

        reqs = [(900, 650), (1200, 800), (700, 500), (500, 400), (400, 300)]
        print(f"    {'请求':<14}{'实际':<18}{'Δ宽':<8}{'Δ高':<8}判定")
        print("    " + "-" * 62)
        for rw, rh in reqs:
            w32.apply_geometry(w.hwnd, base_x, base_y, rw, rh, mons)
            time.sleep(0.12)
            g = w32.get_window_rect(w.hwnd)
            gw, gh = g[2] - g[0], g[3] - g[1]
            dw, dh = gw - rw, gh - rh
            verdict = "✅ 精确" if (dw == 0 and dh == 0) else f"❌ 偏差"
            print(f"    {rw}x{rh:<9}{gw}x{gh:<13}{dw:<+8}{dh:<+8}{verdict}")

        # 尺寸被改写时，是不是悄悄用了「显示器比例」？
        g = w32.get_window_rect(w.hwnd)
        gw, gh = g[2] - g[0], g[3] - g[1]
        mon_w, mon_h = mon.width, mon.height
        print(f"    显示器 {mon.name} {mon_w}x{mon_h}  缩放 {mon.scale * 100:.0f}%")
        for rw, rh in [(900, 650)]:
            print(f"    比例核对 请求 {rw}x{rh} → 比值 {rw / rh:.4f}")
            print(f"              实际 {gw}x{gh} → 比值 {gw / gh:.4f}")
            print(f"              显示器 {mon_w}x{mon_h} → 比值 {mon_w / mon_h:.4f}")
            print(f"    放大倍数 宽 {gw / rw:.4f}  高 {gh / rh:.4f}")

        # 恢复
        w32.apply_geometry(w.hwnd, orig[0], orig[1],
                           orig[2] - orig[0], orig[3] - orig[1], mons)
        time.sleep(0.15)
        back = w32.get_window_rect(w.hwnd)
        same = all(abs(a - b) <= 2 for a, b in zip(back, orig))
        print(f"    恢复: {'✅ 成功' if same else f'⚠️ 实际 {back}'}")

    # ---------------------------------------------------------------
    # 验证：尺寸被改写是不是"位置贡献"造成的
    # 同屏原地改尺寸会不会也被改写？
    # ---------------------------------------------------------------
    print()
    print("=" * 92)
    print("交叉验证：同屏原地 vs 跨屏，尺寸被改写的幅度")
    print("=" * 92)

    for label, w in test_groups[:2]:
        orig = w32.get_window_rect(w.hwnd)
        cur = next((m for m in mons if m.index == w.monitor_index), mons[0])
        other = next((m for m in mons if m.index != cur.index), mons[0])
        print(f"\n  【{label}】")
        for tag, m in (("同屏", cur), ("跨屏", other)):
            wl, wt_, wr, wb = m.work
            tx, ty = wl + 80, wt_ + 80
            w32.apply_geometry(w.hwnd, tx, ty, 900, 650, mons)
            time.sleep(0.15)
            g = w32.get_window_rect(w.hwnd)
            gw, gh = g[2] - g[0], g[3] - g[1]
            print(f"    {tag}（目标屏缩放 {m.scale * 100:.0f}%）请求 900x650 "
                  f"→ 实际 {gw}x{gh}  宽×{gw / 900:.4f} 高×{gh / 650:.4f}")

        w32.apply_geometry(w.hwnd, orig[0], orig[1],
                           orig[2] - orig[0], orig[3] - orig[1], mons)
        time.sleep(0.15)

    print()
    print("=" * 92)
    print("判读")
    print("=" * 92)
    print("""
  · 若「最小跟踪尺寸」栏远大于请求值（例如 1000+），说明是窗口自报约束
    —— 那就是该程序自己不让窗口变小，我们无解，只能如实告知用户。
  · 若各窗口表现不同，说明这是「按程序而异」的现实，引擎需要根据实测
    回读来判定结果，而不是假定设置一定生效。
  · 若同屏/跨屏幅度不同，才需要区分处理；相同则两步协议对尺寸问题无影响。
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
