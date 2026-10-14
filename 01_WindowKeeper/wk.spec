# -*- mode: python ; coding: utf-8 -*-
"""
WindowKeeper 打包配置（PyInstaller）。

两个产物：
    wk.exe        控制台版，给命令行用（python wk.py 的替代）
    WindowKeeper.exe  窗口版，无控制台，跑托盘 + 主界面

体积是这里的主要敌人。PySide6 装全了有 200MB+，但我们只用到
QtCore / QtGui / QtWidgets 三个模块，其余（WebEngine、3D、Charts、
Multimedia、Quick、SQL、Network…）必须显式排除，否则 exe 会胖得离谱。
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH)

# ---------------------------------------------------------------------------
# 用不到的 Qt 模块。排除不干净的话 PySide6 的 hook 会把整个发行版拖进来。
# ---------------------------------------------------------------------------
QT_EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvgWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSpatialAudio", "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtHttpServer", "PySide6.QtNetworkAuth", "PySide6.QtConcurrent",
    "PySide6.QtDBus", "PySide6.QtPrintSupport", "PySide6.QtXml",
    # 第三方里我们用不到的大块头
    "numpy", "pandas", "matplotlib", "scipy", "PIL",
    "tkinter", "unittest", "pydoc", "doctest", "email", "html", "http",
    "xmlrpc", "pdb", "sqlite3", "lib2to3", "distutils", "setuptools",
    "pip", "wheel", "IPython", "pytest",
    # pywin32 里我们用不到的（只用 win32gui/win32api/win32con/win32process）
    "win32com", "win32comext", "pythonwin", "win32clipboard",
    "win32service", "win32serviceutil", "win32com.shell",
]

HIDDEN = ["win32timezone", "win32api", "win32con", "win32gui", "win32process"]

# ---------------------------------------------------------------------------
# ★ 关键：excludes 只管 Python 模块，管不住 Qt 的 DLL。
#   Qt6 的 DLL 是被 PyInstaller 的 PySide6 hook 无差别收集进来的，
#   第一版打出来 45.9MB，其中 Qt6Quick/Qt6Qml/Qt6Pdf/opengl32sw/libcrypto
#   全都是我们根本用不到的。这里在 Analysis 之后、EXE 之前手工剔除。
#
#   必须保留的只有：
#       Qt6Core / Qt6Gui / Qt6Widgets   —— 三个模块本身就够
#       plugins\platforms\qwindows.dll  —— 没有它 Qt 起不来
#       plugins\styles\qmodernwindowsstyle.dll —— Windows 11 原生控件外观
#       MSVC 运行库 (msvcp140/vcruntime140 等)
# ---------------------------------------------------------------------------
# 用不到的大 DLL（前缀匹配文件名，不带路径）
DROP_DLL_PREFIXES = (
    "qt6quick", "qt6qml", "qt6pdf", "qt6network", "qt6sql", "qt6test",
    "qt6xml", "qt6svg", "qt6opengl", "qt6multimedia", "qt6printsupport",
    "qt6dbus", "qt6websockets", "qt6concurrent", "qt6designer", "qt6help",
    "qt6bluetooth", "qt6nfc", "qt6positioning", "qt6serialport",
    "qt6texttospeech", "qt6statemachine", "qt6charts", "qt6datavisualization",
    "opengl32sw",          # 7.3MB 的软件 OpenGL 回退，用不到
    "libcrypto", "libssl", # Qt6Network 的依赖，一起走
    "qt6network", "qtnetwork",   # 我们不做任何网络请求
    "qt6virtualkeyboard", "qtvirtualkeyboard",
    "qtiff", "qwebp", "qtga", "qwbmp", "qicns",
    "d3dcompiler", "qt6webengine",
)
# 用不到的 Qt 插件目录
DROP_PLUGIN_DIRS = (
    "plugins\\imageformats", "plugins\\iconengines", "plugins\\qmltooling",
    "plugins\\multimedia", "plugins\\networkinformation", "plugins\\tls",
    "plugins\\printsupport", "plugins\\sqldrivers", "plugins\\generic",
    "plugins\\virtualkeyboard", "plugins\\platforminputcontexts",
    "plugins\\webview", "plugins\\scenegraph", "plugins\\renderers",
)
# 但平台/样式/图标这几个必须留，否则 Qt 直接起不来
KEEP_ALWAYS = ("qwindows", "qmodernwindowsstyle", "qico", "qjpeg", "qgif",
               "qsvgicon", "qdirect2d")


def _keep_name(raw_name: str) -> bool:
    """判断一个打包项该不该留。raw_name 形如 'PySide6\\Qt6Quick.dll' 或 'PySide6/plugins/...'。"""
    name = raw_name.replace("/", "\\").lower()
    base = name.rsplit("\\", 1)[-1]

    # 1) 明确要留的（平台插件、样式插件、图标插件）
    if base.startswith(KEEP_ALWAYS):
        return True

    # 2) 明确不要的插件目录
    for d in DROP_PLUGIN_DIRS:
        if name.startswith(d.lower()):
            return False

    # 3) 明确不要的 DLL
    for pre in DROP_DLL_PREFIXES:
        if base.startswith(pre):
            if base.startswith(KEEP_ALWAYS):   # 双保险
                return True
            return False

    return True


def _keep_bin(entry):
    return _keep_name(entry[0])


def _keep_data(entry):
    return _keep_name(entry[0])


a = Analysis(
    [str(ROOT / "wk_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=QT_EXCLUDES,
    noarchive=False,
    optimize=0,
)

# --- 手工剔除用不到的 Qt DLL 与插件（必须在 PYZ/EXE 之前）------------------
_before = len(a.binaries) + len(a.datas)
a.binaries = TOC([e for e in a.binaries if _keep_bin(e)])
a.datas = TOC([e for e in a.datas if _keep_data(e)])
_dropped = _before - (len(a.binaries) + len(a.datas))
print(f"[wk.spec] 剔除了 {_dropped} 个用不到的 Qt 组件，"
      f"保留 binaries={len(a.binaries)} datas={len(a.datas)}")

pyz = PYZ(a.pure)

# --- 窗口版：无控制台，托盘 + 主界面 + CLI 三合一 ---------------------------
exe_window = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="WindowKeeper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 关键：双击不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
