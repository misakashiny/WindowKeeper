# -*- coding: utf-8 -*-
"""诊断托盘的热键注册情况：直接跑一遍 HotkeyWatcher，看它报什么错。"""
import sys
import time
from pathlib import Path

# 本脚本在 02_诊断脚本\ 下，项目源码在 01_WindowKeeper\，必须显式加进 sys.path。
# 不能用 sys.path.insert(0, ".")：那只在「当前目录恰好是 01_WindowKeeper」时才对。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "01_WindowKeeper"))

from windowkeeper import store
from windowkeeper.tray import HotkeyWatcher

st = store.PresetStore()
presets = st.load()
print(f"预设数: {len(presets)}")
for p in presets:
    print(f"  · {p.name}  hotkey={p.hotkey!r}  enabled={p.enabled}")

w = HotkeyWatcher()
errs = w.reload(presets)
print(f"\n注册成功的热键: {w.active}")
print(f"错误: {errs}")

if w.active:
    print("\n等待 25 秒，请现在手按 ctrl+alt+1 …")
    t0 = time.time()
    while time.time() - t0 < 25:
        got = w.poll()
        if got:
            print(f"  ★ {time.time() - t0:.1f}s 收到触发: {got}")
            break
        time.sleep(0.2)
    else:
        print("  25 秒内没有收到任何触发")
    w.stop()
