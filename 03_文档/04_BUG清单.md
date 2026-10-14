# 04 · BUG 清单

最后更新：2026-10-14 ｜ 版本：0.2.0

> 图例：🔴 未解决 ｜ 🟡 受限于外部因素 ｜ ✅ 已修复（留档，防止复发）

---

## 一、已知限制（不是 bug，是外部约束）

这些**改不了**，因为受 Windows 或目标程序限制。遇到时按「尽力而为」处理即可。

| 编号 | 限制 | 原因 | 处理方式 |
|---|---|---|---|
| 🟡 L1 | 管理员权限运行的程序管不了 | UIPI（用户界面特权隔离）：普通权限进程无法操作高完整性进程的窗口 | `apply_geometry` 返回失败，报 `[DENY]`；不尝试提权 |
| 🟡 L2 | 全屏独占程序管不了 | DirectX 独占全屏绕过窗口管理器 | 跳过 |
| 🟡 L3 | UWP 应用部分受限 | `ApplicationFrameWindow` 被 UWP 框架托管，尺寸请求被拦截 | 已列入 `UNCONTROLLABLE_HINTS`，UI 标注 |
| 🟡 L4 | Tkinter 程序不响应尺寸 | Tk 在 `Configure` 事件里把尺寸改回去；三个 DPI 模式下表现一致 | 列入「尽力而为」，还原后校验，失败提示 |
| 🟡 L5 | 孪生窗口需人工指定 | `video-player.exe` 有两个窗口进程名+类名+标题全部相同，指纹无法区分 | 匹配分数相同时报 `ambiguous=True`，UI 让用户手动指定 |
| 🟡 L6 | 无法保证 100% 成功率 | 以上所有 | **产品话术上不承诺全部成功**，还原后如实报告「N/M 精确」 |

---

## 二、看起来像 bug，但不是

**这一节是排障时最省时间的部分。** 遇到下列现象不要急着改代码。

| 现象 | 真相 | 证据 |
|---|---|---|
| 还原后某窗口比期望大了 2 像素 | **窗口自身的最小尺寸约束**（`WM_GETMINMAXINFO`），不是程序算错 | 日志里 `requested=23,-1600 2560x1601` vs `actual=... 2562x1601`，且 `min_track=2562x1601` |
| 预设里出现 `(-32000,-32000) 160x28` | **最小化窗口的哨兵值**，是 Windows 的既定行为 | `GetWindowRect` 对最小化窗口就返回这个 |
| `GetMonitorInfo` 报的尺寸比实际小 | 进程处于 DPI-UNAWARE 时返回**虚拟化逻辑坐标**（150% 屏被报成 1707x1067） | 调 `enable_per_monitor_v2()` 后恢复 2560x1600 |
| `GetProcessDpiAwareness()` 返回 `2` 却不是 V2 | **老 API 只有三档，没有 V2** | 要确认 V2 用 `dpi_awareness_context()`，返回 `-4` 才是 |
| `save` 遇到同名预设要求加 `--force` | **这是正确行为**，防止误覆盖 | 测试脚本必须先删掉遗留同名预设 |
| Win11 记事本一打开就是最大化 | 系统默认行为，不是程序改的 | 测试需先 `unmaximize()` 再摆位置，否则测到的不是目标流程 |
| CLI 输出里有 `[LIMIT]` 前缀 | 表示「受窗口约束」，不是失败 | `OutcomeKind.CONSTRAINED` 的中文友好映射 |
| PowerShell 里跑脚本报 `exit code 1` 但实际成功 | PowerShell 把 stderr 当错误 | 只要 stdout 显示「全部通过」就是成功 |
| 日志文件用记事本打开乱码 | 控制台是 GBK 但**文件是正确的 UTF-8** | 用支持 UTF-8 的编辑器打开 |
| 打包测试说「主界面没开出来」 | PyInstaller 单文件是**两个进程**，取 tasklist 第一行拿到的是引导进程 | 必须收集所有同名 PID 一起枚举窗口 |
| 端点测试报 `实际 (-32000, -32000, 160, 28)` | 这是**最小化哨兵值**，通常意味着测试的窗口被**外部因素**带偏了（例如同进程名的其他窗口被一并还原），不一定是引擎出错 | **先看日志**：引擎会逐窗口记 `requested` vs `actual`。实测那次日志写着 3/3 精确，是测试隔离问题（见 B22） |

---

## 三、已修复的 bug（留档，防止复发）

每条都配了回归测试，改动相关代码时请确认这些测试仍然通过。

### ✅ B1 · 跨屏还原尺寸被缩放（最严重的 bug）

