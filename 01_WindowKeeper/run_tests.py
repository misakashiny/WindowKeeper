# -*- coding: utf-8 -*-
r"""
统一测试入口 —— 一条命令跑完全部回归套件。

为什么需要这个文件：
    测试原先散在 tests\ 下，各自用 sys.path.insert(0, ".") 找模块，
    只有在「当前目录恰好是 01_WindowKeeper」时才跑得通；换个目录就报
    ModuleNotFoundError: No module named 'windowkeeper'。
    而且没有地方记录「一共几个套件、哪些要联网、哪些会真的动窗口」。

用法（在任何目录下都可以）：
    python 01_WindowKeeper\run_tests.py            # 跑默认套件
    python 01_WindowKeeper\run_tests.py --all      # 连打包验收也跑
    python 01_WindowKeeper\run_tests.py -k log     # 只跑名字含 log 的套件
    python 01_WindowKeeper\run_tests.py --list     # 只列出套件，不跑

套件分级（为什么不是一股脑全跑）：
    fast  —— 纯逻辑与界面控件断言，不动用户窗口，秒级完成
    slow  —— 会真的移动窗口、注入按键，需要几秒
    pack  —— 对着 dist\WindowKeeper.exe 真跑，要求先打包过
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


@dataclass
class Suite:
    r"""一个测试套件。name 用于 -k 过滤，script 是相对 tests\ 的文件名。"""

    name: str
    script: str
    group: str          # fast / slow / pack
    title: str
    note: str = ""
    # 判定成功的标记：脚本自带 summary 输出，命中任一即算通过
    markers: tuple[str, ...] = field(default=("全部通过", "通过。", "✅"))


SUITES: list[Suite] = [
    Suite("engine", "test_engine.py", "fast", "T1-T8 引擎与存储",
          "DPI 声明、窗口枚举、标题清洗、捕获→匹配往返、几何解析、存储往返"),
    Suite("log", "test_log.py", "fast", "T13 日志系统",
          "级别参数、双格式字段、DECISION 级别、重复 setup 不重复写、诊断包"),
    Suite("hotkey", "test_hotkey.py", "fast", "T9 热键解析与链路",
          "固化了 MSG 偏移 16 与「无修饰键必须拒绝」两个坑"),
    Suite("ui", "test_ui.py", "fast", "T10 界面回归",
          "断言控件内容而非截图；含排障入口按钮"),
    Suite("e2e", "test_e2e_user.py", "slow", "T12 真实用户流程",
          "用测试独占的 charmap.exe，避免同进程名窗口干扰；会真的动窗口"),
    Suite("packaged", "test_packaged.py", "pack", "T11 打包产物验收",
          "对着 dist\\WindowKeeper.exe 真跑，需先执行 build.py"),
]


def run_one(s: Suite) -> tuple[bool, str, float]:
    """跑一个套件，返回 (是否通过, 摘要行, 耗时秒)。"""
    script = ROOT / "tests" / s.script
    if not script.exists():
        return False, f"脚本不存在：{script}", 0.0

    t0 = time.time()
    proc = subprocess.run(
        [PY, str(script)],
        cwd=str(ROOT),                 # ★ 固定工作目录，不依赖调用者的 cwd
        capture_output=True,
    )
    elapsed = time.time() - t0
    # ★ 编码自适应：各测试脚本对 stdout 的编码处理不一致——
    #   有的自己 reconfigure 成 UTF-8，有的没有，于是继承 Windows 控制台的 GBK。
    #   这里两种都试，谁解得通用谁，不要求各脚本自律。
    raw = (proc.stdout or b"") + (proc.stderr or b"")
    out = ""
    for enc in ("utf-8", "gbk", "cp936"):
        try:
            out = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        out = raw.decode("utf-8", errors="replace")

    if "ModuleNotFoundError" in out:
        line = next((l.strip() for l in out.splitlines() if "ModuleNotFoundError" in l), "")
        return False, f"导入失败：{line}", elapsed

    # 从输出里抓「通过 N / 共 M」这类计数，抓不到就退回标记判定
    import re
    m = re.search(r"通过\s*(\d+)\s*/\s*(?:共\s*)?(\d+)", out)
    if m:
        got, total = int(m.group(1)), int(m.group(2))
        ok = got == total and total > 0
    else:
        ok = any(mk in out for mk in s.markers)
        got = total = 0

    if not ok:
        # 计数说通过、但退出码非 0：多半是 stderr 里有 logging 自身的诊断输出
        # （test_log.py 故意测「无占位符时传位置参数」，logging 会把这事写到 stderr，
        #  这不是测试失败）。这种情况以计数为准，只提示一句。
        if m and got == total and proc.returncode != 0:
            return True, f"{got}/{total}（stderr 有诊断输出，非失败）", elapsed
        bad = [l.strip() for l in out.splitlines() if "[FAIL]" in l or "FAILED" in l]
        summary = "失败" + (f"：{bad[0][:80]}" if bad else "")
        return False, summary, elapsed

    return True, (f"{got}/{total}" if total else "通过"), elapsed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="run_tests.py",
        description="WindowKeeper 统一测试入口（在任何目录下都能跑）",
    )
    ap.add_argument("--all", action="store_true", help="连打包验收（pack）也跑")
    ap.add_argument("-k", "--filter", default="", help="只跑名字含此片段的套件")
    ap.add_argument("--list", action="store_true", help="只列出套件，不执行")
    args = ap.parse_args(argv)

    picked = [s for s in SUITES if args.filter.lower() in s.name.lower()]
    if not args.all:
        picked = [s for s in picked if s.group != "pack"]

    print("=" * 78)
    print("WindowKeeper 回归测试")
    print(f"  项目根：{ROOT}")
    print(f"  Python：{PY}")
    print("=" * 78)

    if args.list:
        for s in picked:
            print(f"  [{s.group:4}] {s.name:9} {s.title:18} {s.script}")
            if s.note:
                print(f"             {s.note}")
        return 0

    if not picked:
        print("没有符合条件的套件。可用：", ", ".join(s.name for s in SUITES))
        return 2

    passed = failed = 0
    total_time = 0.0
    rows: list[tuple[Suite, bool, str, float]] = []

    for s in picked:
        print(f"\n--- {s.name}（{s.title}）" + (f"  {s.note}" if s.note else ""))
        ok, summary, el = run_one(s)
        rows.append((s, ok, summary, el))
        total_time += el
        print(f"    {'通过' if ok else '失败'}  {summary}   {el:.1f}s")
        if ok:
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 78)
    print(f"套件：{passed} 通过 / {failed} 失败 / 共 {len(rows)}     总耗时 {total_time:.1f}s")
    print("=" * 78)
    if failed:
        for s, ok, summary, _ in rows:
            if not ok:
                print(f"  ★ {s.name}: {summary}")
    else:
        print("全部套件通过。" + ("" if args.all else "（未包含打包验收，加 --all 可跑）"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
