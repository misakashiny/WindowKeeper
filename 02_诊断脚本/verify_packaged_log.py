"""验证打包后的 exe 在托盘模式下能否正确写日志（T14 的手工版）。

为什么必须单独验：
  源码模式能写日志 != exe 能写。单文件 exe 运行时会把 Python 代码解包到
  %TEMP%\\_MEIxxxx，sys.executable 指向的是 exe 本身而非 python.exe，
  任何依赖"从 __file__ 往上找目录"的路径推导都会在打包后失效。
  日志路径必须走 %APPDATA%，不受解包影响——这个脚本就是验证这一点。
"""
import csv
import io
import subprocess
import sys
import time
from pathlib import Path

# 脚本在 probe\ 下，仓库根在上一层，必须把根加进 sys.path，
# 否则 python probe\xx.py 会以 probe\ 为起点找不到 windowkeeper 包。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from windowkeeper import log  # noqa: E402

log.clear()
exe = str(Path("dist/WindowKeeper.exe").resolve())

subprocess.run(["taskkill", "/F", "/IM", "WindowKeeper.exe"], capture_output=True)
p = subprocess.Popen([exe, "tray"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(7)

# 单文件 exe 是父子两个进程，必须把同名 PID 全收齐，
# 只看 tasklist 第一行会拿到引导进程，从而误判"没起来"。
r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WindowKeeper.exe", "/FO", "CSV"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")
rows = list(csv.reader(io.StringIO(r.stdout)))
pids = [row[1] for row in rows[1:] if len(row) > 1 and row[1].isdigit()]
print(f"存活进程数: {len(pids)}  {pids}")

print("--- 日志文件 ---")
print(f"路径: {log.log_file()}")
print(f"存在: {log.log_file().exists()}")
if log.log_file().exists():
    print(log.log_file().read_text("utf-8"))

print("--- JSONL 是否存在 ---")
print(f"{log.jsonl_file()}  {log.jsonl_file().exists()}")

for pid in pids:
    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
time.sleep(1)
print("已清理")
