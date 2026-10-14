# AGENTS.md · 项目入口

**WindowKeeper** —— Windows 窗口布局记录与一键还原工具（Python 3.13 + PySide6 + Win32 API）。

---

## 接手就从这里开始

新接手的 AI / 开发者，**先读 [03_文档/00_AI接手指南.md](03_文档/00_AI接手指南.md)**，
它包含完整的项目介绍、架构、环境命令、红线约束和文档索引，
读完即可继续开发，**不需要任何历史聊天记录**。

---

## 三分钟速查

```powershell
# 装依赖
python -m pip install -r 01_WindowKeeper\requirements.txt

# 跑全部回归（从任何目录都可以，约 30 秒）
python 01_WindowKeeper\run_tests.py

# 跑全部 + 打包验收（需先 build）
python 01_WindowKeeper\run_tests.py --all

# 开发时跑源码
python 01_WindowKeeper\wk.py list | save <名> | apply <名> | ui | tray

# 打包单文件 exe → 01_WindowKeeper\dist\WindowKeeper.exe
python 01_WindowKeeper\build.py
```

---

## 目录导航

| 路径 | 内容 |
|---|---|
| `01_WindowKeeper\` | **主项目**：源码 `windowkeeper\`、测试 `tests\`、统一入口 `run_tests.py`、打包 `build.py` + `wk.spec` |
| `02_诊断脚本\` | 21 个一次性实验与诊断脚本。**每份都保留了完整代码和实测结论**，是「为什么这样设计」的证据来源 |
| `03_文档\` | 全套中文文档（接手指南、架构、进度、BUG 清单、更新日志、开发规范、环境命令） |
| `04_另一个项目_SWExportSTL\` | ⚠️ **与本项目无关**（C# SolidWorks 插件），不要动、不要算进依赖 |
| `00_只读审计报告.md` | 2026-10-14 目录整理前的只读审计（历史记录） |
| `_backup_20261014\` | 整理前的完整备份（本项目**不是 git 仓库**，这里是唯一回退途径） |

---

## 六条红线

1. `enable_per_monitor_v2()` 必须在任何窗口操作之前调用
2. 还原跨屏窗口必须分两次 `SetWindowPos`（先位置后尺寸）——已封装在 `apply_geometry()`，别绕过
3. 热键用 `RegisterHotKey`，不要换回 `keyboard` 库（本机实测组合键收不到）
4. `nativeEventFilter` 里不要直接动窗口，用 `QTimer.singleShot(0, ...)` 推后
5. 不要为了让测试通过而删掉 `tests\` 里的断言——它们固化的是真踩过的坑
6. 日志系统自身不能成为崩溃源

> 每条红线的**实测数据与失败现象**见 [03_文档/02_架构与模块.md](03_文档/02_架构与模块.md) 的「设计约束」章节。

---

## 改完代码的检查清单

- [ ] `python 01_WindowKeeper\run_tests.py` 全绿（默认 5 套件）
- [ ] 改了界面 → `python 01_WindowKeeper\wk.py ui` 真看一眼
- [ ] 改了引擎/匹配 → 顺带 `wk.py list`、`wk.py apply <预设> -n`（预演，不动窗口）
- [ ] 改了打包配置 → `python 01_WindowKeeper\build.py` 后 `run_tests.py --all`
- [ ] 在 [03_文档/05_更新日志.md](03_文档/05_更新日志.md) 追加一条记录
- [ ] 有新踩的坑 → 写进 [03_文档/04_BUG清单.md](03_文档/04_BUG清单.md)
