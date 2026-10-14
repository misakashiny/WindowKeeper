# -*- coding: utf-8 -*-
"""
T10 —— 界面回归测试（不需要人眼看截图）。

为什么不用截图比对：
    截图只能证明「看起来没错」，证明不了「数据对不对」。
    而且换台机器、换个缩放率，像素就全变了。
    这里直接断言控件里的**内容**，这才是真正会出错的地方。

不做的验证：
    不测真实还原（那会移动用户正在用的窗口），
    还原链路的正确性由 tests\\test_engine.py 负责。
"""
import sys
import time
from pathlib import Path

# 用脚本自身位置推导仓库根，而不是当前工作目录。
# 原来写的是 sys.path.insert(0, ".")，只有在「当前目录恰好是 01_WindowKeeper」
# 时才跑得通；从项目根目录运行会报 ModuleNotFoundError: No module named 'windowkeeper'。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASS = "[OK]  "
FAIL = "[FAIL]"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"  {PASS if cond else FAIL} {name}" + (f"  —  {detail}" if detail else ""))


def pump(app, n=12):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.015)


def main():
    print("=" * 88)
    print("T10  界面回归（控件内容断言）")
    print("=" * 88)

    from PySide6.QtWidgets import QApplication
    from windowkeeper import tray, store, win32_api as w32
    from windowkeeper.ui import STYLE, MainWindow, HotkeyDialog
    from windowkeeper.monitor_map import MonitorMap

    app = tray._prepare_app()
    app.setStyleSheet(STYLE)

    # -- 1. 主窗口能构造 --
    print("\n1. 主窗口构造")
    try:
        w = MainWindow(tray=None)
        w.resize(1140, 720)
        pump(app)
        check("MainWindow 构造成功", True)
    except Exception as exc:  # noqa: BLE001
        check("MainWindow 构造成功", False, repr(exc))
        return 1

    check("self.monitors 在构造时就可用",
          hasattr(w, "monitors") and len(w.monitors) > 0,
          f"{len(getattr(w, 'monitors', []))} 块显示器")

    # -- 2. 显示器页有内容 --
    print("\n2. 显示器页")
    real_mons = w32.enum_monitors()
    check("显示器表行数与系统一致",
          w.mon_table.rowCount() == len(real_mons),
          f"表 {w.mon_table.rowCount()} 行 vs 系统 {len(real_mons)} 块")
    if w.mon_table.rowCount():
        first = [w.mon_table.item(0, j).text() for j in range(5)]
        check("第一行有设备名", bool(first[1]), f"{first[1]!r}")
        check("分辨率格式含 ×", "×" in first[2], f"{first[2]!r}")
        check("缩放列含 %", "%" in first[3], f"{first[3]!r}")

    # -- 3. 预设列表与选中联动 --
    print("\n3. 预设列表")
    n_presets = len(w.store.presets)
    check("列表项数与预设数一致",
          w.list.count() == n_presets, f"列表 {w.list.count()} vs 预设 {n_presets}")
    if n_presets:
        check("默认选中了第一项", w.list.currentRow() == 0)
        p = w._selected()
        check("_selected() 能取到预设", p is not None, p.name if p else "")
        if p:
            check("窗口明细行数 = 预设窗口数",
                  w.table.rowCount() == p.window_count,
                  f"{w.table.rowCount()} vs {p.window_count}")
            title = w.detail_title.text()
            check("标题栏含预设名", p.name in title, title)

    # -- 4. 搜索过滤 --
    print("\n4. 搜索过滤")
    if w.store.presets:
        name = w.store.presets[0].name
        w.search.setText(name[:2])
        pump(app, 4)
        check("搜到内容时列表非空", w.list.count() >= 1,
              f"搜「{name[:2]}」得 {w.list.count()} 项")
        w.search.setText("这个肯定搜不到xyzzy")
        pump(app, 4)
        check("搜不到时列表为空", w.list.count() == 0, f"{w.list.count()} 项")
        w.search.setText("")
        pump(app, 4)
        check("清空搜索后恢复", w.list.count() == n_presets)

    # -- 5. 缩略图控件 --
    print("\n5. 缩略图控件")
    mm = MonitorMap()
    mm.resize(600, 300)
    mm.set_monitors(real_mons)
    mm.show()
    pump(app)
    b = mm._bounds()
    check("有显示器时能算出外接框", b is not None,
          f"{b}" if b else "")
    if b:
        # 本机有负坐标屏，外接框必须含负值
        has_neg = any(m.rect[0] < 0 or m.rect[1] < 0 for m in real_mons)
        if has_neg:
            check("外接框正确处理了负坐标",
                  b.left() < 0 or b.top() < 0,
                  f"left={b.left()} top={b.top()}")
        sc = mm._scale()
        check("能算出缩放比例", sc is not None and sc[0] > 0,
              f"{sc[0]:.5f}" if sc else "")
        # 映射后的矩形必须落在控件内
        r = mm._map_rect(real_mons[0].rect[0], real_mons[0].rect[1],
                         real_mons[0].width, real_mons[0].height)
        inside = (r.left() >= -2 and r.top() >= -2
                  and r.right() <= mm.width() + 2
                  and r.bottom() <= mm.height() + 2)
        check("映射后的显示器矩形落在控件内", inside,
              f"({r.left():.1f},{r.top():.1f})-({r.right():.1f},{r.bottom():.1f})"
              f" 控件 {mm.width()}x{mm.height()}")
    mm.clear_rects()
    check("clear_rects 后不崩", True)

    # -- 6. 空显示器不崩 --
    print("\n6. 边界情况")
    mm2 = MonitorMap()
    mm2.resize(400, 200)
    mm2.set_monitors([])
    pump(app, 3)
    check("没有显示器时渲染不崩", True)
    mm2.set_monitors(real_mons)
    mm2.set_rects([])
    pump(app, 3)
    check("没有窗口时渲染不崩", True)

    # -- 7. 热键对话框的事件解析 --
    print("\n7. 热键对话框按键名解析")
    from PySide6.QtCore import Qt
    cases = [
        (Qt.Key_F5, "f5"), (Qt.Key_A, "a"), (Qt.Key_9, "9"),
        (Qt.Key_Space, "space"), (Qt.Key_Return, "enter"),
        (Qt.Key_Left, "left"), (Qt.Key_Control, "MOD"),
    ]
    ok_all = True
    for key, expect in cases:
        got = HotkeyDialog._key_name(key)
        if got != expect:
            ok_all = False
            check(f"按键名映射 {expect}", False, f"得到 {got!r}")
    check("常用按键名映射全部正确", ok_all, f"{len(cases)} 个用例")

    # -- 8. 按钮启用状态 --
    print("\n8. 按钮启用状态")
    w._set_buttons_enabled(True)
    check("启用后按钮可点", w.btn_apply.isEnabled())
    w._set_buttons_enabled(False)
    check("禁用后按钮不可点", not w.btn_apply.isEnabled())

    # -- 9. 排障入口：日志与诊断包 --
    #    这两个按钮是「用户出问题后能自助取证」的唯一途径，
    #    必须保证它们真的存在、且真的能用。
    print("\n9. 排障入口（日志 / 诊断包）")
    check("有「日志…」按钮", hasattr(w, "btn_log") and w.btn_log.isEnabled())
    check("有「导出诊断包…」按钮",
          hasattr(w, "btn_diag") and w.btn_diag.isEnabled())

    from windowkeeper import log
    check("tail() 返回列表", isinstance(log.tail(20), list))

    # 诊断包真的能生成出来，且内容完整
    import tempfile, zipfile
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        out = log.make_diagnostic_zip(_P(td) / "d.zip")
        check("诊断包生成成功", out.exists() and out.stat().st_size > 0,
              str(out))
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            check("诊断包含报告", "诊断报告.txt" in names, str(names))
            rep = z.read("诊断报告.txt").decode("utf-8")
            check("报告含显示器数量", "显示器数量" in rep)

    # 日志对话框构造不崩（不 exec，只构造）
    from PySide6.QtWidgets import QDialog
    try:
        lines = log.tail(5)
        check("日志内容可读", isinstance(lines, list),
              f"{len(lines)} 行")
    except Exception as exc:  # noqa: BLE001
        check("日志内容可读", False, repr(exc))

    w.close()
    pump(app, 3)

    n, total = sum(results), len(results)
    print()
    print("=" * 88)
    print(f"  通过 {n}/{total}")
    if n == total:
        print("  ✅ 界面回归通过。")
        return 0
    print(f"  ❌ 有 {total - n} 项失败。")
    return 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())
