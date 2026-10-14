# -*- coding: utf-8 -*-
"""
T9 —— 热键与托盘链路的回归测试。

这些断言的价值在于把两个「踩过的坑」固化住：
  1. MSG 结构的 wParam 偏移在 64 位下是 16，不是手算的 12。
     写错过一次，症状是热键注册成功但按下去毫无反应。
  2. 没有修饰键的热键会被 parse_hotkey 拒绝，
     否则程序会抢走全局的普通按键，这是危险的。
"""
import ctypes
import sys
from ctypes import wintypes
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
    mark = PASS if cond else FAIL
    print(f"  {mark} {name}" + (f"  —  {detail}" if detail else ""))


def main():
    print("=" * 88)
    print("T9  热键与托盘链路")
    print("=" * 88)

    # -- 1. MSG 结构布局（那个坑） --
    print("\n1. MSG 结构布局")
    off_msg = getattr(wintypes.MSG, "message").offset
    off_wp = getattr(wintypes.MSG, "wParam").offset
    naive = ctypes.sizeof(wintypes.HWND) + ctypes.sizeof(wintypes.UINT)
    check("message 字段偏移为 8", off_msg == 8, f"实际 {off_msg}")
    check("wParam 字段偏移为 16（不是手算的 12）",
          off_wp == 16, f"实际 {off_wp}，手算得 {naive}")
    check("可以按结构体指针解引用（不再手算偏移）",
          ctypes.sizeof(wintypes.MSG) == 48,
          f"sizeof(MSG)={ctypes.sizeof(wintypes.MSG)}")

    # -- 2. 热键解析 --
    print("\n2. 热键字符串解析")
    from windowkeeper.tray import parse_hotkey, HotkeyError

    cases = [
        ("ctrl+alt+1", True, (0x0002 | 0x0001, 0x31)),
        ("Ctrl+Shift+F5", True, (0x0002 | 0x0004, 0x74)),
        ("win+alt+s", True, (0x0008 | 0x0001, 0x53)),
        ("ctrl+alt+space", True, (0x0003, 0x20)),
    ]
    for text, should_ok, expect in cases:
        try:
            got = parse_hotkey(text)
            check(f"{text!r} 解析正确", got == expect, f"{got}")
        except HotkeyError as exc:
            check(f"{text!r} 解析正确", not should_ok, f"被拒绝：{exc}")

    print("\n3. 危险写法必须被拒绝")
    dangerous = [
        ("f9", "无修饰键"),
        ("a", "无修饰键"),
        ("ctrl+alt", "缺主键"),
        ("ctrl+alt+1+2", "多个主键"),
        ("ctrl+foo", "认不出的键"),
        ("", "空字符串"),
        ("   ", "全是空格"),
    ]
    for text, why in dangerous:
        try:
            got = parse_hotkey(text)
            check(f"拒绝 {text!r}（{why}）", False, f"却接受了 → {got}")
        except HotkeyError:
            check(f"拒绝 {text!r}（{why}）", True)

    # -- 4. RegisterHotKey 真的能注册与注销 --
    print("\n4. Win32 RegisterHotKey 实际可用性")
    user32 = ctypes.windll.user32
    MOD_CONTROL, MOD_ALT, MOD_NOREPEAT = 0x0002, 0x0001, 0x4000
    TEST_VK = 0x7B   # F12，大概率没人占
    hid = 0xC0DE
    ok = user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, TEST_VK)
    check("ctrl+alt+F12 注册成功", bool(ok),
          "" if ok else f"错误码 {ctypes.windll.kernel32.GetLastError()}")
    if ok:
        # 重复注册同一个 id 应该失败
        dup = user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, TEST_VK)
        check("重复注册被系统拒绝", not bool(dup),
              f"错误码 {ctypes.windll.kernel32.GetLastError()}")
        user32.UnregisterHotKey(None, hid)
        # 注销后应能再注册
        again = user32.RegisterHotKey(None, hid, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, TEST_VK)
        check("注销后可重新注册", bool(again))
        if again:
            user32.UnregisterHotKey(None, hid)

    # -- 5. 托盘图标可构造 --
    print("\n5. 托盘图标构造（不需要显示器）")
    try:
        from PySide6.QtWidgets import QApplication
        from windowkeeper.tray import make_icon
        app = QApplication.instance() or QApplication([])
        icon = make_icon()
        check("make_icon() 返回非空图标", not icon.isNull())
        sizes = icon.availableSizes()
        check("图标有实际像素", len(sizes) > 0, f"{sizes}")
    except Exception as exc:  # noqa: BLE001
        check("托盘图标可构造", False, repr(exc))

    # -- 汇总 --
    n, total = sum(results), len(results)
    print()
    print("=" * 88)
    print(f"  通过 {n}/{total}")
    if n == total:
        print("  ✅ 热键链路回归通过。")
        return 0
    print(f"  ❌ 有 {total - n} 项失败。")
    return 1


if __name__ == "__main__":
    # 控制台按 UTF-8 输出，否则中文在 GBK 控制台上会乱码
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    sys.exit(main())
