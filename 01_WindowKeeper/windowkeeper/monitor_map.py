# -*- coding: utf-8 -*-
"""
显示器布局缩略图控件。

为什么值得单独做一个控件：
    多屏用户最怕的就是「还原之后窗口跑到看不见的地方去了」。
    一张按真实相对位置画出来的缩略图，能让人一眼看出「哦这是要摆到副屏上」，
    比任何文字描述都直观。

坐标处理：
    显示器物理坐标含负值（本机有 (23,-1600) 和 (-1440,-834)），
    所以先把所有矩形并成一个外接框，再整体平移映射到控件内。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter,
                           QPainterPath, QPen)
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import win32_api as w32


# 配色：暗色底 + 蓝色高亮，和整体界面一致
BG = QColor("#141821")
MON_FILL = QColor("#1E2530")
MON_BORDER = QColor("#39424F")
MON_PRIMARY = QColor("#5B8DEF")
WIN_FILL = QColor("#3D7EFF")
WIN_FILL_SOFT = QColor("#3D7EFF")
WIN_BORDER = QColor("#8FB6FF")
WIN_GHOST = QColor("#7A8493")
TEXT = QColor("#C9D1DC")
TEXT_DIM = QColor("#7A8493")


class MonitorMap(QWidget):
    """
    画出显示器布局，可选地叠加一组窗口矩形。

    rects 里的每个元素是 (x, y, w, h, label, kind)，
    kind 决定颜色：'win' 正常、'ghost' 灰色（未匹配到）。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._monitors: list[w32.MonitorInfo] = []
        self._rects: list[tuple[int, int, int, int, str, str]] = []
        self.show_labels = True
        self._hover_index = -1

    # -- 数据 --
    def set_monitors(self, monitors: list[w32.MonitorInfo]) -> None:
        self._monitors = list(monitors or [])
        self.update()

    def set_rects(self, rects: list[tuple[int, int, int, int, str, str]]) -> None:
        self._rects = list(rects or [])
        self.update()

    def clear_rects(self) -> None:
        self._rects = []
        self.update()

    def set_show_labels(self, on: bool) -> None:
        self.show_labels = bool(on)
        self.update()

    # -- 坐标映射 --
    def _bounds(self) -> QRectF | None:
        """把所有显示器和窗口并成一个外接框。"""
        boxes = []
        for m in self._monitors:
            boxes.append(m.rect)
        for x, y, w, h, _label, _kind in self._rects:
            boxes.append((x, y, x + w, y + h))
        if not boxes:
            return None
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[2] for b in boxes)
        bottom = max(b[3] for b in boxes)
        if right <= left or bottom <= top:
            return None
        return QRectF(left, top, right - left, bottom - top)

    def _scale(self) -> tuple[float, QPointF, QRectF] | None:
        b = self._bounds()
        if b is None:
            return None
        pad = 14
        avail_w = max(1.0, self.width() - pad * 2)
        avail_h = max(1.0, self.height() - pad * 2)
        # 等比缩放，留出边距
        s = min(avail_w / b.width(), avail_h / b.height())
        # 居中偏移
        draw_w = b.width() * s
        draw_h = b.height() * s
        ox = (self.width() - draw_w) / 2.0
        oy = (self.height() - draw_h) / 2.0
        origin = QPointF(ox - b.left() * s, oy - b.top() * s)
        return s, origin, b

    def _map_rect(self, x, y, w, h) -> QRectF:
        sc = self._scale()
        if sc is None:
            return QRectF()
        s, origin, _ = sc
        return QRectF(origin.x() + x * s, origin.y() + y * s, w * s, h * s)

    # -- 绘制 --
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # 底色
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        p.fillPath(path, BG)

        if not self._monitors:
            p.setPen(TEXT_DIM)
            p.drawText(self.rect(), Qt.AlignCenter, "没有检测到显示器")
            p.end()
            return

        # 显示器
        for m in self._monitors:
            r = self._map_rect(*m.rect)
            r = r.adjusted(1, 1, -1, -1)
            pen = QPen(MON_PRIMARY if m.primary else MON_BORDER, 1.6)
            p.setPen(pen)
            p.setBrush(QBrush(MON_FILL))
            p.drawRoundedRect(r, 5, 5)

            if self.show_labels and r.width() > 46 and r.height() > 30:
                f = QFont()
                f.setPointSizeF(8.0)
                p.setFont(f)
                p.setPen(MON_PRIMARY if m.primary else TEXT_DIM)
                tag = f"{m.index}"
                if m.primary:
                    tag += " 主"
                p.drawText(r.adjusted(6, 4, -6, 0),
                           Qt.AlignTop | Qt.AlignLeft, tag)

                if r.width() > 96 and r.height() > 60:
                    f2 = QFont()
                    f2.setPointSizeF(7.0)
                    p.setFont(f2)
                    p.setPen(TEXT_DIM)
                    info = f"{m.width}×{m.height}"
                    if abs(m.scale - 1.0) > 0.01:
                        info += f"  {m.scale * 100:.0f}%"
                    p.drawText(r.adjusted(6, 0, -6, -4),
                               Qt.AlignBottom | Qt.AlignLeft, info)

        # 窗口
        for x, y, w, h, label, kind in self._rects:
            r = self._map_rect(x, y, w, h)
            r = r.adjusted(1.5, 1.5, -1.5, -1.5)
            ghost = kind == "ghost"

            if ghost:
                p.setBrush(Qt.NoBrush)
                pen = QPen(WIN_GHOST, 1.4, Qt.DashLine)
            else:
                c = QColor(WIN_FILL)
                c.setAlpha(120)
                p.setBrush(QBrush(c))
                pen = QPen(WIN_BORDER, 1.5)
            p.setPen(pen)
            p.drawRoundedRect(r, 4, 4)

            # 窗口标签：只在够大时画，避免糊成一团
            if self.show_labels and label and r.width() > 52 and r.height() > 18:
                f = QFont()
                f.setPointSizeF(7.5)
                p.setFont(f)
                fm = QFontMetrics(f)
                txt = fm.elidedText(label, Qt.ElideRight, int(r.width()) - 10)
                p.setPen(WIN_GHOST if ghost else QColor("#DCE8FF"))
                p.drawText(r.adjusted(5, 0, -5, 0),
                           Qt.AlignVCenter | Qt.AlignLeft, txt)

        p.end()
