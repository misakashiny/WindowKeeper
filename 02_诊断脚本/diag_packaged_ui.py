# -*- coding: utf-8 -*-
"""诊断：打包版 WindowKeeper.exe 走 ui 子命令时，窗口到底有没有开出来。

单独跑：
    python probe\\diag_packaged_ui.py
"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "WindowKeeper.exe"
u = ctypes.windll.user32


def windows_of(pid):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        pp = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pp))
        if pp.value == pid:
            t = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, t, 256)
            c = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(h, c, 256)
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            found.append({
                "cls": c.value, "title": t.value,
                "rect": (r.left, r.top, r.right - r.left, r.bottom - r.top),
                "visible": bool(u.IsWindowVisible(h)),
            })
        return True

    u.EnumWindows(cb, 0)
    return found


def all_pids():
    r = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq WindowKeeper.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True)
    pids = []
    for line in (r.stdout or "").splitlines():
        cells = [x.strip('"') for x in line.split('","')]
        if len(cells) >= 2 and cells[1].isdigit():
            pids.append(int(cells[1]))
    return pids


def main():
    subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                   capture_output=True, check=False)
    time.sleep(2)

    out_f = ROOT / "probe" / "_diag_ui_out.txt"
    err_f = ROOT / "probe" / "_diag_ui_err.txt"
    out_f.write_text("", encoding="utf-8")
    err_f.write_text("", encoding="utf-8")

    print(f"启动: {EXE} ui")
    p = subprocess.Popen(f'"{EXE}" ui > "{out_f}" 2> "{err_f}"',
                         shell=True, cwd=str(ROOT))
    time.sleep(11)
    print(f"shell 进程 pid={p.pid} poll={p.poll()}")

    live = all_pids()
    print(f"tasklist 找到的 PID: {live}")
    if p.poll() is None and p.pid not in live:
        live.append(p.pid)

    for pid in live:
        ws = windows_of(pid)
        print(f"\n--- PID {pid}: {len(ws)} 个顶层窗口 ---")
        for w in ws:
            vis = "可见" if w["visible"] else "隐藏"
            print(f"   [{vis}] {w['cls']:<40} {w['title'][:40]!r} {w['rect']}")

    print("\n--- 进程输出 ---")
    o = out_f.read_text(encoding="utf-8", errors="replace").strip()
    e = err_f.read_text(encoding="utf-8", errors="replace").strip()
    print("stdout:", o[:1500] or "(空)")
    print("stderr:", e[:1500] or "(空)")

    subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                   capture_output=True, check=False)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
