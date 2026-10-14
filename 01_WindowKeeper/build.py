# -*- coding: utf-8 -*-
"""
一键打包成单个 exe。

跑法：
    python build.py            # 打包
    python build.py --test     # 打包并跑打包产物验收测试

产物：dist\\WindowKeeper.exe

为什么要有这个脚本而不是让人直接敲 PyInstaller：
    ① 打包前必须先杀掉正在运行的 WindowKeeper.exe，
       否则 exe 被占用，PyInstaller 会在最后一步 PermissionError 失败
       （这个坑踩过一次，报错出现在构建末尾，很有误导性）
    ② 打包完要顺手报一下体积和验收结果
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"


def kill_running() -> None:
    """打包前先停掉正在运行的实例，否则 dist\\WindowKeeper.exe 被占用。"""
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowKeeper.exe"],
                       capture_output=True, text=True)
    if "WindowKeeper.exe" in (r.stdout or ""):
        print("  发现正在运行的 WindowKeeper.exe，先结束它（否则 exe 文件被占用）")
        subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"],
                       capture_output=True)
        time.sleep(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 WindowKeeper 为单个 exe")
    ap.add_argument("--test", action="store_true", help="打包后跑产物验收测试")
    ap.add_argument("--keep-build", action="store_true",
                    help="保留 build\\ 中间目录（默认清掉，省几百 MB）")
    args = ap.parse_args()

    print("=" * 70)
    print("  打包 WindowKeeper")
    print("=" * 70)

    kill_running()

    print("\n[1/3] 跑 PyInstaller ...")
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "wk.spec"],
        cwd=str(ROOT),
    )
    if r.returncode != 0:
        print(f"\n打包失败，PyInstaller 退出码 {r.returncode}")
        return r.returncode

    exe = DIST / "WindowKeeper.exe"
    if not exe.exists():
        print(f"\n打包命令返回成功，但没找到 {exe}")
        return 1

    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n[2/3] 产物：{exe}")
    print(f"      体积：{size_mb:.1f} MB")

    # 单文件 vs 目录版：目录版说明 spec 里漏了 onefile 语义
    if (DIST / "WindowKeeper").exists():
        print("      ⚠ 同时生成了目录版 dist\\WindowKeeper\\，"
              "说明打包成了 onedir，不是单文件")

    if not args.keep_build and BUILD.exists():
        print("[3/3] 清理 build\\ 中间目录 ...")
        shutil.rmtree(BUILD, ignore_errors=True)
    else:
        print("[3/3] 保留 build\\ 中间目录")

    if args.test:
        print("\n" + "=" * 70)
        print("  跑打包产物验收测试")
        print("=" * 70)
        r = subprocess.run([sys.executable, "tests\\test_packaged.py"],
                           cwd=str(ROOT))
        if r.returncode != 0:
            print("\n产物验收未通过，先别发布。")
            return r.returncode
        print("\n产物验收通过，可以发布了。")

    print(f"\n完成。exe 在：{exe}")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    sys.exit(main())