**现象**：跨屏移动窗口时尺寸被乘了一个系数（150% 屏 → ×1.5）。
**根因**：同一次 `SetWindowPos` 同时改显示器归属和尺寸，系统按旧屏 DPI 解释尺寸。
**修复**：`apply_geometry()` 内部做两次调用，先位置后尺寸。
**回归**：`test_engine.py` 几何解析与真实还原组；`02_诊断脚本\probe_isolation.py`

### ✅ B2 · 热键注册成功但按下去毫无反应

**现象**：`RegisterHotKey` 返回成功、错误码 0，按键无任何反应。
**根因**：手算 MSG 结构 `wParam` 偏移得 12，**实际是 16**（64 位下 WPARAM 8 字节 + 4 字节对齐填充）。
**诊断**：`diag_hotkey_owner.py` 新进程注册同名热键返回 **1409**，证明是消息解析问题而非注册问题。
**修复**：改用 `ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents` 让 ctypes 自己算偏移。
**回归**：`test_hotkey.py` 断言 `message==8`、`wParam==16`，并**明确断言手算的 12 是错的**。

### ✅ B3 · `keyboard` 库收不到组合键

**现象**：普通按键 `a` 能收到，`ctrl+alt+1` 回调 **0 次**。
**根因**：低级键盘钩子需挂在持续泵消息的线程上，否则被 `LowLevelHooksTimeout`（300ms）静默摘掉；且与 Qt 主线程消息循环竞争。
**修复**：整体改用 Win32 `RegisterHotKey`，`keyboard` 依赖已移除。
**回归**：`02_诊断脚本\probe_hotkey_impl.py` 对照实验

### ✅ B4 · `--force` 覆盖预设变成新增

**现象**：用 `--force` 覆盖同名预设，预设数量却一直涨。
**根因**：`PresetStore.update()` 按 `id` 定位；覆盖分支新建了 `Preset`（自带随机新 id），
于是 update 找不到旧记录，走了 else 分支变成 **append**。
**修复**：覆盖分支必须先 `preset.id = existing.id`、`preset.created_at = existing.created_at`、
`preset.notes = existing.notes` 再 update。同样的问题在 `ui.py` 的 `capture_layout()` 里也有一份，一并修了。
**回归**：`test_engine.py` 存储往返组

### ✅ B5 · `st.remove()` 传错参数

**现象**：删除预设无效或报错。
**根因**：`store.remove()` 收的是 `preset_id`，我传了名称字符串。
**修复**：先 `st.by_name(args.name)` 拿对象再 `st.remove(p.id)`。

### ✅ B6 · 日志级别参数只接受字符串

**现象**：`[WindowKeeper] 无法创建日志文件: AttributeError("'int' object has no attribute 'upper'")`
**根因**：`log.py` 里写的是 `getattr(logging, level.upper(), logging.INFO)`。
测试一直传字符串 `'DEBUG'` 所以没暴露，`cli.py`/`tray.py` 传 `logging.INFO`（int）必炸。
**修复**：新增 `_resolve_level(level)` 统一处理 int / str / 未知值。
**回归**：`test_log.py` 级别参数组（int INFO/DEBUG、str INFO、小写 debug、未知串、None）

### ✅ B7 · `get()` 返回裸 Logger，不支持结构化字段

**现象**：`log.get("cli").info("消息", command=args.command)` 抛 `TypeError`。
**根因**：标准 `Logger.info(msg, **kwargs)` 只认 `exc_info/stack_info/stacklevel/extra`。
**修复**：定义 `WKLogger(logging.Logger)` 子类，`_log()` 把 `**fields` 塞进 `extra["wk"]`；
并在**任何 `logging.getLogger()` 之前**执行 `logging.setLoggerClass(WKLogger)`。
**回归**：`test_log.py` 结构化字段组

### ✅ B8 · `_configured` 幂等标志的两个缺陷

**现象**：① 托盘先 `setup(INFO)` 后，CLI 再 `setup(DEBUG)` 不生效 ② 重复调用会重复挂 handler，同一条日志写 N 遍。
**根因**：① 首次配置就把级别永久锁死 ② **配置失败时 `_configured` 不置位**（异常在置位前发生）。
**修复**：`_configured = bool(logger.handlers)` 只在真挂上 handler 后置位；已配置分支仅调整级别后返回。
**回归**：`test_log.py` 断言「重复 setup 不增加 handler 数、同一条日志只写一遍」

### ✅ B9 · `_HumanFormatter` 漏掉结构化字段

