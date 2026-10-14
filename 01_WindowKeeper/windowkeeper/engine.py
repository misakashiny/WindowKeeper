# -*- coding: utf-8 -*-
"""
核心引擎：捕获当前布局、还原预设。

一切设计都服从阶段0探针的实测结论：
  [D1] 还原必须先位置后尺寸（跨屏时），见 win32_api.apply_geometry
  [D5] 最小化窗口的 (-32000,-32000) 哨兵值必须特判
  [D6] 每次还原后必须回读 GetWindowRect 做校验
  [D7] 不可控窗口类型要明确告知，不假装成功
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from . import win32_api as w32
from . import log
from .matcher import (MATCH_THRESHOLD, LiveWindow, MatchResult, WindowRef,
                      enumerate_live_windows, match_preset)
from .store import Preset, clamp_to_monitors, resolve_geometry, snapshot_monitors


class OutcomeKind(str, Enum):
    OK = "ok"                    # 完全按预期还原
    CONSTRAINED = "constrained"  # 位置到了，但尺寸被窗口自己的下限顶开
    ACCEPTABLE = "acceptable"    # 还原了，但有其他偏差
    NOT_FOUND = "not_found"      # 找不到对应窗口
    REFUSED = "refused"          # 窗口完全拒绝改变（如 Tk/UWP）
    ERROR = "error"              # 调用失败


# 探针实测不可控的窗口类型，用于给用户准确的心理预期
UNCONTROLLABLE_HINTS = {
    "TkTopLevel": "Tkinter 程序会自行恢复窗口尺寸，无法可靠控制",
    "ApplicationFrameWindow": "UWP 应用窗口，系统框架会拦截尺寸修改",
}


@dataclass
class WindowOutcome:
    ref: WindowRef
    kind: OutcomeKind
    hwnd: int = 0
    title: str = ""
    requested: tuple[int, int, int, int] = (0, 0, 0, 0)
    actual: tuple[int, int, int, int] = (0, 0, 0, 0)
    match_score: int = 0
    reasons: list[str] = field(default_factory=list)
    message: str = ""
    ambiguous: bool = False
    min_track: tuple[int, int] = (0, 0)

    @property
    def ok(self) -> bool:
        return self.kind in (OutcomeKind.OK, OutcomeKind.ACCEPTABLE,
                             OutcomeKind.CONSTRAINED)

    @property
    def exact(self) -> bool:
        return self.kind == OutcomeKind.OK

    @property
    def status_text(self) -> str:
        return {
            OutcomeKind.OK: "已还原",
            OutcomeKind.CONSTRAINED: "已还原（受尺寸限制）",
            OutcomeKind.ACCEPTABLE: "已还原（有偏差）",
            OutcomeKind.NOT_FOUND: "未找到",
            OutcomeKind.REFUSED: "窗口拒绝改变",
            OutcomeKind.ERROR: "失败",
        }[self.kind]


@dataclass
class ApplyReport:
    preset_name: str
    outcomes: list[WindowOutcome] = field(default_factory=list)
    monitor_warning: str = ""
    elapsed: float = 0.0
    activated: bool = False

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.kind == OutcomeKind.OK)

    @property
    def constrained_count(self) -> int:
        return sum(1 for o in self.outcomes
                   if o.kind == OutcomeKind.CONSTRAINED)

    @property
    def acceptable_count(self) -> int:
        return sum(1 for o in self.outcomes if o.kind == OutcomeKind.ACCEPTABLE)

    @property
    def failed(self) -> list[WindowOutcome]:
        return [o for o in self.outcomes
                if o.kind in (OutcomeKind.NOT_FOUND, OutcomeKind.REFUSED,
                              OutcomeKind.ERROR)]

    @property
    def touched(self) -> int:
        """被实际调整过的窗口数（含受约束的）。"""
        return sum(1 for o in self.outcomes if o.ok and o.hwnd)

    @property
    def perfect(self) -> bool:
        return self.total > 0 and self.ok_count == self.total

    def summary(self) -> str:
        if self.total == 0:
            return "没有可还原的窗口"
        parts = [f"{self.ok_count}/{self.total} 个窗口精确还原"]
        if self.constrained_count:
            parts.append(f"{self.constrained_count} 个受尺寸限制")
        if self.acceptable_count:
            parts.append(f"{self.acceptable_count} 个有偏差")
        if self.failed:
            parts.append(f"{len(self.failed)} 个失败")
        return "，".join(parts)

    def detail_lines(self) -> list[str]:
        """给 UI 用的逐窗口说明。"""
        out = []
        for o in self.outcomes:
            name = o.title or o.ref.title_fp or o.ref.exe_name or "(未知)"
            out.append(f"[{o.status_text}] {name}：{o.message}")
        return out


# --------------------------------------------------------------------------
# 捕获
# --------------------------------------------------------------------------

def capture_current_layout(include_minimized: bool = False,
                           monitors: list[w32.MonitorInfo] | None = None
                           ) -> list[WindowRef]:
    """把当前所有可用窗口的指纹与几何抓下来。"""
    mons = monitors if monitors is not None else w32.enum_monitors()
    live = enumerate_live_windows(mons, include_minimized=include_minimized)
    # 过滤掉几何异常的
    refs = [lw.to_ref(mons) for lw in live if lw.width > 0 and lw.height > 0]
    return refs


def make_preset_from_current(name: str,
                             include_minimized: bool = False) -> Preset:
    mons = w32.enum_monitors()
    refs = capture_current_layout(include_minimized=include_minimized, monitors=mons)
    return Preset(
        name=name,
        windows=refs,
        monitor_snapshot=snapshot_monitors(),
    )


# --------------------------------------------------------------------------
# 还原
# --------------------------------------------------------------------------

def _rect_of(hwnd: int) -> tuple[int, int, int, int]:
    return w32.get_window_rect(hwnd)


def _close_enough(a: tuple[int, int, int, int],
                  b: tuple[int, int, int, int],
                  tol: int = 2) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def apply_preset(preset: Preset,
                 activate: bool = True,
                 monitors: list[w32.MonitorInfo] | None = None,
                 dry_run: bool = False) -> ApplyReport:
    """
    还原一个预设。

    流程：
      1. 枚举当前窗口
      2. 对每个指纹做匹配（已匹配的窗口不会被重复占用）
      3. 计算目标几何（考虑显示器配置变化）
      4. 两步式 SetWindowPos 还原（跨屏时）
      5. 回读校验，把结果如实分类
    """
    t0 = time.perf_counter()
    mons = monitors if monitors is not None else w32.enum_monitors()
    report = ApplyReport(preset_name=preset.name)

    log.decision("开始还原预设", preset=preset.name,
                 windows=len(preset.windows), dry_run=dry_run,
                 activate=activate, monitors=len(mons))

    if not preset.windows:
        report.monitor_warning = "这个预设里没有任何窗口"
        report.elapsed = time.perf_counter() - t0
        log.get("engine").warning("预设「%s」里没有任何窗口，无事可做", preset.name)
        return report

    # 显示器配置变化提示
    try:
        from .store import monitors_changed
        if monitors_changed(preset.monitor_snapshot):
            report.monitor_warning = "显示器配置与保存预设时不同，位置已按比例调整"
            log.get("engine").info("显示器配置与保存预设时不同，位置将按比例调整")
    except Exception as exc:  # noqa: BLE001
        log.swallow("检查显示器配置变化", exc, preset=preset.name)
    live = enumerate_live_windows(mons, include_minimized=True)
    matches = match_preset(preset.windows, live, mons)
    log.get("engine").debug("枚举到 %d 个可用窗口，完成 %d 项匹配",
                            len(live), len(matches))

    for ref, m in zip(preset.windows, matches):
        out = WindowOutcome(ref=ref, kind=OutcomeKind.NOT_FOUND,
                            match_score=m.score, reasons=list(m.reasons),
                            ambiguous=m.ambiguous)

        if m.window is None:
            out.message = "当前没有打开匹配的窗口"
            if m.reasons:
                out.message += f"（{m.reasons[-1]}）"
            report.outcomes.append(out)
            log.decision("未找到匹配窗口",
                         target=ref.exe_name or ref.title_fp,
                         score=m.score, reasons=m.reasons)
            continue

        lw = m.window
        out.hwnd = lw.hwnd
        out.title = lw.title

        if dry_run:
            geo = resolve_geometry(ref, mons, preset.monitor_snapshot)
            out.requested = (geo.x, geo.y, geo.w, geo.h)
            out.kind = OutcomeKind.OK
            out.message = f"[预演] 将还原至 {geo.strategy}"
            report.outcomes.append(out)
            continue

        # --- 计算目标几何 ---
        geo = resolve_geometry(ref, mons, preset.monitor_snapshot)
        x, y, w, h, clamp_note = clamp_to_monitors(geo.x, geo.y, geo.w, geo.h, mons)
        out.requested = (x, y, w, h)

        # --- 还原窗口状态 ---
        try:
            w32.restore_if_minimized(lw.hwnd)
            w32.unmaximize(lw.hwnd)
        except Exception as exc:  # noqa: BLE001
            log.swallow("恢复窗口状态（取消最小化/最大化）", exc,
                        hwnd=lw.hwnd, exe=lw.exe_name)

        # --- 查询这个窗口自己允许的最小尺寸 ---
        # ★ 实测（probe_size_constraint.py）：SetWindowPos 返回成功 ≠ 尺寸生效。
        #   窗口可以通过 WM_GETMINMAXINFO 合法声明下限，系统会把它顶到下限。
        #   位置没有这种约束，所以位置永远精确。先问清楚，才能正确分类结果。
        try:
            min_track = w32.get_min_track_size(lw.hwnd)
        except Exception as exc:  # noqa: BLE001
            min_track = (0, 0)
            log.swallow("查询窗口最小尺寸约束", exc, hwnd=lw.hwnd, exe=lw.exe_name)
        out.min_track = min_track

        # --- 应用几何（跨屏自动走两步协议）---
        try:
            geo_res = w32.apply_geometry_verified(
                lw.hwnd, x, y, w, h, mons, min_track=min_track)
        except Exception as exc:  # noqa: BLE001
            out.kind = OutcomeKind.ERROR
            out.message = f"调用失败：{exc!r}"
            report.outcomes.append(out)
            log.exception(f"移动窗口失败 hwnd={lw.hwnd} ({lw.exe_name})")
            continue

        # --- 回读校验（结论 D6：绝不静默失败）---
        actual = geo_res.actual
        out.actual = actual
        aw, ah = actual[2] - actual[0], actual[3] - actual[1]

        if geo_res.exact:
            out.kind = OutcomeKind.OK
            out.message = "精确还原" if geo.exact else f"已还原（{geo.strategy}）"
        else:
            size_off = abs(aw - w) > 2 or abs(ah - h) > 2
            pos_off = abs(actual[0] - x) > 2 or abs(actual[1] - y) > 2
            hint = UNCONTROLLABLE_HINTS.get(lw.cls, "")

            # ★ 主要情形：位置到位，尺寸被顶到窗口自报的下限。
            #   这是窗口的合法权利，不是失败，要如实告诉用户「已经尽量小了」。
            hit_floor = (
                not pos_off and size_off
                and min_track[0] > 0 and min_track[1] > 0
                and aw >= min_track[0] - 2 and ah >= min_track[1] - 2
                and (aw > w or ah > h)
            )
            if hit_floor:
                out.kind = OutcomeKind.CONSTRAINED
                out.message = (f"已还原，但该程序限制最小窗口为 "
                               f"{min_track[0]}x{min_track[1]}，"
                               f"无法缩到 {w}x{h}")
            elif size_off and not pos_off and (aw <= w and ah <= h):
                out.kind = OutcomeKind.ACCEPTABLE
                out.message = f"尺寸偏小：期望 {w}x{h}，实际 {aw}x{ah}"
                if hint:
                    out.message += f"（{hint}）"
            elif size_off:
                out.kind = OutcomeKind.ACCEPTABLE
                out.message = f"尺寸有偏差：期望 {w}x{h}，实际 {aw}x{ah}"
                if hint:
                    out.message += f"（{hint}）"
            elif pos_off:
                out.kind = OutcomeKind.ACCEPTABLE
                out.message = (f"位置有偏差：期望 ({x},{y})，"
                               f"实际 ({actual[0]},{actual[1]})")
            else:
                out.kind = OutcomeKind.ACCEPTABLE
                out.message = "已还原，但有轻微偏差"

            if not all(geo_res.calls_ok):
                out.message += f"（SetWindowPos 返回 {geo_res.calls_ok}）"

        if clamp_note:
            out.message += f"；{clamp_note}"
        if geo.warning:
            out.message += f"；{geo.warning}" if out.message else geo.warning
        if out.ambiguous:
            out.message += "；存在孪生窗口，可能匹配到了另一个"

        report.outcomes.append(out)

        # ★ 每一个窗口的最终判定都记一条，含「期望 vs 实际」。
        #   出问题时这张表就是全部证据，不用再去猜引擎当时在想什么。
        log.decision(
            f"{out.status_text}：{lw.exe_name or lw.cls}",
            kind=out.kind.value,
            exe=lw.exe_name,
            cls=lw.cls,
            hwnd=lw.hwnd,
            requested=f"{x},{y} {w}x{h}",
            actual=f"{actual[0]},{actual[1]} {aw}x{ah}",
            min_track=f"{min_track[0]}x{min_track[1]}",
            score=m.score,
            ambiguous=out.ambiguous,
            strategy=geo.strategy,
            cross_monitor=geo_res.cross_monitor,
        )

    # --- 可选：把第一个成功窗口带到前台，让用户看到变化 ---
    if activate and not dry_run:
        for o in report.outcomes:
            if o.ok and o.hwnd:
                report.activated = w32.activate_window(o.hwnd)
                break

    report.elapsed = time.perf_counter() - t0

    # 收尾汇总：这一条让日志能独立还原出「这次还原到底成不成」
    log.get("engine").info(
        "还原「%s」结束：%d/%d 精确，%d 受限，%d 有偏差，%d 失败，耗时 %.0fms",
        preset.name, report.ok_count, report.total,
        report.constrained_count, report.acceptable_count,
        len(report.failed), report.elapsed * 1000)

    return report


# --------------------------------------------------------------------------
# 预览（给 UI 画缩略图用）
# --------------------------------------------------------------------------

@dataclass
class PreviewItem:
    ref: WindowRef
    matched: bool
    score: int
    hwnd: int
    will_move: bool
    label: str


def preview_preset(preset: Preset,
                   monitors: list[w32.MonitorInfo] | None = None) -> list[PreviewItem]:
    """不实际改动窗口，只算「哪些能匹配上、会移到哪」。"""
    mons = monitors if monitors is not None else w32.enum_monitors()
    live = enumerate_live_windows(mons, include_minimized=True)
    matches = match_preset(preset.windows, live, mons)
    items: list[PreviewItem] = []
    for ref, m in zip(preset.windows, matches):
        label = ref.title_fp or ref.exe_name or ref.cls or "(未知窗口)"
        items.append(PreviewItem(
            ref=ref,
            matched=m.window is not None,
            score=m.score,
            hwnd=m.window.hwnd if m.window else 0,
            will_move=True,
            label=label,
        ))
    return items
