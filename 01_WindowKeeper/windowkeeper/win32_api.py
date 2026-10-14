# -*- coding: utf-8 -*-
"""
Win32 底层绑定层。

设计原则（来自阶段0探针实测结论）：
  · 所有坐标都是「物理像素」——前提是进程已设为 PER_MONITOR_V2 DPI 感知
  · 位置坐标从不被系统缩放，可以原样存取
  · 跨屏移动 + 改尺寸必须在两次 SetWindowPos 调用中完成（见 apply_geometry）
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass

# --------------------------------------------------------------------------
# DLL
# --------------------------------------------------------------------------

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:  # 极老的系统没有 shcore
    shcore = None


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# SetWindowPos 标志
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOREDRAW = 0x0008
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_NOCOPYBITS = 0x0100
SWP_NOOWNERZORDER = 0x0200
SWP_NOSENDCHANGING = 0x0400
SWP_ASYNCWINDOWPOS = 0x4000

# ShowWindow
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9

# 窗口样式
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOPMOST = 0x00000008

# 最小化窗口的哨兵几何（阶段0实测：GetWindowRect 返回 -32000,-32000 且 160x28）
MINIMIZED_SENTINEL = -30000
MINIMIZED_W = 160
MINIMIZED_H = 28

# 进程权限
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# DPI 感知上下文
DPI_AWARENESS_CONTEXT_UNAWARE = ctypes.c_void_p(-1)
DPI_AWARENESS_CONTEXT_SYSTEM_AWARE = ctypes.c_void_p(-2)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = ctypes.c_void_p(-3)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
DPI_AWARENESS_CONTEXT_UNAWARE_GDISCALED = ctypes.c_void_p(-5)

# GetProcessDpiAwareness 返回值（老 API，只有三档）
# ★ 重要：这里的 2 是 PER_MONITOR_AWARE，**不是** V2。
#   V2 是用 SetProcessDpiAwarenessContext(-4) 引入的「上下文」概念，
#   老 API 没有 V2 这一档，设了 V2 它照样只回 2。
#   想知道到底是不是 V2，必须用下面的 dpi_awareness_context()。
PROCESS_DPI_AWARENESS = {
    0: "UNAWARE",
    1: "SYSTEM_AWARE",
    2: "PER_MONITOR_AWARE",
}

# GetWindowDpiAwarenessContext / GetProcessDpiAwarenessContext 的上下文值
DPI_CONTEXT_NAMES = {
    -1: "UNAWARE",
    -2: "SYSTEM_AWARE",
    -3: "PER_MONITOR_AWARE",
    -4: "PER_MONITOR_AWARE_V2",
    -5: "UNAWARE_GDISCALED",
}

MONITORINFOF_PRIMARY = 0x1
MDT_EFFECTIVE_DPI = 0
MDT_RAW_DPI = 2

# 匹配用的窗口类黑名单：这些是系统壳窗口，不该进预设
SHELL_CLASSES = frozenset({
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
    "Windows.UI.Input.InputSite.WindowClass",
    "ApplicationFrameWindow",     # UWP 外壳，尺寸不可控
    "TextInputHost",
    "CEF-OSC-WIDGET",             # NVIDIA Overlay
    "NVIDIA Overlay",
    "EdgeUiInputTopWndClass",
    "ForegroundStaging",
    "MultitaskingViewFrame",
    "XamlExplorerHostIslandWindow",
    "SysShadow",
    "TaskManagerWindow",
    "Qt51514QWindowIcon_HiddenShell",
})

SHELL_TITLES = frozenset({
    "Program Manager",
    "Windows 输入体验",
    "Windows Input Experience",
    "Microsoft Text Input Application",
    "NVIDIA GeForce Overlay",
    "设置",
    "Settings",
})


# --------------------------------------------------------------------------
# 函数签名
# --------------------------------------------------------------------------

LRESULT = ctypes.c_ssize_t
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.EnumWindows.restype = wt.BOOL
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
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
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindow.restype = wt.BOOL
user32.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
user32.ShowWindowAsync.restype = wt.BOOL
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.BringWindowToTop.restype = wt.BOOL
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.AttachThreadInput.restype = wt.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wt.HWND
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wt.BOOL
user32.GetThreadDpiAwarenessContext.argtypes = []
user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.GetAwarenessFromDpiAwarenessContext.restype = ctypes.c_int

try:
    user32.GetDpiForWindow.argtypes = [wt.HWND]
    user32.GetDpiForWindow.restype = ctypes.c_uint
except AttributeError:
    user32.GetDpiForWindow = None

# ★ 注意：Windows 并没有 GetProcessDpiAwarenessContext 这个函数
#   （很容易和 GetThreadDpiAwarenessContext 记混，实测 user32 里查无此导出）。
#   要拿「本进程」的感知上下文，标准做法就是取主线程的上下文。
try:
    user32.GetThreadDpiAwarenessContext.argtypes = []
    user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
except AttributeError:
    user32.GetThreadDpiAwarenessContext = None

try:
    user32.AreDpiAwarenessContextsEqual.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.AreDpiAwarenessContextsEqual.restype = wt.BOOL
except AttributeError:
    user32.AreDpiAwarenessContextsEqual = None

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL

if shcore is not None:
    shcore.GetProcessDpiAwareness.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_int)]
    shcore.GetProcessDpiAwareness.restype = ctypes.c_long
    shcore.GetDpiForMonitor.argtypes = [wt.HMONITOR, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_uint),
                                        ctypes.POINTER(ctypes.c_uint)]
    shcore.GetDpiForMonitor.restype = ctypes.c_long


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MONITORENUMPROC, wt.LPARAM]
user32.EnumDisplayMonitors.restype = wt.BOOL


# --------------------------------------------------------------------------
# 结构体（必须定义在用到它们的函数之前）
# --------------------------------------------------------------------------

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MINMAXINFO(ctypes.Structure):
    """WM_GETMINMAXINFO 的载荷，窗口用它声明自己的最小/最大尺寸。"""
    _fields_ = [
        ("ptReserved", POINT),
        ("ptMaxSize", POINT),
        ("ptMaxPosition", POINT),
        ("ptMinTrackSize", POINT),
        ("ptMaxTrackSize", POINT),
    ]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", wt.RECT),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


# --------------------------------------------------------------------------
# DPI 感知
# --------------------------------------------------------------------------

_dpi_awareness_set: bool | None = None


def enable_per_monitor_v2() -> bool:
    """
    把本进程设为 PER_MONITOR_V2 DPI 感知。

    ★ 必须在创建任何窗口/QApplication 之前调用，否则无效。
    探针实测：不设置时 GetMonitorInfo 返回虚拟化后的逻辑坐标，
              150% 屏会被报成 1707x1067 而不是真实的 2560x1600。
    """
    global _dpi_awareness_set
    if _dpi_awareness_set is not None:
        return _dpi_awareness_set
    try:
        ok = bool(user32.SetProcessDpiAwarenessContext(
            DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
    except Exception:  # noqa: BLE001
        ok = False
    _dpi_awareness_set = ok
    return ok


def current_dpi_awareness() -> str:
    """
    本进程的 DPI 感知状态（老 API，只有 UNAWARE/SYSTEM_AWARE/PER_MONITOR_AWARE 三档）。

    ★ 想确认到底是不是 V2，请用 dpi_awareness_context()，别用这个。
      老 API 无法区分 PER_MONITOR_AWARE 与 PER_MONITOR_AWARE_V2。
    """
    if shcore is None:
        return "未知(无 shcore)"
    try:
        v = ctypes.c_int(-1)
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(v)) == 0:
            return PROCESS_DPI_AWARENESS.get(v.value, f"?{v.value}")
    except Exception:  # noqa: BLE001
        pass
    return "未知"


def dpi_awareness_context() -> str:
    """
    本进程真实的 DPI 感知上下文，**能区分 V1 和 V2**。

    ★ 为什么需要它：老 API GetProcessDpiAwareness 只有 0/1/2 三档，
      设成 V2 之后它照样返回 2，会被误读成「只是 V1」。
      诊断报告必须给准确值，否则会误导排障方向。

    ★ 实现说明：Windows **没有** GetProcessDpiAwarenessContext 这个函数
      （实测 user32 里查无此导出，很容易和 GetThreadDpiAwarenessContext 记混）。
      进程的感知上下文等于主线程的上下文，所以取线程的即可。
    """
    fn = getattr(user32, "GetThreadDpiAwarenessContext", None)
    if fn is None:
        return "不可用(系统不支持，需 Win10 1607+)"
    try:
        h = fn()
        if not h:
            return "未知(GetThreadDpiAwarenessContext 返回空)"
        h = ctypes.c_void_p(h)

        # 优先直接和已知常量比对：这是最权威的判断方式，
        # 不依赖 GetAwarenessFromDpiAwarenessContext 的数字含义。
        eq = getattr(user32, "AreDpiAwarenessContextsEqual", None)
        if eq is not None:
            for val, name in DPI_CONTEXT_NAMES.items():
                if eq(h, ctypes.c_void_p(val)):
                    return name

        val = user32.GetAwarenessFromDpiAwarenessContext(h)
        return DPI_CONTEXT_NAMES.get(val, f"?{val}")
    except Exception as exc:  # noqa: BLE001
        return f"未知({exc!r})"


# --------------------------------------------------------------------------
# 基础查询
# --------------------------------------------------------------------------

def get_window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def get_process_path(pid: int) -> str:
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


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    return (r.left, r.top, r.right, r.bottom)


def get_window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_minimized_geometry(rect: tuple[int, int, int, int]) -> bool:
    """阶段0实测：最小化窗口返回 (-32000,-32000) 160x28。"""
    l, t, r, b = rect
    if l <= MINIMIZED_SENTINEL and t <= MINIMIZED_SENTINEL:
        return True
    return (r - l) <= MINIMIZED_W and (b - t) <= MINIMIZED_H and l < -30000


def get_window_dpi(hwnd: int) -> int:
    if user32.GetDpiForWindow is not None:
        try:
            return int(user32.GetDpiForWindow(hwnd)) or 96
        except Exception:  # noqa: BLE001
            pass
    return 96


def is_window_alive(hwnd: int) -> bool:
    try:
        return bool(user32.IsWindow(hwnd))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# 显示器
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MonitorInfo:
    index: int
    name: str                       # \\.\DISPLAY1
    rect: tuple[int, int, int, int]  # 物理像素，完整区域
    work: tuple[int, int, int, int]  # 物理像素，可用工作区
    dpi: int
    primary: bool

    @property
    def scale(self) -> float:
        return self.dpi / 96.0

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def enum_monitors() -> list[MonitorInfo]:
    out: list[MonitorInfo] = []

    def _cb(hmon, _hdc, _lprc, _lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dpi = 96
            if shcore is not None:
                dx, dy = ctypes.c_uint(96), ctypes.c_uint(96)
                if shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI,
                                           ctypes.byref(dx), ctypes.byref(dy)) == 0:
                    dpi = dx.value or 96
            out.append(MonitorInfo(
                index=len(out),
                name=mi.szDevice,
                rect=(mi.rcMonitor.left, mi.rcMonitor.top,
                      mi.rcMonitor.right, mi.rcMonitor.bottom),
                work=(mi.rcWork.left, mi.rcWork.top,
                      mi.rcWork.right, mi.rcWork.bottom),
                dpi=dpi,
                primary=bool(mi.dwFlags & MONITORINFOF_PRIMARY),
            ))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    return out


def monitor_for_rect(rect: tuple[int, int, int, int],
                     monitors: list[MonitorInfo] | None = None) -> MonitorInfo | None:
    """按窗口中心点归属显示器；中心点不在任何屏时选交集面积最大的。"""
    mons = monitors if monitors is not None else enum_monitors()
    if not mons:
        return None
    l, t, r, b = rect
    cx, cy = (l + r) // 2, (t + b) // 2
    for m in mons:
        ml, mt, mr, mb = m.rect
        if ml <= cx < mr and mt <= cy < mb:
            return m
    best, best_area = mons[0], -1
    for m in mons:
        ml, mt, mr, mb = m.rect
        ix = max(0, min(r, mr) - max(l, ml))
        iy = max(0, min(b, mb) - max(t, mt))
        if ix * iy > best_area:
            best, best_area = m, ix * iy
    return best


def find_monitor_by_name(name: str,
                         monitors: list[MonitorInfo] | None = None) -> MonitorInfo | None:
    mons = monitors if monitors is not None else enum_monitors()
    for m in mons:
        if m.name == name:
            return m
    return None


# --------------------------------------------------------------------------
# 窗口操作
# --------------------------------------------------------------------------

def apply_geometry(hwnd: int, x: int, y: int, w: int, h: int,
                   monitors: list[MonitorInfo] | None = None) -> tuple[bool, bool]:
    """
    把窗口摆到指定物理位置与尺寸。

    ★★ 这是整个项目的核心调用协议，来自阶段0实测结论 ★★

    探针实测（probe_isolation.py）：
        组3 跨屏移动+改尺寸（一次调用）:  请求 800x600 → 实际 1200x900  ❌
        组4 先跨屏移动，再单独改尺寸:      请求 750x550 → 实际 750x550   ✅

    根因：同一次 SetWindowPos 中同时改变显示器归属与尺寸时，
          系统按【旧屏 DPI】解释尺寸，落屏后按【新屏 DPI】重算，
          偏差 = 新屏DPI / 旧屏DPI。

    因此：先判断是否需要跨屏，需要则分两步调用。

    返回 (位置调用成功, 尺寸调用成功)
    """
    if w <= 0 or h <= 0:
        return (False, False)

    mons = monitors if monitors is not None else enum_monitors()
    cur_rect = get_window_rect(hwnd)
    cur_mon = monitor_for_rect(cur_rect, mons)
    tgt_mon = monitor_for_rect((x, y, x + w, y + h), mons)

    cross_monitor = (cur_mon is not None and tgt_mon is not None
                     and cur_mon.name != tgt_mon.name)

    if not cross_monitor:
        # 同屏：一次调用完成，实测精确
        ok = bool(user32.SetWindowPos(
            hwnd, None, x, y, w, h,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER))
        return (ok, ok)

    # ★ 跨屏：第一步只移动（尺寸维持当前值），第二步只改尺寸
    cw = max(1, cur_rect[2] - cur_rect[0])
    ch = max(1, cur_rect[3] - cur_rect[1])
    ok1 = bool(user32.SetWindowPos(
        hwnd, None, x, y, cw, ch,
        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER))
    ok2 = bool(user32.SetWindowPos(
        hwnd, None, x, y, w, h,
        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER))
    return (ok1, ok2)


def restore_if_minimized(hwnd: int) -> None:
    """
    阶段0实测：最小化窗口的 GetWindowRect 返回 (-32000,-32000)，
    必须先 SW_RESTORE 才能正确设置几何。
    """
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)


def unmaximize(hwnd: int) -> None:
    """
    最大化窗口要先还原，否则 SWP 设置的位置会被忽略。

    ★ 补充发现（probe_chromium_maximize.py 实测）：
      Chromium 系窗口（Chrome / Edge / VS Code / Electron）用「无边框最大化」，
      几何等于工作区外扩 8px，但 IsZoomed() 返回 False —— 传统检测会漏掉。
      可靠做法是看 GetWindowPlacement().showCmd == SW_SHOWMAXIMIZED(3)。

      不过实测也表明：处在 SW_SHOWNORMAL 的普通窗口同样会因最小尺寸约束
      而拒绝缩小，所以「检测最大化」主要解决位置被忽略的问题，
      尺寸问题另有根源（见 get_min_track_size）。
    """
    if user32.IsZoomed(hwnd) or get_show_cmd(hwnd) == SW_SHOWMAXIMIZED:
        user32.ShowWindow(hwnd, SW_RESTORE)


user32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = wt.BOOL


def get_show_cmd(hwnd: int) -> int:
    """比 IsZoomed 更可靠的窗口显示状态查询。"""
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return int(wp.showCmd)
    except Exception:  # noqa: BLE001
        pass
    return 0


def is_maximized(hwnd: int) -> bool:
    """综合两个来源判断最大化，避免漏掉无边框最大化。"""
    try:
        return bool(user32.IsZoomed(hwnd)) or get_show_cmd(hwnd) == SW_SHOWMAXIMIZED
    except Exception:  # noqa: BLE001
        return False


def get_normal_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """
    取窗口「还原后」的几何。

    对最大化窗口，GetWindowRect 给的是最大化后的外框，
    而 GetWindowPlacement().rcNormalPosition 才是它还原后的位置。
    阶段0曾观察到 msedge 最大化时几何是工作区外扩 8px，
    用这个函数就能拿到它真正的常规几何。
    """
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            r = wp.rcNormalPosition
            return (r.left, r.top, r.right, r.bottom)
    except Exception:  # noqa: BLE001
        pass
    return None


def activate_window(hwnd: int) -> bool:
    """把窗口带到前台。Windows 有前台锁定，需要用 AttachThreadInput 绕过。"""
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        cur_thread = kernel32.GetCurrentThreadId()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tgt_thread = user32.GetWindowThreadProcessId(hwnd, None)
        attached_fg = attached_tgt = False
        try:
            if fg_thread and fg_thread != cur_thread:
                attached_fg = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
            if tgt_thread and tgt_thread != cur_thread:
                attached_tgt = bool(user32.AttachThreadInput(cur_thread, tgt_thread, True))
            user32.BringWindowToTop(hwnd)
            return bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached_fg:
                user32.AttachThreadInput(cur_thread, fg_thread, False)
            if attached_tgt:
                user32.AttachThreadInput(cur_thread, tgt_thread, False)
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# 窗口自报的尺寸约束
# --------------------------------------------------------------------------

WM_GETMINMAXINFO = 0x0024

user32.SendMessageW.argtypes = [wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t


def get_min_track_size(hwnd: int) -> tuple[int, int]:
    """
    询问窗口自己允许的最小尺寸（WM_GETMINMAXINFO）。

    ★★ 为什么必须有这个函数 ★★

    实测（probe_size_constraint.py）：
        DeepSeek Harness.exe  自报最小 612x697
            请求 900x650 → 实际 900x697   （高度被顶到下限）
            请求 700x500 → 实际 700x697   （高度被顶到下限）
            请求 500x400 → 实际 612x697   （宽高都被顶到下限）
            请求 1200x800→ 实际 1200x800  （大于下限，通过）
        对照 Notepad.exe  自报最小 332x202
            五个请求全部精确命中

    也就是说：SetWindowPos 返回成功 ≠ 尺寸真的生效。
    每个程序都可以合法地声明「我不接受小于 WxH」，系统会照办。
    位置没有这种约束，所以位置永远是精确的。

    这不是 bug，是 Windows 的既定行为。引擎必须如实回读并区分：
      · 尺寸被顶到窗口自报下限 → 这是「受约束」，要向用户说明，不是失败
      · 尺寸被改成别的值       → 这才是真的异常
    """
    mmi = MINMAXINFO()
    try:
        user32.SendMessageW(hwnd, WM_GETMINMAXINFO, 0,
                            ctypes.cast(ctypes.byref(mmi),
                                        ctypes.c_void_p).value or 0)
    except Exception:  # noqa: BLE001
        return (0, 0)
    return (max(0, mmi.ptMinTrackSize.x), max(0, mmi.ptMinTrackSize.y))


def get_max_track_size(hwnd: int) -> tuple[int, int]:
    mmi = MINMAXINFO()
    try:
        user32.SendMessageW(hwnd, WM_GETMINMAXINFO, 0,
                            ctypes.cast(ctypes.byref(mmi),
                                        ctypes.c_void_p).value or 0)
    except Exception:  # noqa: BLE001
        return (0, 0)
    mx = (mmi.ptMaxTrackSize.x, mmi.ptMaxTrackSize.y)
    # 2147483647 表示「无限制」
    if mx[0] >= 2147483647:
        mx = (0, mx[1])
    if mx[1] >= 2147483647:
        mx = (mx[0], 0)
    return mx


@dataclass
class GeoResult:
    """apply_geometry_verified 的结果：请求值 vs 实际生效值。"""
    requested: tuple[int, int, int, int]
    actual: tuple[int, int, int, int]
    calls_ok: tuple[bool, ...]
    cross_monitor: bool

    @property
    def pos_exact(self) -> bool:
        return (abs(self.actual[0] - self.requested[0]) <= 1
                and abs(self.actual[1] - self.requested[1]) <= 1)

    @property
    def size_exact(self) -> bool:
        aw = self.actual[2] - self.actual[0]
        ah = self.actual[3] - self.actual[1]
        return abs(aw - self.requested[2]) <= 1 and abs(ah - self.requested[3]) <= 1

    @property
    def exact(self) -> bool:
        return self.pos_exact and self.size_exact

    @property
    def size_delta(self) -> tuple[int, int]:
        return (self.actual[2] - self.actual[0] - self.requested[2],
                self.actual[3] - self.actual[1] - self.requested[3])

    def describe_delta(self) -> str:
        if self.exact:
            return "精确"
        parts = []
        if not self.pos_exact:
            parts.append(f"位置偏({self.actual[0] - self.requested[0]:+d},"
                         f"{self.actual[1] - self.requested[1]:+d})")
        if not self.size_exact:
            dw, dh = self.size_delta
            parts.append(f"尺寸偏({dw:+d}x{dh:+d})")
        return "，".join(parts)


def apply_geometry_verified(hwnd: int, x: int, y: int, w: int, h: int,
                            monitors: list[MonitorInfo] | None = None,
                            min_track: tuple[int, int] | None = None,
                            ) -> GeoResult:
    """
    设置几何并回读校验。

    比 apply_geometry 多做的事：
      1. 把请求尺寸预先夹到窗口自报的最小跟踪尺寸之上
         （避免明知会被顶开还去撞，减少一次无谓的窗口抖动）
      2. 调用后回读真实几何，供上层判断「精确 / 受限 / 异常」

    min_track 传入可复用已查到的约束，省一次跨进程消息。
    """
    if w <= 0 or h <= 0:
        return GeoResult((x, y, w, h), get_window_rect(hwnd), (False,), False)

    mons = monitors if monitors is not None else enum_monitors()

    # 预先夹到窗口允许的下限。夹不动就照原样试，让回读去报告真相。
    if min_track is None:
        min_track = get_min_track_size(hwnd)
    aw, ah = w, h
    if min_track[0] > 0 and aw < min_track[0]:
        aw = min_track[0]
    if min_track[1] > 0 and ah < min_track[1]:
        ah = min_track[1]

    cur_rect = get_window_rect(hwnd)
    cur_mon = monitor_for_rect(cur_rect, mons)
    tgt_mon = monitor_for_rect((x, y, x + aw, y + ah), mons)
    cross = (cur_mon is not None and tgt_mon is not None
             and cur_mon.name != tgt_mon.name)

    ok = apply_geometry(hwnd, x, y, aw, ah, mons)

    return GeoResult(
        requested=(x, y, w, h),
        actual=get_window_rect(hwnd),
        calls_ok=tuple(ok) if isinstance(ok, tuple) else (bool(ok),),
        cross_monitor=cross,
    )


def refresh_window(hwnd: int) -> None:
    """强制窗口重绘边框（改样式后需要）。"""
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                        | SWP_NOACTIVATE | SWP_FRAMECHANGED)


def set_topmost(hwnd: int, on: bool) -> bool:
    HWND_TOPMOST = wt.HWND(-1)
    HWND_NOTOPMOST = wt.HWND(-2)
    return bool(user32.SetWindowPos(
        hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))
