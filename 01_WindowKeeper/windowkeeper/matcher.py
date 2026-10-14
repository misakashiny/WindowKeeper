# -*- coding: utf-8 -*-
"""
窗口模型与指纹匹配。

阶段0探针揭示的现实：
  · explorer.exe 有 4 个窗口，类名全同 → 只能靠标题
  · msedge.exe   有 4 个窗口，类名全同 → 标题含「和另外 N 个页面」，会变
  · video-player.exe 有 2 个窗口，进程名、类名、标题【全部相同】→ 指纹无法区分

所以匹配必须是「分级 + 打分」而不是「精确查找」，
并且必须承认有些情况就是无法唯一确定，要让用户手动指定。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable

from . import win32_api as w32


# --------------------------------------------------------------------------
# 标题清洗
# --------------------------------------------------------------------------

# ★ 不可见字符必须先清掉，否则任何正则都可能在它上面失配。
#   实测：Edge 标题「… - 个人 - Microsoft\u200b Edge」里 Microsoft 与 Edge
#   之间夹了一个零宽空格，导致 `Microsoft.?Edge` 也匹配不上（`.` 不匹配它
#   是因为它被当作普通字符吃掉了位置）。
_INVISIBLE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]", re.UNICODE)

# 浏览器标题尾部常见噪声；这些内容会随用户操作变化，不能作为匹配依据
_TITLE_NOISE_PATTERNS = [
    re.compile(r"\s*和另外\s*\d+\s*个页面", re.UNICODE),
    re.compile(r"\s*and\s+\d+\s+more\s+pages?", re.IGNORECASE),
    # 「- 个人 - Microsoft Edge」整段（含前面的 和另外N个页面 已在上一条清掉）
    re.compile(r"\s*[-–—]\s*(个人|工作|Personal|Work)\s*[-–—]\s*"
               r"(Microsoft|Google)?\s*Edge\s*$", re.IGNORECASE),
    re.compile(r"\s*[-–—]\s*(Google Chrome|Mozilla Firefox|Brave|Vivaldi)\s*$",
               re.IGNORECASE),
    re.compile(r"\s*[-–—]\s*Visual Studio Code\s*$", re.IGNORECASE),
    re.compile(r"\s*\[(管理员|Administrator|已修改|Modified)\]\s*$"),
    re.compile(r"\s*[-–—]\s*(文件资源管理器|File Explorer)\s*$", re.IGNORECASE),
]

# 未保存指示器：记事本/编辑器的 "*" 或圆点会变
_LEADING_MARKERS = re.compile(r"^[\*\u2022\u25cf\u25cb]\s*")


def clean_title(title: str) -> str:
    """把标题里的易变部分剥掉，留下稳定主干。"""
    t = _INVISIBLE.sub("", title or "").strip()
    t = _LEADING_MARKERS.sub("", t)
    for pat in _TITLE_NOISE_PATTERNS:
        t = pat.sub("", t)
    return t.strip()


def title_fingerprint(title: str) -> str:
    """标题的稳定部分用于弱匹配：取主干的前 24 个字符。"""
    return clean_title(title)[:24]


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------

@dataclass
class WindowRef:
    """一个窗口的完整指纹 + 几何，是预设的最小单元。"""
    # --- 身份指纹（按可靠度从高到低）---
    exe: str = ""                # 完整 exe 路径
    exe_name: str = ""           # notepad.exe
    cls: str = ""                # 窗口类名
    title: str = ""              # 原始标题
    title_fp: str = ""           # 清洗后的标题主干

    # --- 几何（物理像素）---
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    # --- 显示器归属（换屏后能否还原的关键）---
    monitor_name: str = ""       # \\.\DISPLAY1
    monitor_index: int = 0
    # 相对该显示器工作区左上角的偏移，换分辨率/换屏后做回退用
    offset_in_work: tuple[int, int] = (0, 0)
    # 相对工作区的归一化位置（0~1），显示器尺寸变了也能大致落对位置
    rel_in_work: tuple[float, float] = (0.0, 0.0)
    rel_size: tuple[float, float] = (0.0, 0.0)

    # --- 状态 ---
    minimized: bool = False
    maximized: bool = False
    dpi: int = 96

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["offset_in_work"] = list(self.offset_in_work)
        d["rel_in_work"] = list(self.rel_in_work)
        d["rel_size"] = list(self.rel_size)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WindowRef":
        d = dict(d)
        for k, default in (("offset_in_work", (0, 0)),
                           ("rel_in_work", (0.0, 0.0)),
                           ("rel_size", (0.0, 0.0))):
            v = d.get(k, default)
            d[k] = tuple(v) if isinstance(v, (list, tuple)) else default
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        d = {k: v for k, v in d.items() if k in known}
        return cls(**d)

    @property
    def is_valid(self) -> bool:
        return self.w > 0 and self.h > 0 and bool(self.exe_name or self.cls)

    def identity_key(self) -> str:
        """用于去重和索引的稳定键。"""
        return f"{self.exe_name}|{self.cls}|{self.title_fp}"


@dataclass
class LiveWindow:
    """当前系统里的一个活动窗口。"""
    hwnd: int
    title: str
    cls: str
    exe: str
    exe_name: str
    pid: int
    rect: tuple[int, int, int, int]
    minimized: bool
    maximized: bool
    dpi: int
    monitor_name: str = ""
    monitor_index: int = -1

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    def to_ref(self, monitors: list[w32.MonitorInfo] | None = None) -> WindowRef:
        mons = monitors if monitors is not None else w32.enum_monitors()
        mon = w32.monitor_for_rect(self.rect, mons)
        ref = WindowRef(
            exe=self.exe, exe_name=self.exe_name, cls=self.cls,
            title=self.title, title_fp=title_fingerprint(self.title),
            x=self.rect[0], y=self.rect[1], w=self.width, h=self.height,
            minimized=self.minimized, maximized=self.maximized, dpi=self.dpi,
        )
        if mon is not None:
            ref.monitor_name = mon.name
            ref.monitor_index = mon.index
            wl, wt_, wr, wb = mon.work
            ww, wh = max(1, wr - wl), max(1, wb - wt_)
            dx, dy = self.rect[0] - wl, self.rect[1] - wt_
            ref.offset_in_work = (dx, dy)
            ref.rel_in_work = (dx / ww, dy / wh)
            ref.rel_size = (self.width / ww, self.height / wh)
        return ref


# --------------------------------------------------------------------------
# 枚举活动窗口
# --------------------------------------------------------------------------

def enumerate_live_windows(monitors: list[w32.MonitorInfo] | None = None,
                           include_minimized: bool = False) -> list[LiveWindow]:
    """
    枚举真实可用窗口。

    过滤规则来自阶段0实测（424 个顶层窗口 → 16 个真实可用）：
      · 不可见     390
      · 无标题      15
      · 工具窗口     3
    """
    mons = monitors if monitors is not None else w32.enum_monitors()
    result: list[LiveWindow] = []

    def _cb(hwnd, _lparam):
        if not w32.user32.IsWindowVisible(hwnd):
            return True

        title = w32.get_window_text(hwnd)
        if not title.strip():
            return True

        cls = w32.get_class_name(hwnd)
        if cls in w32.SHELL_CLASSES:
            return True
        if title.strip() in w32.SHELL_TITLES:
            return True

        ex_style = w32.user32.GetWindowLongW(hwnd, w32.GWL_EXSTYLE)
        if (ex_style & w32.WS_EX_TOOLWINDOW) and not (ex_style & w32.WS_EX_APPWINDOW):
            return True

        rect = w32.get_window_rect(hwnd)
        minimized = bool(w32.user32.IsIconic(hwnd))
        if w32.is_minimized_geometry(rect):
            minimized = True
        if minimized and not include_minimized:
            return True

        # 零尺寸窗口：可见、非最小化，但 GetWindowRect 的宽或高是 0。
        # 实测碰到的是 InstallShield 安装程序刚弹出时的窗口
        # （setup.exe / InstallShield_Win，rect=(1280,720,1280,720) 即 0x0）。
        # 这类窗口存进预设毫无意义（还原不出任何东西），而且会让
        # 「所有窗口都有几何」这条不变量失效，所以在这里就拦掉。
        if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
            return True

        pid = w32.get_window_pid(hwnd)
        exe = w32.get_process_path(pid)
        exe_name = exe.rsplit("\\", 1)[-1] if exe else ""

        lw = LiveWindow(
            hwnd=hwnd, title=title, cls=cls, exe=exe, exe_name=exe_name, pid=pid,
            rect=rect, minimized=minimized,
            maximized=bool(w32.user32.IsZoomed(hwnd)),
            dpi=w32.get_window_dpi(hwnd),
        )
        mon = w32.monitor_for_rect(rect, mons)
        if mon is not None:
            lw.monitor_name = mon.name
            lw.monitor_index = mon.index
        result.append(lw)
        return True

    w32.user32.EnumWindows(w32.WNDENUMPROC(_cb), 0)
    return result


# --------------------------------------------------------------------------
# 匹配打分
# --------------------------------------------------------------------------

# 各级权重。总和 100，低于阈值视为不可信。
W_EXE = 45
W_CLS = 20
W_TITLE_EXACT = 30
W_TITLE_FUZZY = 18
W_GEOMETRY = 12

MATCH_THRESHOLD = 55          # 低于这个分数不认为是同一个窗口
STRONG_MATCH = 85


@dataclass
class MatchResult:
    ref: WindowRef
    window: LiveWindow | None
    score: int
    reasons: list[str] = field(default_factory=list)
    ambiguous: bool = False
    candidates: list[tuple[LiveWindow, int]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.window is not None and self.score >= MATCH_THRESHOLD


def _geometry_similarity(ref: WindowRef, live: LiveWindow) -> int:
    """
    几何相似度 0~100。
    位置权重高于尺寸——因为用户调整布局时通常只挪位置。
    """
    if ref.w <= 0 or ref.h <= 0:
        return 0
    pos_penalty = abs(live.rect[0] - ref.x) + abs(live.rect[1] - ref.y)
    size_penalty = abs(live.width - ref.w) + abs(live.height - ref.h)

    # 位置：完全一致 0 分罚，偏差 1000 像素以上归零
    pos_score = max(0.0, 1.0 - pos_penalty / 1200.0)
    # 尺寸：相对偏差
    size_score = max(0.0, 1.0 - size_penalty / max(1, ref.w + ref.h) * 1.5)
    return int(round((pos_score * 0.55 + size_score * 0.45) * 100))


def score_match(ref: WindowRef, live: LiveWindow,
                monitors: list[w32.MonitorInfo] | None = None) -> tuple[int, list[str]]:
    """给一个候选窗口打分，返回 (分数, 理由列表)。"""
    score = 0
    reasons: list[str] = []

    # --- 进程 ---
    if ref.exe and live.exe and ref.exe.lower() == live.exe.lower():
        score += W_EXE
        reasons.append(f"进程路径完全一致(+{W_EXE})")
    elif ref.exe_name and live.exe_name and ref.exe_name.lower() == live.exe_name.lower():
        score += W_EXE - 3
        reasons.append(f"进程名一致(+{W_EXE - 3})")
    elif ref.exe_name:
        # 进程名不同 = 基本不可能是同一个窗口
        return (0, ["进程名不匹配"])

    # --- 类名 ---
    if ref.cls and live.cls:
        if ref.cls == live.cls:
            score += W_CLS
            reasons.append(f"类名一致(+{W_CLS})")
        else:
            reasons.append("类名不同")
            score -= 10

    # --- 标题 ---
    if ref.title_fp and live.title:
        live_fp = title_fingerprint(live.title)
        if ref.title and ref.title == live.title:
            score += W_TITLE_EXACT
            reasons.append(f"标题完全一致(+{W_TITLE_EXACT})")
        elif live_fp and ref.title_fp and live_fp == ref.title_fp:
            score += W_TITLE_FUZZY
            reasons.append(f"标题主干一致(+{W_TITLE_FUZZY})")
        else:
            # 标题开头重合度（应对标题后半段变化）
            a, b = ref.title_fp.lower(), live_fp.lower()
            common = 0
            for ca, cb in zip(a, b):
                if ca != cb:
                    break
                common += 1
            if common >= 6:
                part = int(W_TITLE_FUZZY * min(1.0, common / max(1, len(a))))
                score += part
                reasons.append(f"标题前缀重合 {common} 字(+{part})")
            else:
                reasons.append("标题不匹配")

    # --- 几何 ---
    gs = _geometry_similarity(ref, live)
    gpts = int(W_GEOMETRY * gs / 100)
    if gpts:
        score += gpts
        reasons.append(f"几何相似度 {gs}%(+{gpts})")

    return (max(0, min(100, score)), reasons)


def find_best_match(ref: WindowRef,
                    windows: Iterable[LiveWindow],
                    monitors: list[w32.MonitorInfo] | None = None) -> MatchResult:
    """
    在候选窗口里为 ref 找最佳匹配。

    阶段0的现实约束：存在「进程名+类名+标题」全同的孪生窗口，
    此时靠几何相似度做最后仲裁，并标记 ambiguous 让 UI 提示用户。
    """
    scored: list[tuple[LiveWindow, int, list[str]]] = []
    for lw in windows:
        s, why = score_match(ref, lw, monitors)
        if s > 0:
            scored.append((lw, s, why))

    if not scored:
        return MatchResult(ref=ref, window=None, score=0,
                           reasons=["当前没有任何窗口匹配此指纹"])

    scored.sort(key=lambda t: -t[1])
    best_lw, best_score, best_why = scored[0]

    # 检查是否歧义：第二名分数接近，说明分不开
    ambiguous = False
    if len(scored) > 1:
        second_score = scored[1][1]
        if second_score >= best_score - 6 and second_score >= MATCH_THRESHOLD:
            ambiguous = True
            best_why = best_why + [f"⚠️ 存在同分候选（{second_score} 分），已按几何相似度择优选一个"]

    return MatchResult(
        ref=ref,
        window=best_lw if best_score >= MATCH_THRESHOLD else None,
        score=best_score,
        reasons=best_why,
        ambiguous=ambiguous,
        candidates=[(lw, s) for lw, s, _ in scored[:5]],
    )


def match_preset(refs: list[WindowRef],
                 windows: list[LiveWindow] | None = None,
                 monitors: list[w32.MonitorInfo] | None = None) -> list[MatchResult]:
    """
    为一整组指纹做匹配。

    关键：已匹配的窗口会被「消耗」，避免一个真实窗口被两个指纹抢走。
    """
    mons = monitors if monitors is not None else w32.enum_monitors()
    pool = list(windows) if windows is not None else enumerate_live_windows(mons)
    remaining = list(pool)
    results: list[MatchResult] = []

    # 先匹配「特征最独特」的指纹，即标题最长的，减少抢占
    order = sorted(range(len(refs)),
                   key=lambda i: (-len(refs[i].title_fp or ""), -len(refs[i].cls or "")))

    out: list[MatchResult | None] = [None] * len(refs)
    for i in order:
        ref = refs[i]
        r = find_best_match(ref, remaining, mons)
        out[i] = r
        if r.window is not None:
            remaining = [w for w in remaining if w.hwnd != r.window.hwnd]

    for r in out:
        if r is not None:
            results.append(r)
    return results
