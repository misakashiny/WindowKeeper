# -*- coding: utf-8 -*-
"""
WindowKeeper 主界面。

设计取向（用户明确要求「美观简洁」）：
    · 单窗口、左右分栏：左边预设列表，右边预览与操作
    · 不用菜单栏，操作都放在看得见的地方
    · 深色主题，和缩略图配色一致
    · 所有危险操作（删除）都要二次确认
    · 还原前可以先「预演」，把结果画在缩略图上

界面只做「展示与转发」，真正的窗口操作全部交给 engine，
这样界面出错也不会把用户布局弄乱。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox,
                               QComboBox, QDialog, QDialogButtonBox,
                               QFrame, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QSizePolicy, QSplitter,
                               QStatusBar, QTabWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from . import __version__, engine, log, store
from . import win32_api as w32
from .monitor_map import MonitorMap
from .tray import HotkeyWatcher, HotkeyError, make_icon, parse_hotkey


# --------------------------------------------------------------------------
# 样式表
# --------------------------------------------------------------------------

STYLE = """
QWidget {
    background: #0F1218;
    color: #C9D1DC;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QLabel#title {
    font-size: 19px;
    font-weight: 600;
    color: #E8EEF7;
}
QLabel#subtitle {
    color: #7A8493;
    font-size: 12px;
}
QLabel#sectionLabel {
    color: #8FB6FF;
    font-size: 12px;
    font-weight: 600;
    padding: 2px 0;
}
QListWidget {
    background: #141821;
    border: 1px solid #232A35;
    border-radius: 8px;
    padding: 5px;
    outline: none;
}
QListWidget::item {
    padding: 9px 11px;
    border-radius: 6px;
    margin: 2px 0;
}
QListWidget::item:selected {
    background: #22314D;
    color: #E8EEF7;
}
QListWidget::item:hover:!selected {
    background: #1B2230;
}
QPushButton {
    background: #232A35;
    border: 1px solid #333C4A;
    border-radius: 6px;
    padding: 7px 15px;
    color: #D5DDE8;
}
QPushButton:hover  { background: #2C3543; border-color: #44506A; }
QPushButton:pressed{ background: #1E2530; }
QPushButton:disabled { color: #55606E; background: #1A1F28; border-color: #262D38; }
QPushButton#primary {
    background: #2F6FE4;
    border: 1px solid #4C8DFF;
    color: #FFFFFF;
    font-weight: 600;
    padding: 8px 20px;
}
QPushButton#primary:hover   { background: #3D7EFF; }
QPushButton#primary:pressed { background: #275BC0; }
QPushButton#danger { color: #FF9B9B; border-color: #5A3030; }
QPushButton#danger:hover { background: #3A2020; }
QLineEdit, QComboBox {
    background: #141821;
    border: 1px solid #2A323F;
    border-radius: 6px;
    padding: 7px 10px;
    selection-background-color: #2F6FE4;
}
QLineEdit:focus, QComboBox:focus { border-color: #4C8DFF; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView {
    background: #1A1F28;
    border: 1px solid #333C4A;
    selection-background-color: #22314D;
}
QTableWidget {
    background: #141821;
    border: 1px solid #232A35;
    border-radius: 8px;
    gridline-color: #1E2530;
    outline: none;
}
QHeaderView::section {
    background: #1A1F28;
    color: #8FB6FF;
    border: none;
    border-bottom: 1px solid #232A35;
    padding: 7px 9px;
    font-size: 12px;
}
QTableWidget::item { padding: 5px 7px; }
QTableWidget::item:selected { background: #22314D; }
QTabWidget::pane {
    border: 1px solid #232A35;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: #7A8493;
    padding: 8px 17px;
    border: none;
}
QTabBar::tab:selected {
    color: #8FB6FF;
    border-bottom: 2px solid #4C8DFF;
}
QTabBar::tab:hover:!selected { color: #A8B4C4; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator {
    width: 15px; height: 15px;
    border: 1px solid #3A4351;
    border-radius: 4px;
    background: #141821;
}
QCheckBox::indicator:checked {
    background: #2F6FE4;
    border-color: #4C8DFF;
}
QSplitter::handle { background: transparent; width: 10px; }
QStatusBar { color: #7A8493; }
QStatusBar::item { border: none; }
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #2C3543; border-radius: 5px; min-height: 26px;
}
QScrollBar::handle:vertical:hover { background: #3C4757; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal {
    background: transparent; height: 10px; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #2C3543; border-radius: 5px; min-width: 26px;
}
"""


# --------------------------------------------------------------------------
# 热键编辑对话框
# --------------------------------------------------------------------------

class HotkeyDialog(QDialog):
    """让用户按组合键，而不是手打字符串。手打太容易写错。"""

    MODS = ["ctrl", "alt", "shift", "win"]

    def __init__(self, current: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置热键")
        self.setMinimumWidth(400)
        self.result_text = current
        self._mods: set[str] = set()
        self._key: str = ""

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        tip = QLabel("在下面的输入框里直接按下你想用的组合键。\n"
                     "至少要带一个修饰键（Ctrl / Alt / Shift / Win）。")
        tip.setWordWrap(True)
        tip.setObjectName("subtitle")
        lay.addWidget(tip)

        self.capture = QLineEdit()
        self.capture.setPlaceholderText("点这里，然后按下组合键…")
        self.capture.setReadOnly(True)
        self.capture.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSizeF(13.0)
        self.capture.setFont(f)
        self.capture.installEventFilter(self)
        lay.addWidget(self.capture)

        if current:
            self.capture.setText(current)

        warn = QLabel("")
        warn.setObjectName("subtitle")
        warn.setWordWrap(True)
        self.warn = warn
        lay.addWidget(warn)

        row = QHBoxLayout()
        clear = QPushButton("清除热键")
        clear.clicked.connect(self._clear)
        row.addWidget(clear)
        row.addStretch(1)
        lay.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("确定")
        bb.button(QDialogButtonBox.Cancel).setText("取消")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _clear(self) -> None:
        self.result_text = ""
        self.capture.clear()
        self.warn.setText("")

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.capture and event.type() == event.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            names = []
            if mods & Qt.ControlModifier:
                names.append("ctrl")
            if mods & Qt.AltModifier:
                names.append("alt")
            if mods & Qt.ShiftModifier:
                names.append("shift")
            if mods & Qt.MetaModifier:
                names.append("win")

            name = self._key_name(key)
            if name == "MOD":
                # 只按了修饰键，先把修饰键显示出来
                if names:
                    self.capture.setText("+".join(names) + "+…")
                return True
            if name:
                if not names:
                    self.warn.setText("⚠ 必须带一个修饰键，否则会抢走全局按键")
                    self.capture.setText(name)
                else:
                    text = "+".join(names) + "+" + name
                    try:
                        parse_hotkey(text)
                        self.warn.setText("")
                        self.capture.setText(text)
                        self.result_text = text
                    except HotkeyError as exc:
                        self.warn.setText(f"⚠ {exc}")
                return True
        return super().eventFilter(obj, event)

    @staticmethod
    def _key_name(key: int) -> str:
        special = {
            Qt.Key_Space: "space", Qt.Key_Tab: "tab", Qt.Key_Return: "enter",
            Qt.Key_Enter: "enter", Qt.Key_Escape: "esc",
            Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete",
            Qt.Key_Insert: "insert", Qt.Key_Home: "home", Qt.Key_End: "end",
            Qt.Key_PageUp: "pageup", Qt.Key_PageDown: "pagedown",
            Qt.Key_Left: "left", Qt.Key_Up: "up",
            Qt.Key_Right: "right", Qt.Key_Down: "down",
        }
        if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta):
            return "MOD"
        if key in special:
            return special[key]
        if Qt.Key_F1 <= key <= Qt.Key_F24:
            return f"f{key - Qt.Key_F1 + 1}"
        if Qt.Key_A <= key <= Qt.Key_Z:
            return chr(key).lower()
        if Qt.Key_0 <= key <= Qt.Key_9:
            return chr(key)
        # 符号键走 text()
        return ""

    def _accept(self) -> None:
        if not self.capture.text() or self.capture.text().endswith("…"):
            self.warn.setText("⚠ 还没输入完整的组合键")
            return
        if not self.result_text:
            self.warn.setText("⚠ 还没输入完整的组合键")
            return
        try:
            parse_hotkey(self.result_text)
        except HotkeyError as exc:
            self.warn.setText(f"⚠ {exc}")
            return
        self.accept()


# --------------------------------------------------------------------------
# 主窗口
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, tray: "object | None" = None) -> None:
        super().__init__()
        self.tray_app = tray
        self.store = store.PresetStore()
        self.store.load()
        # ★ 必须先于 reload_presets 初始化：
        #   reload_presets 会选中第一项并触发 _on_select → _show_detail，
        #   那里要用 self.monitors 画缩略图。
        self.monitors: list[w32.MonitorInfo] = w32.enum_monitors()
        self.watcher = HotkeyWatcher(self)
        self.watcher.triggered.connect(self._on_hotkey)

        self.setWindowTitle(f"WindowKeeper {__version__}")
        self.setWindowIcon(make_icon())
        self.resize(1140, 720)
        self.setMinimumSize(QSize(920, 580))

        self._build_ui()
        self.reload_presets()
        self.refresh_monitors()
        self._install_shortcuts()

        # 显示器热插拔不会发通知给普通窗口，只能定时比对
        self._mon_timer = QTimer(self)
        self._mon_timer.timeout.connect(self._check_monitors)
        self._mon_timer.start(2500)
        self._mon_sig = self._monitor_signature()

    # ---------------- 界面搭建 ----------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 16, 18, 12)
        outer.setSpacing(12)

        outer.addLayout(self._build_header())

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._build_left())
        split.addWidget(self._build_right())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([310, 830])
        outer.addWidget(split, 1)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪")

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        col = QVBoxLayout()
        col.setSpacing(1)
        t = QLabel("WindowKeeper")
        t.setObjectName("title")
        s = QLabel("记下窗口的位置，一键摆回来")
        s.setObjectName("subtitle")
        col.addWidget(t)
        col.addWidget(s)
        row.addLayout(col)
        row.addStretch(1)

        self.btn_capture = QPushButton("  捕获当前布局")
        self.btn_capture.setObjectName("primary")
        self.btn_capture.setToolTip("把现在所有窗口的位置和大小记成一个新预设  (Ctrl+N)")
        self.btn_capture.clicked.connect(self.capture_layout)
        row.addWidget(self.btn_capture)
        return row

    def _build_left(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        head = QHBoxLayout()
        lbl = QLabel("我的布局")
        lbl.setObjectName("sectionLabel")
        head.addWidget(lbl)
        head.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("subtitle")
        head.addWidget(self.count_label)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_presets)
        lay.addWidget(self.search)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentItemChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda _: self.apply_selected())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.btn_apply = QPushButton("还原")
        self.btn_apply.setObjectName("primary")
        self.btn_apply.setToolTip("把窗口摆回这个预设的位置  (Ctrl+Enter)")
        self.btn_apply.clicked.connect(self.apply_selected)
        self.btn_preview = QPushButton("预演")
        self.btn_preview.setToolTip("只画出来会怎么动，不真的移动窗口")
        self.btn_preview.clicked.connect(self.preview_selected)
        row.addWidget(self.btn_apply, 2)
        row.addWidget(self.btn_preview, 1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.btn_hotkey = QPushButton("热键…")
        self.btn_hotkey.clicked.connect(self.edit_hotkey)
        self.btn_update = QPushButton("更新")
        self.btn_update.setToolTip("用当前窗口位置覆盖这个预设  (Ctrl+S)")
        self.btn_update.clicked.connect(self.update_selected)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.clicked.connect(self.delete_selected)
        row2.addWidget(self.btn_hotkey)
        row2.addWidget(self.btn_update)
        row2.addWidget(self.btn_delete)
        lay.addLayout(row2)

        return box

    def _build_right(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.detail_title = QLabel("未选择预设")
        self.detail_title.setObjectName("sectionLabel")
        lay.addWidget(self.detail_title)

        self.tabs = QTabWidget()

        # -- 预览页 --
        page1 = QWidget()
        p1 = QVBoxLayout(page1)
        p1.setContentsMargins(12, 12, 12, 12)
        p1.setSpacing(8)

        bar = QHBoxLayout()
        self.chk_labels = QCheckBox("显示名称")
        self.chk_labels.setChecked(True)
        self.chk_labels.stateChanged.connect(
            lambda: self.map.set_show_labels(self.chk_labels.isChecked()))
        bar.addWidget(self.chk_labels)
        bar.addStretch(1)
        self.legend = QLabel("")
        self.legend.setObjectName("subtitle")
        bar.addWidget(self.legend)
        p1.addLayout(bar)

        self.map = MonitorMap()
        p1.addWidget(self.map, 1)

        self.hint = QLabel("选中左边的布局，这里会画出它摆好的样子。")
        self.hint.setObjectName("subtitle")
        self.hint.setWordWrap(True)
        p1.addWidget(self.hint)

        self.tabs.addTab(page1, "布局预览")

        # -- 窗口详情页 --
        page2 = QWidget()
        p2 = QVBoxLayout(page2)
        p2.setContentsMargins(12, 12, 12, 12)
        p2.setSpacing(8)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["程序", "位置", "尺寸", "显示器", "状态"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3, 4):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        p2.addWidget(self.table)
        self.tabs.addTab(page2, "窗口明细")

        # -- 显示器页 --
        page3 = QWidget()
        p3 = QVBoxLayout(page3)
        p3.setContentsMargins(12, 12, 12, 12)
        p3.setSpacing(8)

        self.mon_table = QTableWidget(0, 5)
        self.mon_table.setHorizontalHeaderLabels(
            ["序号", "设备名", "分辨率", "缩放", "工作区"])
        self.mon_table.verticalHeader().setVisible(False)
        self.mon_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        mh = self.mon_table.horizontalHeader()
        mh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (0, 2, 3, 4):
            mh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        p3.addWidget(self.mon_table)

        self.mon_map = MonitorMap()
        self.mon_map.setMinimumHeight(170)
        self.mon_map.setMaximumHeight(230)
        p3.addWidget(self.mon_map)
        self.tabs.addTab(page3, "显示器")

        lay.addWidget(self.tabs, 1)

        # -- 底部操作条 --
        foot = QHBoxLayout()
        self.btn_export = QPushButton("导出…")
        self.btn_export.clicked.connect(self.export_presets)
        self.btn_import = QPushButton("导入…")
        self.btn_import.clicked.connect(self.import_presets)
        self.btn_folder = QPushButton("数据文件夹")
        self.btn_folder.clicked.connect(self.open_data_dir)
        foot.addWidget(self.btn_export)
        foot.addWidget(self.btn_import)
        foot.addWidget(self.btn_folder)

        # -- 排障用：查看日志 / 导出诊断包 --
        # 设计意图：出了问题时用户不需要知道日志在哪、该复制哪些文件，
        # 点一下「导出诊断包」就能拿到一个可以直接发过来的 zip。
        self.btn_log = QPushButton("日志…")
        self.btn_log.clicked.connect(self.show_log)
        self.btn_diag = QPushButton("导出诊断包…")
        self.btn_diag.clicked.connect(self.export_diagnostics)
        foot.addWidget(self.btn_log)
        foot.addWidget(self.btn_diag)

        foot.addStretch(1)
        self.hk_label = QLabel("")
        self.hk_label.setObjectName("subtitle")
        foot.addWidget(self.hk_label)
        lay.addLayout(foot)

        return box

    def _install_shortcuts(self) -> None:
        pairs = [
            ("Ctrl+N", self.capture_layout),
            ("Ctrl+Return", self.apply_selected),
            ("Ctrl+Enter", self.apply_selected),
            ("Ctrl+S", self.update_selected),
            ("Ctrl+F", lambda: self.search.setFocus()),
            ("F5", self.reload_presets),
            ("Delete", self.delete_selected),
        ]
        for keys, fn in pairs:
            a = QAction(self)
            a.setShortcut(QKeySequence(keys))
            a.triggered.connect(fn)
            self.addAction(a)

    # ---------------- 数据与刷新 ----------------

    def reload_presets(self, select_id: str = "") -> None:
        self.store.load()
        cur = select_id or self._selected_id()
        self._filter_presets(keep_id=cur)

        errs = self.watcher.reload(self.store.presets)
        bad = [e for e in errs if e]
        if bad:
            self.status.showMessage("热键：" + "；".join(bad), 8000)
        self._update_hotkey_label()

    def _filter_presets(self, _text: str = "", keep_id: str = "") -> None:
        needle = self.search.text().strip().lower()
        self.list.clear()
        shown = 0
        for p in self.store.presets:
            if needle and needle not in p.name.lower():
                continue
            shown += 1
            item = QListWidgetItem(self._item_label(p))
            item.setData(Qt.UserRole, p.id)
            if p.hotkey:
                item.setToolTip(f"热键：{p.hotkey}")
            else:
                item.setToolTip("未设置热键")
            self.list.addItem(item)

        total = len(self.store.presets)
        self.count_label.setText(
            f"{shown}/{total}" if needle else (f"{total} 个" if total else ""))

        if not total:
            self.detail_title.setText("还没有预设")
            self.hint.setText("点右上角「捕获当前布局」，"
                              "把你现在这一屏窗口的位置记下来。")
            self.map.clear_rects()
            self.table.setRowCount(0)
            self._set_buttons_enabled(False)
            return

        self._set_buttons_enabled(True)
        if self.list.count():
            target = 0
            if keep_id:
                for i in range(self.list.count()):
                    if self.list.item(i).data(Qt.UserRole) == keep_id:
                        target = i
                        break
            self.list.setCurrentRow(target)

    @staticmethod
    def _item_label(p: store.Preset) -> str:
        n = p.window_count
        label = f"{p.name}\n{n} 个窗口"
        if p.hotkey:
            label += f"   ·   {p.hotkey}"
        return label

    def _set_buttons_enabled(self, on: bool) -> None:
        for b in (self.btn_apply, self.btn_preview, self.btn_update,
                  self.btn_delete, self.btn_hotkey):
            b.setEnabled(on)

    def _selected_id(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""

    def _selected(self) -> store.Preset | None:
        return self.store.get(self._selected_id())

    def refresh_monitors(self) -> None:
        self.monitors = w32.enum_monitors()
        self.mon_table.setRowCount(len(self.monitors))
        for i, m in enumerate(self.monitors):
            cells = [
                str(m.index),
                m.name,
                f"{m.width} × {m.height}",
                f"{m.scale * 100:.0f}%" + ("  主屏" if m.primary else ""),
                f"{m.work[0]},{m.work[1]}  {m.work[2] - m.work[0]}×{m.work[3] - m.work[1]}",
            ]
            for j, txt in enumerate(cells):
                self.mon_table.setItem(i, j, QTableWidgetItem(txt))
        self.mon_map.set_monitors(self.monitors)

    def _monitor_signature(self):
        return [(m.name, m.rect, m.work, m.dpi) for m in w32.enum_monitors()]

    def _check_monitors(self) -> None:
        try:
            sig = self._monitor_signature()
        except Exception:  # noqa: BLE001
            return
        if sig != self._mon_sig:
            self._mon_sig = sig
            self.refresh_monitors()
            self.status.showMessage("显示器配置有变化，已刷新", 4000)

    def _update_hotkey_label(self) -> None:
        n = len(self.watcher.active)
        self.hk_label.setText(f"全局热键 {n} 个生效" if n else "未启用全局热键")

    # ---------------- 选中联动 ----------------

    def _on_select(self, cur, _prev) -> None:
        if cur is None:
            return
        p = self.store.get(cur.data(Qt.UserRole))
        if p is None:
            return
        self._show_detail(p)

    def _show_detail(self, p: store.Preset) -> None:
        self.detail_title.setText(f"{p.name}   ·   {p.window_count} 个窗口")

        # 缩略图：先按保存的几何画
        rects = []
        for r in p.windows:
            label = r.title_fp or r.exe_name
            rects.append((r.x, r.y, r.w, r.h, label, "win"))
        self.map.set_monitors(getattr(self, "monitors", None) or w32.enum_monitors())
        self.map.set_rects(rects)
        self.legend.setText("实心 = 记录的窗口")

        # 明细表
        self.table.setRowCount(len(p.windows))
        for i, r in enumerate(p.windows):
            status = []
            if r.minimized:
                status.append("捕获时最小化")
            if r.maximized:
                status.append("捕获时最大化")
            if r.dpi:
                status.append(f"{r.dpi}dpi")
            cells = [
                r.exe_name or r.exe,
                f"{r.x}, {r.y}",
                f"{r.w} × {r.h}",
                r.monitor_name or f"[{r.monitor_index}]",
                "、".join(status) or "正常",
            ]
            for j, txt in enumerate(cells):
                item = QTableWidgetItem(txt)
                if j == 4 and status:
                    item.setForeground(QColor("#E0B060"))
                self.table.setItem(i, j, item)

        self.hint.setText(
            f"这个布局记录了 {p.window_count} 个窗口，"
            f"保存时有 {len(p.monitor_snapshot)} 块显示器。"
            "点「预演」可以看它现在能不能对上。")

    # ---------------- 操作 ----------------

    def capture_layout(self) -> None:
        name, ok = QInputDialog.getText(
            self, "捕获当前布局", "给这个布局起个名字：",
            text=self._suggest_name())
        if not ok:
            return
        name = name.strip() or self._suggest_name()

        refs = engine.capture_current_layout()
        if not refs:
            QMessageBox.information(self, "没有捕获到窗口",
                                    "现在没有找到可以记录的窗口。\n"
                                    "如果窗口最小化了，先恢复出来。")
            return

        existing = self.store.by_name(name)
        if existing:
            ans = QMessageBox.question(
                self, "已存在",
                f"已经有一个叫「{name}」的布局了。\n要覆盖它吗？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return

        p = store.Preset(
            name=name,
            windows=refs,
            monitor_snapshot=store.snapshot_monitors(),
            hotkey=existing.hotkey if existing else "",
        )
        if existing:
            # ★ 沿用旧 id，否则 update() 定位不到，会变成新增
            p.id = existing.id
            p.created_at = existing.created_at
            self.store.update(p)
        else:
            self.store.add(p)

        self.reload_presets(select_id=p.id)
        self.status.showMessage(f"已保存「{name}」，记录 {len(refs)} 个窗口", 5000)

    def _suggest_name(self) -> str:
        names = {p.name for p in self.store.presets}
        for i in range(1, 100):
            cand = f"布局 {i}" if i > 1 else "布局 1"
            if cand not in names:
                return cand
        return "布局"

    def apply_selected(self) -> None:
        p = self._selected()
        if p is None:
            return
        self.status.showMessage(f"正在还原「{p.name}」…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report = engine.apply_preset(p, activate=True)
        finally:
            QApplication.restoreOverrideCursor()

        self._after_apply(p, report)
        self.status.showMessage(f"{p.name}：{report.summary()}", 7000)

        bad = report.failed
        if bad:
            QMessageBox.warning(
                self, "有窗口没还原",
                report.summary() + "\n\n" + "\n".join(
                    f"· {o.ref.title_fp or o.ref.exe_name}：{o.message or o.status_text}"
                    for o in bad[:8]))

    def _after_apply(self, p: store.Preset, report: engine.ApplyReport) -> None:
        """还原后把缩略图换成实际结果——用户最想看的是「现在真的摆成什么样了」。"""
        rects = []
        for o in report.outcomes:
            if o.actual is None:
                r = o.ref
                rects.append((r.x, r.y, r.w, r.h,
                              r.title_fp or r.exe_name, "ghost"))
            else:
                x, y, w, h = o.actual
                label = o.ref.title_fp or o.ref.exe_name
                kind = "win" if o.kind == engine.OutcomeKind.OK else "ghost"
                rects.append((x, y, w, h, label, kind))
        self.map.set_rects(rects)
        self.legend.setText("实心 = 已还原   虚线 = 未成功")

        for i, o in enumerate(report.outcomes):
            if i >= self.table.rowCount():
                break
            item = QTableWidgetItem(o.status_text)
            item.setForeground(
                QColor("#7DDC9A") if o.kind == engine.OutcomeKind.OK
                else QColor("#E0B060") if o.kind == engine.OutcomeKind.CONSTRAINED
                else QColor("#FF9B9B"))
            self.table.setItem(i, 4, item)

    def preview_selected(self) -> None:
        p = self._selected()
        if p is None:
            return
        items = engine.preview_preset(p, self.monitors)
        if not items:
            self.hint.setText("这个预设里没有窗口。")
            return

        rects = []
        missed = 0
        for it in items:
            r = it.ref
            label = r.title_fp or r.exe_name
            if it.matched:
                rects.append((r.x, r.y, r.w, r.h, label, "win"))
            else:
                missed += 1
                rects.append((r.x, r.y, r.w, r.h, label, "ghost"))
        self.map.set_monitors(self.monitors)
        self.map.set_rects(rects)
        self.legend.setText("实心 = 找到窗口   虚线 = 没找到")
        self.tabs.setCurrentIndex(0)

        if missed:
            self.hint.setText(
                f"{len(items)} 个窗口里有 {missed} 个现在没找到（虚线）。"
                "没打开的窗口无法还原，其他窗口不受影响。")
            self.status.showMessage(f"{missed} 个窗口当前没打开", 5000)
        else:
            self.hint.setText(
                f"{len(items)} 个窗口全部都能对上，可以直接还原。")

    def update_selected(self) -> None:
        p = self._selected()
        if p is None:
            return
        ans = QMessageBox.question(
            self, "更新预设",
            f"用当前窗口的位置覆盖「{p.name}」？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return

        refs = engine.capture_current_layout()
        if not refs:
            QMessageBox.information(self, "没有捕获到窗口", "现在没有可记录的窗口。")
            return

        p.windows = refs
        p.monitor_snapshot = store.snapshot_monitors()
        self.store.update(p)
        self.reload_presets(select_id=p.id)
        self.status.showMessage(f"「{p.name}」已更新为 {len(refs)} 个窗口", 5000)

    def delete_selected(self) -> None:
        p = self._selected()
        if p is None:
            return
        ans = QMessageBox.question(
            self, "删除预设",
            f"确定要删除「{p.name}」吗？\n这个操作不能撤销。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ans != QMessageBox.Yes:
            return
        self.store.remove(p.id)
        self.reload_presets()
        self.status.showMessage(f"已删除「{p.name}」", 4000)

    def edit_hotkey(self) -> None:
        p = self._selected()
        if p is None:
            return
        dlg = HotkeyDialog(p.hotkey, self)
        if dlg.exec() != QDialog.Accepted:
            return
        text = dlg.result_text

        # 查重：同一个热键不能绑两个预设
        if text:
            for other in self.store.presets:
                if other.id != p.id and other.hotkey.strip().lower() == text.lower():
                    QMessageBox.warning(
                        self, "热键冲突",
                        f"「{other.name}」已经用了 {text}。\n换一个吧。")
                    return

        p.hotkey = text
        self.store.update(p)
        self.reload_presets(select_id=p.id)
        if text:
            errs = [e for e in self.watcher.errors if e]
            if any(text in e for e in errs):
                QMessageBox.warning(self, "热键没能生效",
                                    "\n".join(errs))
            else:
                self.status.showMessage(f"「{p.name}」的热键已设为 {text}", 5000)
        else:
            self.status.showMessage(f"「{p.name}」的热键已清除", 4000)

    def _on_hotkey(self, preset_id: str) -> None:
        p = self.store.get(preset_id)
        if p is None:
            return
        self.list.setCurrentRow(self._row_of(preset_id))
        report = engine.apply_preset(p, activate=True)
        self._after_apply(p, report)
        self.status.showMessage(f"热键：{p.name} — {report.summary()}", 6000)

    def _row_of(self, preset_id: str) -> int:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == preset_id:
                return i
        return 0

    # ---------------- 右键菜单 ----------------

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        self.list.setCurrentItem(item)
        p = self._selected()
        if p is None:
            return

        m = QMenu(self)
        a = m.addAction("还原")
        a.triggered.connect(self.apply_selected)
        a = m.addAction("预演")
        a.triggered.connect(self.preview_selected)
        m.addSeparator()
        a = m.addAction("更新为当前布局")
        a.triggered.connect(self.update_selected)
        a = m.addAction("设置热键…")
        a.triggered.connect(self.edit_hotkey)
        a = m.addAction("重命名…")
        a.triggered.connect(self.rename_selected)
        a = m.addAction("复制一份")
        a.triggered.connect(self.duplicate_selected)
        m.addSeparator()
        a = m.addAction("删除")
        a.triggered.connect(self.delete_selected)
        m.exec(self.list.mapToGlobal(pos))

    def rename_selected(self) -> None:
        p = self._selected()
        if p is None:
            return
        name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=p.name)
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name == p.name:
            return
        if self.store.by_name(name):
            QMessageBox.warning(self, "重名", f"已经有一个叫「{name}」的布局了。")
            return
        p.name = name
        self.store.update(p)
        self.reload_presets(select_id=p.id)

    def duplicate_selected(self) -> None:
        p = self._selected()
        if p is None:
            return
        import copy
        new = copy.deepcopy(p)
        new.id = ""
        new.__post_init__()
        new.name = self.store.ensure_unique_name(p.name + " 副本")
        new.hotkey = ""
        self.store.add(new)
        self.reload_presets(select_id=new.id)
        self.status.showMessage(f"已复制为「{new.name}」", 4000)

    # ---------------- 导入导出 ----------------

    def export_presets(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        if not self.store.presets:
            QMessageBox.information(self, "没有可导出的内容", "先捕获一个布局吧。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出布局", "WindowKeeper 布局.json", "JSON 文件 (*.json)")
        if not path:
            return
        from pathlib import Path
        n = self.store.export_to(Path(path))
        self.status.showMessage(f"已导出 {n} 个布局到 {path}", 6000)

    def import_presets(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "导入布局", "", "JSON 文件 (*.json)")
        if not path:
            return
        from pathlib import Path
        n, warns = self.store.import_from(Path(path))
        if n:
            self.store.save()
            self.reload_presets()
        msg = f"已导入 {n} 个布局。"
        if warns:
            msg += "\n\n" + "\n".join(warns[:6])
        QMessageBox.information(self, "导入完成", msg)

    def open_data_dir(self) -> None:
        import os
        d = self.store.dir
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(d))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            QMessageBox.information(self, "数据文件夹", f"{d}\n\n{exc}")

    # ---------------- 排障：日志与诊断包 ----------------

    def show_log(self) -> None:
        """
        在窗口里直接看日志。

        设计取舍：不调记事本打开文件，而是内嵌一个只读文本框。
        理由是用户来这里就是为了「看看刚才那次怎么失败了」，
        为了这件事再弹一个外部程序、还要自己找滚动条到底部，太重了。
        """
        lines = log.tail(400)
        text = "".join(lines) if lines else "（还没有日志）"

        dlg = QDialog(self)
        dlg.setWindowTitle("WindowKeeper 日志")
        dlg.resize(880, 560)
        lay = QVBoxLayout(dlg)

        hint = QLabel(f"日志文件：{log.log_file()}")
        hint.setWordWrap(True)
        hint.setObjectName("subtitle")
        lay.addWidget(hint)

        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        # 滚到底部：用户关心的是「刚刚发生了什么」
        box.verticalScrollBar().setValue(box.verticalScrollBar().maximum())
        lay.addWidget(box, 1)

        row = QHBoxLayout()
        b_open = QPushButton("用记事本打开")
        b_open.clicked.connect(lambda: self._open_path(log.log_file()))
        b_diag = QPushButton("导出诊断包…")
        b_diag.clicked.connect(self.export_diagnostics)
        b_clear = QPushButton("清空日志")
        b_clear.setObjectName("danger")

        def _do_clear() -> None:
            ok = QMessageBox.question(
                dlg, "清空日志",
                "确定要清空所有日志吗？\n\n"
                "如果是为了报问题，建议先「导出诊断包」再清空。")
            if ok == QMessageBox.StandardButton.Yes:
                log.clear()
                log.get("ui").info("用户清空了日志")
                box.setPlainText("（已清空）")

        b_clear.clicked.connect(_do_clear)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(dlg.accept)

        row.addWidget(b_open)
        row.addWidget(b_diag)
        row.addWidget(b_clear)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

        dlg.exec()

    def export_diagnostics(self) -> None:
        """
        打一个诊断包，用户直接发过来就能定位问题。

        这是日志功能真正的落地点：用户不需要知道
        「要给我看哪个文件、放在哪」，点一下拿到 zip 即可。
        """
        try:
            out = log.make_diagnostic_zip()
        except Exception as exc:  # noqa: BLE001
            log.exception("导出诊断包失败")
            QMessageBox.warning(self, "导出失败", f"{exc!r}")
            return

        log.get("ui").info("已导出诊断包", path=str(out))
        size_kb = out.stat().st_size / 1024 if out.exists() else 0
        msg = (f"诊断包已生成：\n\n{out}\n\n"
               f"大小 {size_kb:.1f} KB\n\n"
               "里面是运行环境、显示器配置、预设摘要和最近的日志。\n"
               "发给开发者即可定位问题。")
        box = QMessageBox(self)
        box.setWindowTitle("导出诊断包")
        box.setText(msg)
        b_open = box.addButton("打开所在文件夹", QMessageBox.ButtonRole.ActionRole)
        box.addButton("好", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is b_open:
            self._open_path(out.parent)

    @staticmethod
    def _open_path(p) -> None:
        import os
        try:
            os.startfile(str(p))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            QMessageBox.information(None, "打开失败", f"{p}\n\n{exc}")

    # ---------------- 关闭 ----------------

    def closeEvent(self, event) -> None:  # noqa: N802
        """关窗口不等于退出——如果托盘还在，就缩回托盘。"""
        if self.tray_app is not None:
            event.ignore()
            self.hide()
            self.tray_app.tray.showMessage(
                "WindowKeeper 仍在后台运行",
                "全局热键继续有效。想彻底退出请右键托盘图标。",
                make_icon(), 3000)
        else:
            self.watcher.detach()
            event.accept()
