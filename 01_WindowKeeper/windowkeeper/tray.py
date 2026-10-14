# -*- coding: utf-8 -*-
"""
托盘常驻 + 全局热键。

热键实现的选择（有实测依据，不是拍脑袋）：
    02_诊断脚本\\probe_hotkey_impl.py 做过对照实验，同一台机器、同样注入 ctrl+alt+1：
        Win32 RegisterHotKey + 消息循环  → 收到 WM_HOTKEY
        keyboard 库的后台线程监听        → 回调 0 次
    所以这里用 RegisterHotKey。它还有两个额外好处：
        · 零第三方依赖，打包体积更小
        · RegisterHotKey(None, ...) 把 WM_HOTKEY 投给调用线程的消息队列，
          而 QApplication.exec() 本来就在泵消息，于是不需要任何后台线程。
    低级键盘钩子（keyboard 库的做法）会被系统按 LowLevelHooksTimeout 静默摘掉，
    RegisterHotKey 是内核级注册，不受此限。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import __version__, engine, log, store
from . import win32_api as w32


# --------------------------------------------------------------------------
# 图标：不依赖外部图片文件，直接画一个
# --------------------------------------------------------------------------

def make_icon(color: str = "#4C8DFF") -> QIcon:
    """画一个「窗口四宫格」图标。这样打包成单文件 exe 时不必带资源文件。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.Antialiasing, True)

    pt.setBrush(QColor(color))
    pt.setPen(Qt.NoPen)
    # 左上大块 + 右侧两块，形似铺好的窗口布局
    pt.drawRoundedRect(6, 8, 28, 48, 4, 4)
    pt.drawRoundedRect(38, 8, 20, 22, 4, 4)
    pt.setBrush(QColor(color).lighter(135))
    pt.drawRoundedRect(38, 34, 20, 22, 4, 4)
    pt.end()
    return QIcon(pm)


# --------------------------------------------------------------------------
# 热键：Win32 RegisterHotKey + Qt 原生事件过滤器
# --------------------------------------------------------------------------

WM_HOTKEY = 0x0312

# 修饰键
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 名字 → 虚拟键码。只收录常用键，够覆盖「ctrl+alt+1」这类布局热键。
_VK_NAMES: dict[str, int] = {}
for _i in range(1, 25):
    _VK_NAMES[f"f{_i}"] = 0x6F + _i                    # F1..F24
for _c in "0123456789":
    _VK_NAMES[_c] = ord(_c)                            # 0x30..
for _c in "abcdefghijklmnopqrstuvwxyz":
    _VK_NAMES[_c] = ord(_c.upper())                    # 0x41..
_VK_NAMES.update({
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    "\\": 0xDC, ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
})

_MOD_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN, "cmd": MOD_WIN,
}


class HotkeyError(ValueError):
    """热键写法不合法。"""


def parse_hotkey(text: str) -> tuple[int, int]:
    """把 'ctrl+alt+1' 解析成 (modifiers, vk)。写错就抛 HotkeyError。"""
    parts = [p.strip().lower() for p in (text or "").split("+") if p.strip()]
    if not parts:
        raise HotkeyError("热键是空的")

    mods = 0
    vk = None
    for p in parts:
        if p in _MOD_NAMES:
            mods |= _MOD_NAMES[p]
        elif p in _VK_NAMES:
            if vk is not None:
                raise HotkeyError(f"「{text}」里有不止一个主键")
            vk = _VK_NAMES[p]
        else:
            raise HotkeyError(f"认不出按键「{p}」")

    if vk is None:
        raise HotkeyError(f"「{text}」缺少主键，例如 ctrl+alt+1")
    if mods == 0:
        # 不加修饰键会抢走全局的普通按键，必须拦住
        raise HotkeyError(f"「{text}」没有修饰键，会抢走全局按键，不允许")
    return mods, vk


