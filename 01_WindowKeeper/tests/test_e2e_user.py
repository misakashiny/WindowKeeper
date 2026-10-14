# -*- coding: utf-8 -*-
"""最终端到端验收：完全模拟真实用户的操作流程。

    1. 双击 exe（无参数）→ 托盘常驻
    2. 用户把窗口弄乱
    3. 用户按全局热键
    4. 窗口精确回到记录位置

这是唯一一个"从用户视角"跑完整链路的脚本，其余测试都是分模块的。
"""
import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "WindowKeeper.exe"
sys.path.insert(0, str(ROOT))

u = ctypes.windll.user32
PASS, FAIL = "[OK]  ", "[FAIL]"
results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"  {PASS if cond else FAIL} {name}" + (f"  —  {detail}" if detail else ""))


def pids_of_exe():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowKeeper.exe",
                        "/FO", "CSV", "/NH"], capture_output=True, text=True)
    out = []
    for line in (r.stdout or "").splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) >= 2 and cells[1].isdigit():
            out.append(int(cells[1]))
    return out


def count_tray_windows(pids):
    n = [0]

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        pp = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pp))
        if pp.value in pids:
            c = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, c, 256)
            if "TrayIconMessageWindow" in c.value:
                n[0] += 1
        return True

    u.EnumWindows(cb, 0)
    return n[0]


def inject_ctrl_alt_9():
    VK_CONTROL, VK_MENU, VK_9, KU = 0x11, 0x12, 0x39, 2
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_MENU, 0, 0, 0)
    u.keybd_event(VK_9, 0, 0, 0)
    u.keybd_event(VK_9, 0, KU, 0)
    u.keybd_event(VK_MENU, 0, KU, 0)
    u.keybd_event(VK_CONTROL, 0, KU, 0)