**现象**：「开始还原预设」在人类可读日志里只剩一句光秃秃的话，`preset=`/`windows=`/`dry_run=` 全丢。
**根因**：`format()` 没读 `record.wk`，字段只进了 JSONL。
**为什么严重**：**用户发回来给我看的恰恰是人类可读那份**，最关键的信息反而看不到。
**修复**：`format()` 读 `getattr(record, "wk", None)`，拼成 `[k=v  k=v]` 附在行尾，用 `_short(v, limit=60)` 截断。
**教训**：双格式日志时**不要让任一份缺关键字段**。
**回归**：`test_log.py` 断言两份格式都含相同字段

### ✅ B10 · `clear()` 后所有日志不再更新

**现象**：用户在界面上点「清空日志」，之后所有日志都不再写入。
**根因**：`RotatingFileHandler` 一旦打开就长期持有文件句柄。Windows 上删不掉（被占用），
即使删掉，新写入也落进已删除的句柄。
**修复**：先 `h.close()` 所有 handler、`logger.handlers.clear()`、带 3 次重试的 unlink、然后重新 `setup()`。

### ✅ B11 · 诊断报告谎报显示器状态

**现象**：诊断包里的报告写 `\\.\DISPLAY3 1707x1067 @100% dpi=96`，**真实是 2560x1600 @150% dpi=144**。
**根因**：`_env_report()` 是独立调用的，**没有先执行 `enable_per_monitor_v2()`**，于是拿到虚拟化逻辑坐标。
**为什么严重**：它恰恰是实现排障最关键的信息，谎报等于毁掉线索。
**修复**：DPI 段开头先调 `w32.enable_per_monitor_v2()`（幂等）。
**回归**：`test_log.py` 断言报告含正确的显示器数量与 DPI 字段

### ✅ B12 · 把 `2` 误读成 Per-Monitor V2

**现象**：诊断报告里出现 `<< 不是 2，显示器数据可能不准` 的警告，但显示器数据其实是对的。
**根因**：`PROCESS_DPI_AWARENESS = {0:UNAWARE, 1:SYSTEM_AWARE, 2:PER_MONITOR_AWARE}` 对应**老 API**，
**只有三档没有 V2**；设了 V2 老 API 照样返回 2。我据此加了误报警告。
**修复**：新增 `DPI_CONTEXT_NAMES` 与 `dpi_awareness_context()`（走 `GetProcessDpiAwarenessContext`，返回 `-4` 才是 V2）。
**踩坑**：`user32.GetProcessDpiAwarenessContext` 直接属性访问会抛 `AttributeError`（老系统不导出），必须 `getattr(..., None)` 兜底。
**回归**：`test_log.py` 断言报告含「DPI 感知上下文」与「DPI 感知(旧API)」两行

### ✅ B13 · `MainWindow` 启动时 `AttributeError: 'MainWindow' object has no attribute 'monitors'`

**堆栈**：
```
File "windowkeeper\ui.py", line 683, in _on_select
    self._show_detail(p)
File "windowkeeper\ui.py", line 693, in _show_detail
    self.map.set_monitors(self.monitors)
AttributeError: 'MainWindow' object has no attribute 'monitors'
```
**根因**：`self.monitors` 只在 `refresh_monitors()` 里赋值，但 `__init__` 里 `reload_presets()` 先执行，
它会选中第一项并触发 `_on_select` → `_show_detail`，此时 `monitors` 还不存在。
**修复**：① `__init__` 里**先于** `reload_presets` 初始化 `self.monitors = w32.enum_monitors()`
② `_show_detail` 兜底 `getattr(self, "monitors", None) or w32.enum_monitors()`。
**回归**：`test_ui.py` 断言「`self.monitors` 构造时即可用」

### ✅ B14 · 打包后 CLI 的 `ui` 子命令 ImportError

**现象**：
```
ImportError("cannot import name 'run_ui' from 'windowkeeper.ui' (...\windowkeeper\ui.pyc)")
```
**根因**：`cli.py` 里写的是 `from .ui import run_ui`，但 **`run_ui` 定义在 `tray.py` 里**
（它和 `run_tray` 共用 `_prepare_app()`）。源码模式下没人跑 CLI 的 ui 分支，所以一直没暴露，**打包后才炸**。
**修复**：改为 `from .tray import run_ui`。
**教训**：**打包测试必须单独做**——很多只在 frozen 环境暴露的问题，源码测试永远看不到。
**回归**：`test_packaged.py`

### ✅ B15 · 托盘还原异常无人接住

**现象**：托盘模式下还原出错，Qt 直接吞掉异常，用户只看到「图标突然没了」。
**根因**：`TrayApp.apply_preset()` 原本**完全没有 except**。
**修复**：加 try/except，`log.exception()` 记录堆栈 + `_notify()` 气泡提示用户。

