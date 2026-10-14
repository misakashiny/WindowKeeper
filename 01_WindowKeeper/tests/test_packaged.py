# -*- coding: utf-8 -*-
"""
打包产物验收测试。

为什么必须单独测打包版：
    PyInstaller 打包后的失败模式和源码运行完全不同——
    隐藏 import 没收集到、Qt 插件被排除掉了、资源路径找不到、
    单文件解压目录里找不到模块…… 这些在源码里跑一百遍都发现不了。
    所以每一项都要对着 dist\\WindowKeeper.exe 真跑。

跑法：
    python tests\\test_packaged.py
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

PASS, FAIL = "[OK]  ", "[FAIL]"
results = []
u = ctypes.windll.user32


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"  {PASS if cond else FAIL} {name}" + (f"  —  {detail}" if detail else ""))


def find_tray_window(cls_name="Qt6120TrayIconMessageWindowClass"):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        buf = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(h, buf, 256)
        if buf.value == cls_name:
            found.append(h)
        return True

    u.EnumWindows(cb, 0)
    return found


def find_windows_of_pid(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and u.IsWindowVisible(h):
            t = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, t, 256)
            found.append((h, t.value))
        return True

    u.EnumWindows(cb, 0)
    return found


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                   capture_output=True, check=False)
    time.sleep(2)


def main():
    print("=" * 88)
    print("T11  打包产物验收（对着 dist\\WindowKeeper.exe 真跑）")
    print("=" * 88)

    if not EXE.exists():
        print(f"  {FAIL} 找不到 {EXE}，先跑 PyInstaller 打包")
        return 1

    kill_all()

    # -- 1. 文件本身 --
    print("\n1. 产物文件")
    size_mb = EXE.stat().st_size / 1024 / 1024
    check("exe 存在", True, f"{size_mb:.1f} MB")
    check("体积在合理范围（<40MB）", size_mb < 40, f"{size_mb:.1f} MB")

    # 确认没有变成"文件夹版"（PyInstaller 若不慎用 onedir 会多出 dist\WindowKeeper\）
    onedir = ROOT / "dist" / "WindowKeeper"
    check("是单文件而非目录版", not onedir.exists(),
          "dist\\WindowKeeper\\ 不存在" if not onedir.exists() else "有目录版残留")

    # -- 2. 命令行模式 --
    print("\n2. 命令行模式（exe=CLI 三合一）")
    r = subprocess.run([str(EXE), "monitors"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=90)
    check("monitors 子命令返回 0", r.returncode == 0, f"exit={r.returncode}")
    out = r.stdout or ""
    check("输出了显示器清单", "DISPLAY" in out.upper(),
          out.strip().splitlines()[0] if out.strip() else "(空)")
    check("列出了 3 块显示器", out.count("DISPLAY") >= 3,
          f"出现 {out.count('DISPLAY')} 次")

    r = subprocess.run([str(EXE), "list"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=90)
    check("list 子命令返回 0", r.returncode == 0, f"exit={r.returncode}")

    # 编造一个不存在的子命令，应该报错而不是静默成功
    r = subprocess.run([str(EXE), "definitely-not-a-command"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=90)
    check("未知子命令会报错退出", r.returncode != 0, f"exit={r.returncode}")

    # -- 3. 托盘模式 --
    print("\n3. 托盘模式（无参数双击）")
    # 输出重定向到系统临时目录，而不是项目里某个固定子目录。
    # 原先写死在 probe\ 下，那个目录整理时被移走了（probe → 02_诊断脚本），
    # 于是这里抛 FileNotFoundError 让整个验收假失败。用临时目录后不再依赖任何项目目录。
    _tmp = Path(tempfile.mkdtemp(prefix="wk_pkg_"))
    out_f0 = _tmp / "_pkg_tray_out.txt"
    err_f0 = _tmp / "_pkg_tray_err.txt"
    for f in (out_f0, err_f0):
        f.write_text("", encoding="utf-8")
    p = subprocess.Popen(f'"{EXE}" > "{out_f0}" 2> "{err_f0}"',
                         shell=True, cwd=str(ROOT))
    time.sleep(8)
    alive = p.poll() is None
    check("进程起来了且没崩", alive,
          f"PID {p.pid}" if alive else f"已退出，code={p.poll()}")

    trays = find_tray_window()
    check("Qt 托盘消息窗口已注册", bool(trays), f"{len(trays)} 个")

    # 托盘本身没有可见窗口（它只是个通知区图标），所以这里不能要求有窗口。
    # 真正的证据是：1) 进程活着  2) 托盘消息窗口存在  3) 热键占住了（下一节测）
    if not alive:
        kill_all()
        return 1

    # -- 4. 打包版能否打开主界面 --
    print("\n4. 主界面（打包版）")
    # 注意：exe 是 console=False 的，用 Popen(pipe) 抓不到它的输出，
    # 而且它会一直阻塞在 Qt 事件循环里。所以用文件重定向 + 独立进程，
    # 靠"窗口是否真的出现"来判断成功，而不是靠进程退出码。
    kill_all()
    out_f = _tmp / "_pkg_ui_out.txt"
    err_f = _tmp / "_pkg_ui_err.txt"
    for f in (out_f, err_f):
        f.write_text("", encoding="utf-8")

    p2 = subprocess.Popen(
        f'"{EXE}" ui > "{out_f}" 2> "{err_f}"',
        shell=True, cwd=str(ROOT),
    )
    time.sleep(11)
    alive2 = p2.poll() is None
    check("ui 子命令进程存活", alive2,
          f"PID {p2.pid}" if alive2 else f"已退出 code={p2.poll()}")

    if not alive2:
        # 进程死了才能安全读输出文件
        msg = (out_f.read_text(encoding="utf-8", errors="replace").strip()
               or err_f.read_text(encoding="utf-8", errors="replace").strip())
        check("ui 启动失败时给出可读报错", bool(msg), msg[:300] or "(无输出)")

    # 关键：PyInstaller 单文件 exe 运行时是【两个进程】——
    #   父进程 = 引导进程（窗口类 PyInstallerOnefileHiddenWindow）
    #   子进程 = 真正跑 Python/Qt 的那个
    # 只拿 tasklist 的第一行会拿到引导进程，从而误判"没有窗口"。
    # 所以要把所有 WindowKeeper.exe 的 PID 都收集起来一起看。
    all_pids = []
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowKeeper.exe",
                        "/FO", "CSV", "/NH"],
                       capture_output=True, text=True, timeout=60)
    for line in (r.stdout or "").splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) >= 2 and cells[1].isdigit():
            all_pids.append(int(cells[1]))

    wins = []
    for pid in all_pids:
        wins.extend(find_windows_of_pid(pid))
    titles = [t for _h, t in wins if t]
    check("主界面窗口真的开出来了", bool(wins), f"{titles}" if titles else "无窗口")
    has_main = any("WindowKeeper" in t for t in titles)
    check("窗口标题是 WindowKeeper", has_main, f"{titles}")

    # 主界面窗口要有合理尺寸（不是 0x0 那种没布局成功的）
    if wins:
        h = wins[0][0]
        rect = wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(rect))
        w, ht = rect.right - rect.left, rect.bottom - rect.top
        check("主界面尺寸合理（>600x400）", w > 600 and ht > 400, f"{w}x{ht}")
    else:
        check("主界面尺寸合理（>600x400）", False, "没有窗口可测")

    # -- 5. 退出干净 --
    print("\n5. 退出")
    kill_all()
    time.sleep(1)
    leftover = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowKeeper.exe"],
                              capture_output=True, text=True, timeout=60)
    check("退出后没有残留进程",
          "WindowKeeper.exe" not in (leftover.stdout or ""),
          "已清理")

    n, total = sum(results), len(results)
    print()
    print("=" * 88)
    print(f"  通过 {n}/{total}")
    if n == total:
        print("  ✅ 打包产物验收通过。")
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
