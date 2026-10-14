# -*- coding: utf-8 -*-
"""
阶段 0 探针：验证本机能否枚举顶层窗口、提取窗口指纹、并移动/缩放窗口。

零依赖，纯 ctypes + 标准库。目的只有一个：
在写任何 UI 之前，确认「窗口识别 + 操控」这条关键路径是通的。

用法:
    python probe_windows.py            # 枚举所有可见顶层窗口
    python probe_windows.py --move     # 额外做一次移动/缩放实测
    python probe_windows.py --move --title 记事本
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Win32 绑定
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 让 64 位下的句柄不被截断成 32 位 int
LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL

user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL

user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL

user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL

user32.IsZoomed.argtypes = [wt.HWND]
user32.IsZoomed.restype = wt.BOOL

user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL

user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD

user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]
user32.SetWindowPos.restype = wt.BOOL

user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL

user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE

kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL

kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

# SetWindowPos 标志
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SW_RESTORE = 9

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------

@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    exe: str
    pid: int
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    minimized: bool = False
    maximized: bool = False
    monitor_index: int = -1
    monitor_name: str = ""
    note: str = ""

    @property
    def size(self) -> tuple[int, int]:
        return self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]

    @property
    def exe_name(self) -> str:
        return self.exe.rsplit("\\", 1)[-1] if self.exe else "?"


@dataclass
class ProbeReport:
    total_enumerated: int = 0
    real_windows: list[WindowInfo] = field(default_factory=list)
    filtered: dict[str, int] = field(default_factory=dict)

    def bump(self, reason: str) -> None:
        self.filtered[reason] = self.filtered.get(reason, 0) + 1


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------

def _proc_path(pid: int) -> str:
    """拿到进程的完整 exe 路径。失败返回空串（多半是权限不够）。"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _monitor_rects() -> list[tuple[str, tuple[int, int, int, int], bool]]:
    """枚举显示器：(设备名, (l,t,r,b), 是否为主显示器)。"""
    monitors: list[tuple[str, tuple[int, int, int, int], bool]] = []

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

    def _cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            monitors.append((
                mi.szDevice,
                (r.left, r.top, r.right, r.bottom),
                bool(mi.dwFlags & MONITORINFOF_PRIMARY),
            ))
        return True

    user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wt.BOOL
    user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
    user32.EnumDisplayMonitors.restype = wt.BOOL

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    return monitors


def _assign_monitor(rect: tuple[int, int, int, int], monitors) -> tuple[int, str]:
    """按窗口中心点落在哪个显示器来归属。"""
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    for idx, (name, (l, t, r, b), _primary) in enumerate(monitors):
        if l <= cx < r and t <= cy < b:
            return idx, name
    # 中心点不在任何屏内（跨屏或跑飞）→ 用交集面积最大的
    best_i, best_area, best_name = 0, 0, ""
    for idx, (name, (l, t, r, b), _primary) in enumerate(monitors):
        ix = max(0, min(rect[2], r) - max(rect[0], l))
        iy = max(0, min(rect[3], b) - max(rect[1], t))
        if ix * iy > best_area:
            best_i, best_area, best_name = idx, ix * iy, name
    return best_i, best_name


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------

def enumerate_windows(verbose: bool = False) -> ProbeReport:
    report = ProbeReport()
    monitors = _monitor_rects()

    def _cb(hwnd, _lparam):
        report.total_enumerated += 1

        if not user32.IsWindowVisible(hwnd):
            report.bump("不可见")
            return True

        title_len = user32.GetWindowTextLengthW(hwnd)
        if title_len == 0:
            report.bump("无标题")
            return True

        buf = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, buf, title_len + 1)
        title = buf.value.strip()
        if not title:
            report.bump("空标题")
            return True

        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value

        ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
            report.bump("工具窗口")
            return True

        rect = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            report.bump("取矩形失败")
            return True

        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        info = WindowInfo(
            hwnd=hwnd,
            title=title,
            cls=cls,
            exe=_proc_path(pid.value),
            pid=pid.value,
            rect=(rect.left, rect.top, rect.right, rect.bottom),
            minimized=bool(user32.IsIconic(hwnd)),
            maximized=bool(user32.IsZoomed(hwnd)),
        )
        info.monitor_index, info.monitor_name = _assign_monitor(info.rect, monitors)
        if not info.exe:
            info.note = "拿不到进程路径(权限)"

        report.real_windows.append(info)
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return report


# --------------------------------------------------------------------------
# 移动实测
# --------------------------------------------------------------------------