def main():
    from windowkeeper import win32_api as w32, matcher, store, engine

    w32.enable_per_monitor_v2()
    print("=" * 88)
    print("T12  真实用户流程端到端验收")
    print("=" * 88)

    subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                   capture_output=True, check=False)
    time.sleep(2)

    # 找一块可用、能响应的窗口做实验。
    # ★ 不能随便挑：SLDWORKS 那种启动慢、窗口多的程序会让测试忽明忽暗。
    #   自己启一个程序最可控——它几何稳定、响应 SetWindowPos。
    #
    # ★★ 为什么用 charmap.exe（字符映射表）而不是 notepad.exe：
    #   测试要用 `--only <进程名>` 存预设，而 --only 只能按进程名过滤、
    #   无法按 hwnd 精确指定。用 notepad.exe 时，如果这台机器上恰好还开着
    #   别的记事本（开发机上几乎总是开着），那些窗口会被一起存进预设，
    #   还原时被一并操作，测试监视的那个窗口就被外部因素带偏，
    #   表现为偶发失败（实测见过 rect 变成最小化哨兵值 -32000,-32000）。
    #   charmap.exe 是「测试独占」的进程名，普通用户不会开着它，
    #   所以 --only charmap.exe 必定只命中本测试自己的窗口。
    #   它也是传统 Win32 程序，响应 SetWindowPos，几何稳定。
    _PROBE_EXE = "charmap.exe"
    _PROBE_TITLE = "准备实验窗口（自己启一个字符映射表，进程名测试独占）"
    print(f"\n1. {_PROBE_TITLE}")
    # 先清掉可能的残留，保证起点干净
    subprocess.run(["taskkill", "/F", "/IM", _PROBE_EXE],
                   capture_output=True, check=False)
    time.sleep(1.0)

    probe = subprocess.Popen([_PROBE_EXE])
    time.sleep(3.5)

    live = matcher.enumerate_live_windows()
    target = None
    others = []
    for w in live:
        if w.exe_name.lower() != _PROBE_EXE:
            continue
        if w32.is_minimized_geometry(w.rect):
            continue
        if target is None:
            target = w
        else:
            others.append(w)

    # 进程名独占的守卫：万一还有别的同名窗口，说明环境不干净，直接说清楚
    check("目标程序是测试独占的（无同名干扰窗口）",
          not others,
          f"额外 {len(others)} 个: {[o.hwnd for o in others]}" if others else "无干扰")

    if target:
        # 取消最大化，并摆到一个明确的非最大化位置
        w32.unmaximize(target.hwnd)
        time.sleep(0.6)
        w32.apply_geometry(target.hwnd, 320, 260, 760, 520,
                           w32.enum_monitors())
        time.sleep(0.8)
        fresh = w32.get_window_rect(target.hwnd)
        check("目标窗口已是普通（非最大化）状态",
              not w32.is_maximized(target.hwnd),
              f"IsZoomed={bool(u.IsZoomed(target.hwnd))} "
              f"rect={(fresh[0], fresh[1], fresh[2]-fresh[0], fresh[3]-fresh[1])}")

    check("目标窗口已就绪", target is not None,
          f"hwnd={target.hwnd}" if target else "没找到")
    if not target:
        probe.kill()
        return 1
    h = target.hwnd

    # -- 2. 用 exe 存一个预设，绑热键 ctrl+alt+9 --
    print("\n2. 用打包好的 exe 存预设")
    # 先删掉同名遗留（save 遇到重名会要求 --force，这是正确行为，
    # 但测试里要保证干净起点）
    st0 = store.PresetStore()
    st0.load()
    old = st0.by_name("__验收临时__")
    if old:
        st0.remove(old.id)

    r = subprocess.run([str(EXE), "save", "__验收临时__", "--only", _PROBE_EXE],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    check("exe save 成功", r.returncode == 0,
          f"exit={r.returncode} {(r.stdout or r.stderr or '').strip()[:120]}")
    r = subprocess.run([str(EXE), "hotkey", "__验收临时__", "ctrl+alt+9"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    check("exe hotkey 设置成功", r.returncode == 0, f"exit={r.returncode}")

    st = store.PresetStore()
    st.load()
    got = st.by_name("__验收临时__")
    check("预设确实写进了存储", got is not None,
          f"{len(got.windows) if got else 0} 个窗口, 热键={got.hotkey if got else ''}")
    if not got:
        return 1
    # ★ 前提守卫：预设里必须只有 1 个窗口（就是本测试启的那个）。
    #   若多于 1 个，说明进程名不再独占（环境里混进了别的同名窗口），
    #   此时后面的还原断言会测到不该测的东西，所以直接说清楚而不是继续跑。
    check("预设里只有本测试的那一个窗口", len(got.windows) == 1,
          f"实际 {len(got.windows)} 个: {[(r.exe_name, r.x, r.y) for r in got.windows]}"
          if len(got.windows) != 1 else "1 个")
    ref = got.windows[0]
    exp = (ref.x, ref.y, ref.w, ref.h)

    # -- 3. 双击 exe → 托盘 --
    print("\n3. 双击 exe（无参数 → 托盘）")
    # 输出重定向到系统临时目录，而不是项目里某个固定子目录。
    # 原先写死在 probe\ 下，那个目录整理时被移走了，于是这里抛
    # FileNotFoundError 让整个验收假失败。用临时目录后不再依赖任何项目目录。
    _tmp = Path(tempfile.mkdtemp(prefix="wk_e2e_"))
    out_f = _tmp / "_e2e_out.txt"
    err_f = _tmp / "_e2e_err.txt"
    out_f.write_text("", encoding="utf-8")
    err_f.write_text("", encoding="utf-8")
    p = subprocess.Popen(f'"{EXE}" > "{out_f}" 2> "{err_f}"',
                         shell=True, cwd=str(ROOT))
    time.sleep(10)
    check("托盘进程存活", p.poll() is None, f"pid={p.pid}")

    pids = pids_of_exe()
    tray_n = count_tray_windows(pids)
    check("托盘图标已注册", tray_n >= 1, f"{tray_n} 个消息窗口")

    # -- 4. 把窗口弄乱，按热键 --
    print("\n4. 弄乱窗口 → 按 Ctrl+Alt+9")
    still = [w for w in matcher.enumerate_live_windows() if w.hwnd == h]
    if not still:
        check("目标窗口还在", False, "窗口已关闭")
        subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                       capture_output=True, check=False)
        probe.kill()
        return 1
    check("目标窗口还在", True, f"hwnd={h}")

    mass = (exp[0] + 200, exp[1] + 150, 900, 600)
    w32.apply_geometry(h, mass[0], mass[1], mass[2], mass[3],
                       w32.enum_monitors())
    time.sleep(1.0)
    got_rect = w32.get_window_rect(h)
    check("窗口确实被弄乱了",
          abs(got_rect[0] - exp[0]) > 50 or abs(got_rect[1] - exp[1]) > 50,
          f"现在 {(got_rect[0], got_rect[1], got_rect[2]-got_rect[0], got_rect[3]-got_rect[1])}")

    print("  注入真实 ctrl+alt+9 ...")
    inject_ctrl_alt_9()
    for _ in range(40):
        time.sleep(0.1)

    final = w32.get_window_rect(h)
    fin = (final[0], final[1], final[2] - final[0], final[3] - final[1])
    check("★ 窗口被 exe 的热键精确还原", fin == exp,
          f"期望 {exp}，实际 {fin}")

    # -- 5. 清理 --
    print("\n5. 清理")
    subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                   capture_output=True, check=False)
    try:
        probe.kill()
    except Exception:  # noqa: BLE001
        pass
    st.load()
    tmp = st.by_name("__验收临时__")
    if tmp:
        st.remove(tmp.id)
    check("临时预设已清理", st.by_name("__验收临时__") is None,
          f"剩余预设 {[x.name for x in st.presets]}")

    n, total = sum(results), len(results)
    print()
    print("=" * 88)
    print(f"  通过 {n}/{total}")
    if n == total:
        print("  ✅ 真实用户流程完全跑通。")
        return 0
    print(f"  ❌ 有 {total - n} 项失败。")
    return 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