### ✅ B16 · 打包时 `PermissionError [WinError 5]`

**现象**：构建的**最后一步**报 `PermissionError [WinError 5]`，误导性极强。
**根因**：正在运行的 `WindowKeeper.exe` 占用了文件。
**修复**：`build.py` 里自动 `taskkill`。
**教训**：报错在最后一步不代表问题在最后一步。

### ✅ B17 · 打包后 Qt DLL 一个都不会被 `excludes` 排除

**现象**：exe 45.9MB，里面躺着 `opengl32sw.dll`、`Qt6Quick.dll`、`Qt6Pdf.dll` 等一大批用不到的东西。
**根因**：`excludes` 只作用于 Python 模块；Qt 的 DLL 是 PySide6 的 PyInstaller hook 无差别收集的。
**修复**：在 `wk.spec` 里手工过滤 `a.binaries`/`a.datas`，剔除 20 个组件，**45.9MB → 26.2MB**。
**必须保留**：Qt6Core/Qt6Gui/Qt6Widgets、`qwindows.dll`、`qmodernwindowsstyle.dll`、MSVC 运行库。

### ✅ B18 · 移动目录后测试报 `ModuleNotFoundError`（2026-10-14 整理时发现）

**现象**：从项目根目录运行 `python 01_WindowKeeper\tests\test_hotkey.py` 报
`ModuleNotFoundError: No module named 'windowkeeper'`。
**根因**：这些脚本写死 `sys.path.insert(0, ".")`，只在「当前目录恰好是 `01_WindowKeeper`」时才对。
**修复**：6 处改成 `Path(__file__).resolve().parent.parent` 推导；并新建统一入口
`01_WindowKeeper\run_tests.py`（内部固定 `cwd=ROOT`，且做编解码自适应）。
**回归**：从任意目录运行 `run_tests.py` 全绿。

### ✅ B19 · 端到端验收写文件到已删除的目录（2026-10-14 整理时发现）

**现象**：
```
FileNotFoundError: [Errno 2] No such file or directory:
'D:\\AI Dsh\\软件制作\\01_WindowKeeper\\probe\\_e2e_out.txt'
```
**根因**：`test_e2e_user.py` 把 exe 输出重定向写死在 `ROOT / "probe"` 下，而 `probe\` 整理时被移走了。
**修复**：改用 `tempfile.mkdtemp()`，不再依赖任何项目目录存在。
**教训**：测试写临时文件的路径不能假设项目里某个目录存在。

### ✅ B20 · `run_tests.py` 把 stderr 诊断误判为失败

**现象**：`test_log.py` 明明 52/52 通过、退出码 0，统一入口却报「失败」。
**根因**：① `test_log.py` **没有重配 stdout 编码**，在 GBK 控制台输出 GBK 字节；
统一入口用 `encoding="utf-8"` 读 → 解出乱码 → 正则匹配不到「通过 N / 共 M」→ 误判。
② 该脚本有一条测试**故意**测「无占位符时传位置参数」，logging 会把诊断写到 stderr。
**修复**：统一入口改为**编码自适应**（依次试 utf-8 / gbk / cp936）+「计数说通过但退出码非 0」时以计数为准。
**教训**：不要要求每个测试脚本自律地处理编码，入口层做兼容更可靠。

---

### ✅ B21 · 零尺寸窗口被枚举进预设（2026-10-14 整理后回归时发现）

**现象**：`test_engine.py` 从 51/51 掉到 49/51，两条断言失败：
```
❌ 所有窗口都有几何
❌ 指纹几何完整  —  无效: ['setup.exe']
```
**根因**：系统上运行着一个 InstallShield 安装程序（`setup.exe`，窗口类 `InstallShield_Win`），
它弹出瞬间的窗口状态是 `IsWindowVisible=True`、`IsIconic=False`，但
`GetWindowRect` 返回 `(1280, 720, 1280, 720)`——**宽高都是 0**。
现有的过滤规则（不可见 / 无标题 / 工具窗口 / 最小化）全都不覆盖这种情况，
于是它被当成正常窗口枚举出来，破坏了「所有窗口都有几何」这条不变量。

**修复**（`windowkeeper/matcher.py` 的 `enumerate_live_windows`，在最小化判断之后加一道）：
```python
# 零尺寸窗口：可见、非最小化，但 GetWindowRect 的宽或高是 0。
if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
    return True
