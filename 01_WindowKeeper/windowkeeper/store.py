# -*- coding: utf-8 -*-
"""
预设的存储与几何解析。

阶段0探针结论 6 决定了存储模型：
    本机三块屏的原点分别是 (0,0) / (23,-1600) / (-1440,-834)，
    且含负坐标。绝对坐标一旦换了显示器配置就全废。

因此每个窗口同时存四种表示，还原时按可用性逐级回退：
    1. 所属显示器设备名 + 绝对物理坐标        —— 最精确，配置没变时用
    2. 所属显示器设备名 + 相对工作区偏移       —— 分辨率变了但屏还在时用
    3. 相对工作区的归一化比例                  —— 显示器尺寸大幅变化时用
    4. 显示器的「序号」+ 主屏回退              —— 设备名对不上时用
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import win32_api as w32
from .matcher import WindowRef


SCHEMA_VERSION = 1
APP_DIR_NAME = "WindowKeeper"


def default_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / APP_DIR_NAME


# --------------------------------------------------------------------------
# 预设
# --------------------------------------------------------------------------

@dataclass
class Preset:
    id: str = ""
    name: str = "未命名"
    hotkey: str = ""                     # 形如 "ctrl+alt+1"
    windows: list[WindowRef] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    # 保存时的显示器配置快照，用于判断「配置是否变过」
    monitor_snapshot: list[dict[str, Any]] = field(default_factory=list)
    notes: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "hotkey": self.hotkey,
            "windows": [w.to_dict() for w in self.windows],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "monitor_snapshot": self.monitor_snapshot,
            "notes": self.notes,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Preset":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", "未命名"),
            hotkey=d.get("hotkey", ""),
            windows=[WindowRef.from_dict(w) for w in d.get("windows", [])],
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
            monitor_snapshot=d.get("monitor_snapshot", []),
            notes=d.get("notes", ""),
            enabled=d.get("enabled", True),
        )

    @property
    def window_count(self) -> int:
        return len(self.windows)


def snapshot_monitors() -> list[dict[str, Any]]:
    """
    把当前显示器配置记下来，写进预设。

    这份快照有两个用途：
      1. monitors_changed() 判断配置有没有变，好给用户提示
      2. resolve_geometry() 判断某块屏的几何有没有真的变，
         从而决定用绝对坐标还是按比例换算

    ★ 为什么必须有第 2 个用途：
      曾经用「窗口是否完整落在工作区内」来推断配置变化，结果误判。
      msedge 的窗口是 (-8,-8) 2576x1408，比工作区外扩 8px
      （窗口阴影、无边框最大化都会造成这种正常外溢），
      就被当成了「显示器配置变了」，位置被多挪了一次。
    """
    return [
        {"name": m.name, "index": m.index, "rect": list(m.rect),
         "work": list(m.work), "dpi": m.dpi, "primary": m.primary}
        for m in w32.enum_monitors()
    ]


def _rect_map_from_snapshot(
        snapshot: list[dict[str, Any]] | None
) -> dict[str, tuple[int, int, int, int]]:
    """把预设里存的显示器快照，转成「设备名 → 矩形」的查表。"""
    out: dict[str, tuple[int, int, int, int]] = {}
    for item in snapshot or []:
        r = item.get("rect")
        name = item.get("name")
        if name and isinstance(r, (list, tuple)) and len(r) == 4:
            out[str(name)] = (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
    return out


def monitors_changed(snapshot: list[dict[str, Any]]) -> bool:
    """当前显示器配置是否与保存时不同。"""
    if not snapshot:
        return False
    cur = snapshot_monitors()
    if len(cur) != len(snapshot):
        return True
    cur_by_name = {m["name"]: m for m in cur}
    for old in snapshot:
        now = cur_by_name.get(old.get("name", ""))
        if now is None:
            return True
        if list(old.get("rect", [])) != now["rect"] or old.get("dpi") != now["dpi"]:
            return True
    return False


# --------------------------------------------------------------------------
# 几何解析：把存储的几何还原成当前配置下的绝对坐标
# --------------------------------------------------------------------------

@dataclass
class ResolvedGeometry:
    x: int
    y: int
    w: int
    h: int
    strategy: str          # 用了哪一级回退
    exact: bool            # 是否精确（配置未变）
    warning: str = ""


def resolve_geometry(ref: WindowRef,
                     monitors: list[w32.MonitorInfo] | None = None,
                     saved_snapshot: list[dict[str, Any]] | None = None
                     ) -> ResolvedGeometry:
    """
    把预设里的几何换算成当前显示器配置下的物理坐标。

    saved_snapshot 是这个预设保存时的显示器配置。传了它，才能准确判断
    某块屏的几何到底有没有变；不传则一律按「未变」处理（保守但正确：
    宁可用绝对坐标，也不要无端把窗口挪走）。
    """
    mons = monitors if monitors is not None else w32.enum_monitors()
    if not mons:
        return ResolvedGeometry(ref.x, ref.y, ref.w, ref.h, "无显示器信息", False,
                                "系统未报告任何显示器")

    saved_rects = _rect_map_from_snapshot(saved_snapshot)

    # 1) 按设备名找屏，且这块屏的矩形与保存时一致 → 直接用绝对坐标
    mon = w32.find_monitor_by_name(ref.monitor_name, mons) if ref.monitor_name else None

    if mon is not None:
        wl, wt_, wr, wb = mon.work
        ww, wh = max(1, wr - wl), max(1, wb - wt_)

        # ★ 怎么判断「显示器配置有没有变」？
        #   曾经用「窗口是否完整落在工作区内」来判断，结果误判：
        #   msedge 的窗口是 (-8,-8) 2576x1408，比工作区外扩了 8px
        #   （窗口阴影/无边框最大化都会造成这种略微外溢），
        #   于是被当成「工作区变了」，位置被无端按比例挪了一次。
        #
        #   正确做法：拿保存时的显示器快照，比这块屏自己的矩形。
        #   窗口溢不溢出工作区，和显示器配置变没变，是两件不相干的事。
        saved_rect = saved_rects.get(mon.name)
        if saved_rect is None or saved_rect == mon.rect:
            return ResolvedGeometry(ref.x, ref.y, ref.w, ref.h,
                                    "显示器未变，绝对坐标", True)

        # 屏还在但几何真的变了 → 用偏移 + 归一化
        dx, dy = ref.offset_in_work
        nx = wl + dx
        ny = wt_ + dy
        if not (wl <= nx and nx + ref.w <= wr and wt_ <= ny and ny + ref.h <= wb):
            nx = int(wl + ref.rel_in_work[0] * ww)
            ny = int(wt_ + ref.rel_in_work[1] * wh)
        return ResolvedGeometry(
            nx, ny, ref.w, ref.h,
            f"显示器 {mon.name} 几何已变化，按偏移换算",
            False,
            f"显示器 {mon.name} 的分辨率与保存时不同，位置已按比例调整")

    # 2) 设备名对不上 → 按序号找
    if 0 <= ref.monitor_index < len(mons):
        m = mons[ref.monitor_index]
        wl, wt_, wr, wb = m.work
        ww, wh = max(1, wr - wl), max(1, wb - wt_)
        nx = int(wl + ref.rel_in_work[0] * ww)
        ny = int(wt_ + ref.rel_in_work[1] * wh)
        nw = int(ref.rel_size[0] * ww) or ref.w
        nh = int(ref.rel_size[1] * wh) or ref.h
        return ResolvedGeometry(
            nx, ny, nw, nh,
            f"按显示器序号 {ref.monitor_index} 回退",
            False,
            f"找不到显示器 {ref.monitor_name or '(未记录)'}，已按序号回退到 {m.name}")

    # 3) 全都不行 → 主屏 + 归一化
    primary = next((m for m in mons if m.primary), mons[0])
    wl, wt_, wr, wb = primary.work
    ww, wh = max(1, wr - wl), max(1, wb - wt_)
    nx = int(wl + ref.rel_in_work[0] * ww)
    ny = int(wt_ + ref.rel_in_work[1] * wh)
    nw = int(ref.rel_size[0] * ww) or ref.w
    nh = int(ref.rel_size[1] * wh) or ref.h
    return ResolvedGeometry(
        nx, ny, nw, nh, "回退到主显示器", False,
        f"显示器 {ref.monitor_name or '(未记录)'} 已不存在，已放到主显示器上")


def clamp_to_monitors(x: int, y: int, w: int, h: int,
                      monitors: list[w32.MonitorInfo] | None = None,
                      min_visible: int = 80) -> tuple[int, int, int, int, str]:
    """
    保证窗口至少有 min_visible 像素落在某块屏上，否则用户看不到也点不着。
    返回 (x, y, w, h, 说明)。
    """
    mons = monitors if monitors is not None else w32.enum_monitors()
    if not mons:
        return (x, y, w, h, "")

    # 尺寸先夹到最大屏内
    max_w = max(m.width for m in mons)
    max_h = max(m.height for m in mons)
    nw, nh = w, h
    note = ""
    if nw > max_w or nh > max_h:
        nw, nh = min(nw, max_w), min(nh, max_h)
        note = "窗口尺寸超过最大显示器，已缩小"

    for m in mons:
        ml, mt, mr, mb = m.rect
        vis_x = min(x + nw, mr) - max(x, ml)
        vis_y = min(y + nh, mb) - max(y, mt)
        if vis_x >= min_visible and vis_y >= min_visible:
            return (x, y, nw, nh, note)

    # 完全不可见 → 挪到主屏工作区居中
    primary = next((m for m in mons if m.primary), mons[0])
    wl, wt_, wr, wb = primary.work
    nx = wl + max(0, ((wr - wl) - nw) // 2)
    ny = wt_ + max(0, ((wb - wt_) - nh) // 2)
    return (nx, ny, nw, nh,
            (note + "；" if note else "") + "原位置已不在任何显示器上，已移到主显示器")


# --------------------------------------------------------------------------
# 仓库
# --------------------------------------------------------------------------

class PresetStore:
    """预设的持久化。原子写入 + schema 版本号。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.dir = Path(data_dir) if data_dir else default_data_dir()
        self.path = self.dir / "presets.json"
        self.presets: list[Preset] = []
        self.load_error: str = ""

    # -- 读写 --
    def load(self) -> list[Preset]:
        self.load_error = ""
        if not self.path.exists():
            self.presets = []
            return self.presets
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"预设文件损坏，已忽略：{exc}"
            self.presets = []
            return self.presets

        version = raw.get("version", 0)
        if version > SCHEMA_VERSION:
            self.load_error = (f"预设文件版本 {version} 高于本程序支持的 "
                               f"{SCHEMA_VERSION}，可能有内容无法识别")
        self.presets = [Preset.from_dict(p) for p in raw.get("presets", [])]
        return self.presets

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "app": APP_DIR_NAME,
            "saved_at": time.time(),
            "presets": [p.to_dict() for p in self.presets],
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # -- 增删改查 --
    def get(self, preset_id: str) -> Preset | None:
        return next((p for p in self.presets if p.id == preset_id), None)

    def by_name(self, name: str) -> Preset | None:
        return next((p for p in self.presets if p.name == name), None)

    def add(self, preset: Preset) -> Preset:
        self.presets.append(preset)
        self.save()
        return preset

    def remove(self, preset_id: str) -> bool:
        before = len(self.presets)
        self.presets = [p for p in self.presets if p.id != preset_id]
        if len(self.presets) != before:
            self.save()
            return True
        return False

    def update(self, preset: Preset) -> None:
        preset.updated_at = time.time()
        for i, p in enumerate(self.presets):
            if p.id == preset.id:
                self.presets[i] = preset
                break
        else:
            self.presets.append(preset)
        self.save()

    def ensure_unique_name(self, name: str, exclude_id: str = "") -> str:
        base = name.strip() or "未命名"
        existing = {p.name for p in self.presets if p.id != exclude_id}
        if base not in existing:
            return base
        i = 2
        while f"{base} ({i})" in existing:
            i += 1
        return f"{base} ({i})"

    # -- 导入导出 --
    def export_to(self, path: Path, preset_ids: list[str] | None = None) -> int:
        items = self.presets
        if preset_ids:
            items = [p for p in self.presets if p.id in preset_ids]
        payload = {
            "version": SCHEMA_VERSION,
            "app": APP_DIR_NAME,
            "exported_at": time.time(),
            "presets": [p.to_dict() for p in items],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        return len(items)

    def import_from(self, path: Path) -> tuple[int, list[str]]:
        """返回 (导入数量, 警告列表)。同名预设自动改名，不覆盖。"""
        warnings: list[str] = []
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            return (0, [f"读取失败：{exc}"])

        count = 0
        for d in raw.get("presets", []):
            try:
                p = Preset.from_dict(d)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"跳过一条无法解析的预设：{exc}")
                continue
            p.id = uuid.uuid4().hex[:12]
            new_name = self.ensure_unique_name(p.name)
            if new_name != p.name:
                warnings.append(f"「{p.name}」已存在，导入为「{new_name}」")
            p.name = new_name
            self.presets.append(p)
            count += 1
        if count:
            self.save()
        return (count, warnings)