class HotkeyWatcher(QObject, QAbstractNativeEventFilter):
    """
    注册全局热键，并在 Qt 的事件循环里接收 WM_HOTKEY。

    不需要后台线程：RegisterHotKey(None, ...) 把消息投给调用线程的消息队列，
    而这个线程正跑着 QApplication.exec()。
    """

    triggered = Signal(str)      # 参数是预设 id

    def __init__(self, parent: Optional[QObject] = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._bound: dict[int, str] = {}     # hotkey_id -> preset_id
        self._next_id = 0xA000
        self.errors: list[str] = []
        self.active: list[str] = []          # 注册成功的热键原文
        self._installed = False

    # -- 注册 --
    def reload(self, presets: list[store.Preset]) -> list[str]:
        self.stop()
        self.errors = []
        self.active = []

        if not self._installed:
            app = QApplication.instance()
            if app is not None:
                app.installNativeEventFilter(self)
                self._installed = True

        seen: set[str] = set()
        for p in presets:
            if not p.enabled or not p.hotkey.strip():
                continue
            text = p.hotkey.strip()
            key = text.lower()
            if key in seen:
                self.errors.append(f"热键 {text} 被多个预设占用，只保留第一个")
                continue

            try:
                mods, vk = parse_hotkey(text)
            except HotkeyError as exc:
                self.errors.append(f"「{p.name}」的热键无效：{exc}")
                continue

            hid = self._next_id
            self._next_id += 1
            ok = ctypes.windll.user32.RegisterHotKey(
                None, hid, mods | MOD_NOREPEAT, vk
            )
            if not ok:
                err = ctypes.windll.kernel32.GetLastError()
                # 1409 = ERROR_HOTKEY_ALREADY_REGISTERED
                if err == 1409:
                    self.errors.append(f"热键 {text} 已被别的程序占用")
                    log.get("hotkey").warning(
                        "热键已被别的程序占用", hotkey=text, preset=p.name,
                        error_code=err)
                else:
                    self.errors.append(f"热键 {text} 注册失败（错误码 {err}）")
                    log.get("hotkey").warning(
                        "热键注册失败", hotkey=text, preset=p.name,
                        error_code=err)
                continue

            self._bound[hid] = p.id
            seen.add(key)
            self.active.append(text)

        log.get("hotkey").info(
            "热键注册完成", registered=len(self.active),
            failed=len(self.errors), keys=self.active)
        return self.errors

    def stop(self) -> None:
        for hid in list(self._bound):
            try:
                ctypes.windll.user32.UnregisterHotKey(None, hid)
            except Exception:  # noqa: BLE001
                pass
        self._bound.clear()

    def detach(self) -> None:
        """退出时调用，把事件过滤器也摘掉。"""
        self.stop()
        if self._installed:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeNativeEventFilter(self)
                except Exception:  # noqa: BLE001
                    pass
            self._installed = False

    # -- Qt 原生事件入口 --
    def nativeEventFilter(self, event_type, message):  # noqa: N802
        """
        message 是指向 MSG 结构的指针。

        ★ 坑：不能自己按「sizeof(HWND)+sizeof(UINT)」手算 wParam 偏移。
          64 位下 MSG 的实际布局是 hwnd(0) / message(8) / wParam(16) / lParam(24)，
          因为 WPARAM 是 8 字节，编译器在 4 字节的 message 后补了 4 字节对齐。
          手算会少 4 个字节，读出垃圾值。这里直接让 ctypes 按结构体解引用。
        """
        try:
            if event_type != b"windows_generic_MSG":
                return False, 0
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message != WM_HOTKEY:
                return False, 0
            hid = int(msg.wParam)
        except Exception:  # noqa: BLE001
            return False, 0

        pid = self._bound.get(hid)
        if pid:
            # 在事件过滤器里直接动窗口会让消息循环卡住，交给下一轮事件循环
            QTimer.singleShot(0, lambda: self.triggered.emit(pid))
        return False, 0


# --------------------------------------------------------------------------
# 托盘
# --------------------------------------------------------------------------

class TrayApp(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.store = store.PresetStore()
        self.store.load()
        self.icon = make_icon()
        self.watcher = HotkeyWatcher(self)
        self.watcher.triggered.connect(self.apply_preset)
        self._busy = False

        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(f"WindowKeeper {__version__}")
        self.tray.activated.connect(self._on_activated)
        self._build_menu()
        self.tray.show()

        self.refresh()
        log.get("tray").info(
            "托盘已启动", version=__version__, presets=len(self.store.presets),
            data_dir=str(self.store.dir))
        self._notify("WindowKeeper 已启动",
                     f"{len(self.store.presets)} 个预设已就绪")

    # -- 菜单 --
    def _build_menu(self) -> None:
        m = QMenu()

        self.status_action = QAction("WindowKeeper", m)
        self.status_action.setEnabled(False)
        m.addAction(self.status_action)
        m.addSeparator()

        self.preset_menu = QMenu("还原布局", m)
        m.addMenu(self.preset_menu)

        act = QAction("捕获当前布局…", m)
        act.triggered.connect(self._capture)
        m.addAction(act)

        act = QAction("打开主界面", m)
        act.triggered.connect(self.open_window)
        m.addAction(act)

        act = QAction("刷新", m)
        act.triggered.connect(self.refresh)
        m.addAction(act)

        m.addSeparator()
        act = QAction("打开数据文件夹", m)
        act.triggered.connect(self._open_data_dir)
        m.addAction(act)

        act = QAction("重新加载热键", m)
        act.triggered.connect(self._reload_hotkeys)
        m.addAction(act)

        m.addSeparator()
        act = QAction("退出", m)
        act.triggered.connect(self._quit)
        m.addAction(act)

        self.menu = m
        self.tray.setContextMenu(m)

    def refresh(self) -> None:
        self.store.load()
        self.preset_menu.clear()

        presets = self.store.presets
        if not presets:
            a = QAction("(还没有预设)", self.preset_menu)
            a.setEnabled(False)
            self.preset_menu.addAction(a)
        else:
            for p in presets:
                label = p.name
                if p.hotkey:
                    label = f"{p.name}    {p.hotkey}"
                a = QAction(label, self.preset_menu)
                # 用默认参数固定住当前 p，否则所有项都会指向最后一个
                a.triggered.connect(lambda _=False, pid=p.id: self.apply_preset(pid))
                self.preset_menu.addAction(a)

        n = len(presets)
        self.status_action.setText(f"WindowKeeper {__version__} · {n} 个预设")
        self.tray.setToolTip(f"WindowKeeper · {n} 个预设")

        errs = self.watcher.reload(presets)
        bad = [e for e in errs if e]
        if bad:
            self.status_action.setText(
                f"WindowKeeper · {n} 个预设 · 热键 {len(self.watcher.active)} 个")

    def _reload_hotkeys(self) -> None:
        errs = self.watcher.reload(self.store.presets)
        if errs:
            QMessageBox.warning(None, "热键", "\n".join(errs))
        else:
            self._notify("热键已重新加载",
                         f"{len(self.watcher.active)} 个热键生效" if self.watcher.active
                         else "没有配置热键")

    # -- 交互 --
    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_window()

    def open_window(self) -> None:
        """打开主界面。已经开着就把它带到前台。"""
        from .ui import MainWindow
        if getattr(self, "_window", None) is None:
            self._window = MainWindow(tray=self)
        self._window.reload_presets()
        self._window.refresh_monitors()
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def _capture(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(None, "捕获当前布局", "预设名称：")
        if not ok or not name.strip():
            return
        name = name.strip()

        refs = engine.capture_current_layout()
        if not refs:
            self._notify("没有捕获到窗口", "试试把窗口恢复出来再捕获")
            return

        existing = self.store.by_name(name)
        preset = store.Preset(
            name=name,
            windows=refs,
            monitor_snapshot=store.snapshot_monitors(),
            hotkey=existing.hotkey if existing else "",
        )
        if existing:
            preset.id = existing.id
            self.store.update(preset)
        else:
            self.store.add(preset)

        self.refresh()
        log.get("tray").info("捕获布局", preset=name, windows=len(refs),
                             overwritten=bool(existing))
        self._notify(f"已保存「{name}」", f"记录 {len(refs)} 个窗口")

    def apply_preset(self, preset_id: str) -> None:
        if self._busy:
            return
        p = self.store.get(preset_id)
        if p is None:
            log.get("tray").warning("要还原的预设不存在，可能已被删除",
                                    preset_id=preset_id)
            return

        self._busy = True
        try:
            log.get("tray").info("热键/菜单触发还原", preset=p.name)
            report = engine.apply_preset(p, activate=True)
            self._notify(f"{p.name}：{report.summary()}",
                         self._short_detail(report))
        except Exception as exc:  # noqa: BLE001
            # ★ 这里原来没有 except：一次还原失败会直接冒泡到 Qt，
            #   在托盘模式下用户只会看到「图标突然没了」，没有任何线索。
            log.exception(f"还原预设时发生未预期错误：{p.name}")
            self._notify(f"{p.name}：还原失败", str(exc)[:200])
        finally:
            self._busy = False

    @staticmethod
    def _short_detail(report: engine.ApplyReport) -> str:
        lines = []
        for o in report.outcomes:
            if o.kind == engine.OutcomeKind.OK:
                continue
            name = (o.ref.title_fp or o.ref.exe_name or "?")[:28]
            lines.append(f"{name}：{o.status_text}")
            if len(lines) >= 4:
                break
        return "\n".join(lines)

    def _open_data_dir(self) -> None:
        import os
        d = self.store.dir
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(d))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            QMessageBox.information(None, "数据文件夹", f"{d}\n\n{exc}")

    def _notify(self, title: str, body: str = "") -> None:
        try:
            self.tray.showMessage(title, body, self.icon, 3200)
        except Exception:  # noqa: BLE001
            pass

    def _quit(self) -> None:
        self.watcher.detach()
        win = getattr(self, "_window", None)
        if win is not None:
            win.tray_app = None      # 告诉主窗口「这次是真退出」，别再退回托盘
            win.close()
        self.tray.hide()
        self.app.quit()


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------

def _prepare_app() -> QApplication:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("WindowKeeper")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(make_icon())
    app.setStyleSheet("")   # 主界面的样式由 ui.STYLE 提供，这里先清空
    return app


def _install_crash_logger() -> None:
    """
    把「没人接的异常」写进日志。

    ★ 这是日志功能最重要的一环：托盘程序没有控制台，
      崩溃时如果只往 stderr 写，打包成 console=False 的 exe 就彻底消失了，
      用户只能看到「它自己没了」。写进文件才能事后查。
    """
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("windowkeeper").critical(
            "未捕获的异常：%s", exc_value, exc_info=(exc_type, exc_value, exc_tb))

    sys.excepthook = _hook
    # Qt 槽函数里抛出的异常不会走 sys.excepthook，Qt6 会直接终止进程，
    # 这里至少保证异常信息先落盘。
    logging.getLogger("windowkeeper").debug("已安装全局异常记录钩子")


def run_tray() -> int:
    log.setup(logging.INFO, console=False)
    log.get("tray").info(
        "===== WindowKeeper %s 启动（托盘模式）=====", __version__)
    _install_crash_logger()

    app = _prepare_app()
    app.setQuitOnLastWindowClosed(False)   # 没有主窗口，别一上来就退出

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.get("tray").error("这台机器没有可用的系统托盘，无法启动")
        print("这台机器没有可用的系统托盘。")
        return 1

    tray = TrayApp(app)
    code = app.exec()
    log.get("tray").info("托盘已退出", exit_code=code)
    return code


def run_ui() -> int:
    """单独打开主界面（不带托盘）。适合只想改改预设的场合。"""
    log.setup(logging.INFO, console=False)
    log.get("ui").info("===== WindowKeeper %s 启动（主界面模式）=====", __version__)
    _install_crash_logger()

    app = _prepare_app()
    from .ui import STYLE, MainWindow
    app.setStyleSheet(STYLE)

    win = MainWindow(tray=None)
    win.show()
    code = app.exec()
    log.get("ui").info("主界面已退出", exit_code=code)
    return code