```
理由：这类窗口存进预设毫无意义（还原不出任何东西），且是**瞬时状态**，
不该让它污染预设。

**回归**：`test_engine.py` 的 T2「所有窗口都有几何」+ T4「指纹几何完整」恢复通过，51/51。

**教训**：窗口过滤规则要覆盖「几何无效」这一整类，而不只是逐个列举已知的无效类型。

---

### ✅ B22 · 端到端验收用 `notepad.exe` 导致偶发失败（2026-10-14 整理后回归时发现）

**现象**：`test_e2e_user.py` 的最终断言偶发失败，而且**每次报的实际值都不一样**：
```
[FAIL] ★ 窗口被 exe 的热键精确还原 — 期望 (320, 260, 760, 520)，实际 (0, 0, 0, 0)
[FAIL] ★ 窗口被 exe 的热键精确还原 — 期望 (320, 260, 760, 520)，实际 (-32000, -32000, 160, 28)
```
**干扰性极强**：`(-32000,-32000) 160x28` 是最小化哨兵值，看起来像「还原把窗口弄最小化了」；
`(0,0,0,0)` 看起来像「窗口被销毁了」。但**引擎日志显示 3/3 全部精确还原，零失败**：
```
15:49:06.405  决定  已还原：Notepad.exe  [... hwnd=26677034 ... requested=320,260 760x520  actual=320,260 760x520 ...]
15:49:06.406  决定  已还原：Notepad.exe  [... hwnd=10556154 ...]
15:49:06.453  INFO  还原「__验收临时__」结束：3/3 精确，0 受限，0 有偏差，0 失败
```
**根因**：测试用 `--only Notepad.exe` 存预设，但 **`--only` 只能按进程名过滤，无法按 hwnd 精确指定**。
开发机上几乎总是开着别的记事本（实测那一轮，用户正在用的「项目交接文案.txt」也被存进了预设），
于是预设里有 3 个 Notepad 窗口，还原时被一并操作，测试监视的那个窗口被外部因素带偏。
**这是测试隔离缺陷，不是引擎 bug**——引擎日志是清白的。

**修复**（`tests/test_e2e_user.py`，三处）：
1. 实验对象从 `notepad.exe` 换成 **`charmap.exe`（字符映射表）**——进程名测试独占，
   普通用户不会开着它，`--only charmap.exe` 必定只命中本测试自己的窗口；
   它同样是传统 Win32 程序，响应 `SetWindowPos`，几何稳定。
   启动前先 `taskkill` 同类残留，保证起点干净。
2. 新增守卫「目标程序是测试独占的（无同名干扰窗口）」——枚举到第二个同名窗口即报错。
3. 新增守卫「预设里只有本测试的那一个窗口」——若预设窗口数 ≠ 1 直接说清楚，
   而不是让后面的还原断言测到不该测的东西。断言数 11 → 13。

**回归**：连跑三遍全部 13/13，偶发消失。全量 `run_tests.py --all` 6 套件全绿。

**教训**：**验收测试用的实验对象必须是测试独占的资源**。只要测试和目标程序可能被
「同类的其他实例」干扰，就一定会偶发失败，而且报出来的几何值会把人引向完全错误的
方向（这次差点去查引擎的最小化处理）。另外——**引擎自己的日志救了这个排查**：
它明确记录 3/3 精确，直接排除了引擎的嫌疑。

---

## 四、当前未解决的项

**没有已知的功能性 bug。** 上述 🔴 未解决项为零。

已知的**_风险点_**（尚未成为 bug，但值得留意）：

| 风险 | 说明 | 建议 |
|---|---|---|
| 不是 git 仓库 | 唯一回退途径是 `_backup_20261014\` | 尽快 `git init` 并首次提交 |
| 显示器热插拔依赖 2.5 秒轮询 | 因为热插拔不发通知给普通窗口，只能定时比对 | 可改用 `WM_DISPLAYCHANGE` 消息降低延迟 |
| 长期不清理日志 | 已轮转 1MB × 3，但不会自动删除 | 界面上已有「清空日志」按钮 |
| 端到端测试依赖 `charmap.exe` 存在 | 它是 Windows 内置程序，但极精简的系统可能没有 | 找不到时测试会明确报「目标窗口已就绪：没找到」，不会静默通过 |

---

## 五、发现新 bug 时怎么加进来

1. 给编号（B21、B22…），已修复的用 ✅，未解决的用 🔴
2. 必写四段：**现象**（含原始报错文本，方便将来 grep）、**根因**、**修复**、**回归**（哪个测试固化它）
3. 如果是「看起来像 bug 但不是」，加到第二节的表格里
4. 同步在 [05_更新日志.md](05_更新日志.md) 记一条