def test_move(info: WindowInfo, dx: int = 60, dy: int = 60) -> bool:
    """把窗口先挪动一点，再还原回原处。验证 SetWindowPos 是否真的生效。"""
    l, t, r, b = info.rect
    w, h = r - l, b - t

    if info.minimized:
        user32.ShowWindow(info.hwnd, SW_RESTORE)

    print(f"  原位置: ({l}, {t})  尺寸: {w}x{h}")

    ok1 = user32.SetWindowPos(
        info.hwnd, None, l + dx, t + dy, w, h,
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    err1 = ctypes.get_last_error()

    after = wt.RECT()
    user32.GetWindowRect(info.hwnd, ctypes.byref(after))
    moved = (after.left, after.top) != (l, t)

    print(f"  移动后: ({after.left}, {after.top})  SetWindowPos 返回={bool(ok1)} err={err1}")
    print(f"  实际是否移动: {'是' if moved else '否'}")

    # 还原（顺便测一次缩放）
    ok2 = user32.SetWindowPos(
        info.hwnd, None, l, t, w, h,
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    back = wt.RECT()
    user32.GetWindowRect(info.hwnd, ctypes.byref(back))
    restored = (back.left, back.top, back.right, back.bottom) == (l, t, r, b)

    print(f"  还原后: ({back.left}, {back.top}, {back.right}, {back.bottom})  "
          f"返回={bool(ok2)}  完全还原={'是' if restored else '否'}")

    # 再测一次真正的尺寸变化
    ok3 = user32.SetWindowPos(
        info.hwnd, None, l, t, max(400, w - 120), max(300, h - 120),
        SWP_NOZORDER | SWP_NOACTIVATE,
    )
    resized = wt.RECT()
    user32.GetWindowRect(info.hwnd, ctypes.byref(resized))
    print(f"  缩小测试: {resized.right - resized.left}x{resized.bottom - resized.top}  "
          f"返回={bool(ok3)}")

    user32.SetWindowPos(info.hwnd, None, l, t, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
    print("  已恢复原始几何")

    return moved


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------

def print_report(report: ProbeReport, monitors) -> None:
    print("=" * 100)
    print("显示器")
    print("=" * 100)
    for idx, (name, (l, t, r, b), primary) in enumerate(monitors):
        print(f"  [{idx}] {name}  ({l},{t})-({r},{b})  {r - l}x{b - t}"
              f"{'  <主显示器>' if primary else ''}")
    if not monitors:
        print("  (未检测到显示器？！)")

    windows = sorted(report.real_windows, key=lambda w: (w.monitor_index, w.exe_name, w.title))

    print()
    print("=" * 100)
    print(f"可见顶层窗口（{len(windows)} 个）")
    print("=" * 100)
    print(f"{'#':>3}  {'显示器':<4} {'进程':<22} {'类名':<28} {'几何':<24} 标题")
    print("-" * 100)
    for i, w in enumerate(windows, 1):
        l, t, r, b = w.rect
        geom = f"{l},{t} {r - l}x{b - t}"
        if w.minimized:
            geom += " [最小化]"
        elif w.maximized:
            geom += " [最大化]"
        print(f"{i:>3}  {w.monitor_index:<4} {w.exe_name[:21]:<22} "
              f"{w.cls[:27]:<28} {geom:<24} {w.title[:34]}")

    print()
    print("=" * 100)
    print("被过滤掉的窗口（数量 / 原因）")
    print("=" * 100)
    for reason, cnt in sorted(report.filtered.items(), key=lambda kv: -kv[1]):
        print(f"  {reason:<16} {cnt}")

    # 指纹可辨识度分析 —— 这是关键结论
    print()
    print("=" * 100)
    print("指纹可辨识度分析（决定匹配算法能有多准）")
    print("=" * 100)
    by_exe: dict[str, list[WindowInfo]] = {}
    for w in windows:
        by_exe.setdefault(w.exe_name, []).append(w)

    dupes = {k: v for k, v in by_exe.items() if len(v) > 1}
    print(f"  不同应用数: {len(by_exe)}")
    print(f"  存在多窗口的应用: {len(dupes)}")
    for exe, ws in sorted(dupes.items(), key=lambda kv: -len(kv[1])):
        print(f"    - {exe}: {len(ws)} 个窗口"
              f"  （类名{'不同' if len({w.cls for w in ws}) > 1 else '相同'}）")
        for w in ws[:4]:
            print(f"        cls={w.cls[:34]:<36} title={w.title[:40]}")

    print()
    print(f"  枚举总窗口数: {report.total_enumerated}，其中真实可用: {len(windows)}")


def _fix_console_encoding() -> None:
    """控制台默认 GBK，中文会乱码。尽量切成 UTF-8。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    _fix_console_encoding()

    ap = argparse.ArgumentParser(description="阶段0 可行性探针")
    ap.add_argument("--move", action="store_true", help="做一次移动/缩放实测")
    ap.add_argument("--title", default="", help="--move 时按标题关键字挑选目标窗口")
    ap.add_argument("--index", type=int, default=0, help="--move 时的窗口序号(从报告里看)")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("这个探针只能在 Windows 上跑。")
        return 2

    report = enumerate_windows()
    monitors = _monitor_rects()
    print_report(report, monitors)

    if args.move:
        print()
        print("=" * 100)
        print("移动 / 缩放实测")
        print("=" * 100)

        windows = sorted(report.real_windows, key=lambda w: (w.monitor_index, w.exe_name, w.title))

        target = None
        if args.title:
            kw = args.title.lower()
            cands = [w for w in windows if kw in w.title.lower() or kw in w.exe_name.lower()]
            # 优先挑非最小化的
            cands.sort(key=lambda w: w.minimized)
            target = cands[0] if cands else None
            if not target:
                print(f"  没找到标题/进程名包含 '{args.title}' 的窗口")
        elif windows:
            target = windows[min(args.index, len(windows) - 1)]

        if not target:
            print("  没有可测试的目标窗口。")
            return 1

        print(f"  目标: [{target.exe_name}] pid={target.pid} hwnd=0x{target.hwnd:X}")
        print(f"  标题: {target.title}")
        print(f"  类名: {target.cls}")
        try:
            moved = test_move(target)
        except Exception as exc:  # noqa: BLE001
            print(f"  实测抛异常: {exc!r}")
            return 1

        print()
        if moved:
            print("  ✅ 结论: SetWindowPos 生效，窗口操控路径可用。")
        else:
            print("  ⚠️  结论: 调用没报错但窗口没动。可能是:")
            print("      - 目标窗口以管理员权限运行（需要本程序也提权）")
            print("      - 全屏 / UWP 特殊窗口")
            print("      - 有程序在持续重设自己的窗口位置")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
