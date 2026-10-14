# -*- coding: utf-8 -*-
"""
诊断 keyboard 库为什么收不到事件。

要分清三件事：
  1. lib 能不能加载（DLL 是否可用）
  2. 底层钩子是否真的装上了（hook 是否在收事件）
  3. 是不是所有键都收不到，还是只有组合键收不到
"""
import sys
import time
from pathlib import Path

# 本脚本在 02_诊断脚本\ 下，项目源码在 01_WindowKeeper\，必须显式加进 sys.path。
# 不能用 sys.path.insert(0, ".")：那只在「当前目录恰好是 01_WindowKeeper」时才对。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "01_WindowKeeper"))

print("=== 1. keyboard 库基本状态 ===")
import keyboard
print("版本:", getattr(keyboard, "__version__", "?"))
print("平台:", keyboard.is_modifier if hasattr(keyboard, "is_modifier") else "?")
print("_os_type:", getattr(keyboard, "_os_type", "?"))

print("\n=== 2. 所有键盘事件都记录一遍（5秒，随便敲键） ===")
hits = []
try:
    h = keyboard.on_press(lambda e: hits.append(e.name), suppress=False)
    print("on_press 注册成功")
except Exception as exc:
    print("on_press 失败:", repr(exc))
    sys.exit(1)

t0 = time.time()
# 程序化地发一个按键，不依赖人去敲
import ctypes
user32 = ctypes.windll.user32
time.sleep(0.5)
print("程序化发送按键 A …")
user32.keybd_event(0x41, 0, 0, 0)
time.sleep(0.05)
user32.keybd_event(0x41, 0, 2, 0)

while time.time() - t0 < 5:
    time.sleep(0.1)

print(f"5 秒内捕获到的事件: {len(hits)} 个")
if hits:
    print("  样例:", hits[:20])
else:
    print("  ★ 一个事件都没收到 —— 底层钩子没工作")

print("\n=== 3. 直接测组合键回调 ===")
combo_hits = []
try:
    keyboard.add_hotkey("ctrl+alt+1", lambda: combo_hits.append(time.time()))
except Exception as exc:
    print("add_hotkey 失败:", repr(exc))

# again need to message-pump; keyboard uses its own listener thread
time.sleep(0.5)
user32.keybd_event(0x11, 0, 0, 0)   # ctrl down
user32.keybd_event(0x12, 0, 0, 0)   # alt down
time.sleep(0.05)
user32.keybd_event(0x31, 0, 0, 0)   # 1 down
time.sleep(0.05)
user32.keybd_event(0x31, 0, 2, 0)   # 1 up
user32.keybd_event(0x12, 0, 2, 0)
user32.keybd_event(0x11, 0, 2, 0)
time.sleep(1.0)
print(f"组合键回调次数: {len(combo_hits)}")

print("\n=== 4. 结论 ===")
if not hits and not combo_hits:
    print("keyboard 库在本机完全收不到键盘事件。")
    print("最可能：需要管理员权限，或该库的 hook 与当前会话不兼容。")
elif hits and not combo_hits:
    print("普通按键能收到，但组合键不行 —— 是组合解析的问题。")
else:
    print("keyboard 库工作正常。")
