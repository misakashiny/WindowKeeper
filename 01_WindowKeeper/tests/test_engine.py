# -*- coding: utf-8 -*-
"""
核心引擎的端到端测试。

不依赖 Qt，可以在纯命令行下跑。这是写 UI 之前的关卡：
引擎不可靠的话，UI 再漂亮也没意义。

用法:
    python tests/test_engine.py            # 全流程测试
    python tests/test_engine.py --keep     # 测试后保留改动，方便肉眼确认
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowkeeper import engine, matcher, store, win32_api as w32  # noqa: E402


PASS, FAIL, WARN = "✅", "❌", "⚠️ "
_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    mark = PASS if ok else FAIL
    print(f"  {mark} {name}" + (f"  —  {detail}" if detail else ""))
    return ok


def note(msg: str) -> None:
    print(f"  {WARN}{msg}")


def section(title: str) -> None:
    print()
    print("=" * 88)
    print(title)
    print("=" * 88)


# --------------------------------------------------------------------------
# T1 DPI 感知
# --------------------------------------------------------------------------

def test_dpi(monitors) -> None:
    section("T1  DPI 感知与显示器枚举")

    aware = w32.current_dpi_awareness()
    check("进程已是 PER_MONITOR_AWARE", aware == "PER_MONITOR_AWARE",
          f"当前 = {aware}")

    check("枚举到显示器", len(monitors) > 0, f"{len(monitors)} 块")

    if len(monitors) > 1:
        scales = {m.scale for m in monitors}
        check("混合缩放场景已覆盖", len(scales) > 1,
              f"缩放率 = {sorted(s * 100 for s in scales)}%")

    for m in monitors:
        ok = m.width > 0 and m.height > 0 and m.dpi >= 96
        check(f"显示器 [{m.index}] {m.name} 数据有效", ok,
              f"{m.width}x{m.height} @{m.dpi}dpi 工作区={m.work}")


# --------------------------------------------------------------------------
# T2 窗口枚举
# --------------------------------------------------------------------------

def test_enumeration(monitors) -> list:
    section("T2  窗口枚举与指纹提取")

    live = matcher.enumerate_live_windows(monitors, include_minimized=False)
    check("枚举到可用窗口", len(live) > 0, f"{len(live)} 个")

    # 过滤必须真的起作用
    bad = [w for w in live if w.rect[0] <= w32.MINIMIZED_SENTINEL]
    check("最小化哨兵值已被过滤", len(bad) == 0,
          f"残留 {len(bad)} 个" if bad else "无残留")

    bad_shell = [w for w in live if w.cls in w32.SHELL_CLASSES]
    check("系统壳窗口已被过滤", len(bad_shell) == 0,
          f"残留 {[w.cls for w in bad_shell]}" if bad_shell else "无残留")

    check("所有窗口都有几何", all(w.width > 0 and w.height > 0 for w in live))
    check("所有窗口都归属到显示器",
          all(w.monitor_index >= 0 for w in live),
          f"未归属: {[w.exe_name for w in live if w.monitor_index < 0]}")

    print()
    print(f"  {'进程':<26}{'显示器':<10}{'几何':<22}标题")
    print("  " + "-" * 84)
    for w in sorted(live, key=lambda x: (x.monitor_index, x.exe_name))[:20]:
        geom = f"{w.rect[0]},{w.rect[1]} {w.width}x{w.height}"
        print(f"  {w.exe_name[:25]:<26}[{w.monitor_index}]{w.monitor_name[-8:]:<8}"
              f"{geom:<22}{w.title[:30]}")
    return live


# --------------------------------------------------------------------------
# T3 标题清洗
# --------------------------------------------------------------------------

def test_title_cleaning() -> None:
    section("T3  标题清洗（把易变部分剥掉）")

    cases = [
        ("Agent2API · 多提供商本地网关 和另外 1 个页面 - 个人 - Microsoft Edge",
         "Agent2API · 多提供商本地网关"),
        ("xiaozhi - Google 搜索 和另外 1 个页面 - 个人 - Microsoft Edge",
         "xiaozhi - Google 搜索"),
        ("main.py - 项目 - Visual Studio Code", "main.py - 项目"),
        ("*未保存的文档.txt - Notepad", "未保存的文档.txt - Notepad"),
        ("AI Dsh - 文件资源管理器", "AI Dsh"),
    ]
    for raw, expected in cases:
        got = matcher.clean_title(raw)
        ok = got == expected
        check(f"清洗 {raw[:38]!r}", ok,
              f"→ {got!r}" + ("" if ok else f"  (期望 {expected!r})"))


# --------------------------------------------------------------------------
# T4 捕获 → 匹配 往返一致性
# --------------------------------------------------------------------------

def test_capture_match_roundtrip(monitors, live) -> None:
    section("T4  捕获 → 匹配 往返一致性（最关键的一环）")

    refs = [w.to_ref(monitors) for w in live]
    check("从活动窗口捕获到指纹", len(refs) == len(live), f"{len(refs)} 个")

    check("指纹几何完整", all(r.is_valid for r in refs),
          f"无效: {[r.exe_name for r in refs if not r.is_valid]}")

    check("每个指纹都记录了显示器", all(r.monitor_name for r in refs))

    # 用刚捕获的指纹去匹配当前窗口 —— 必须全部命中且高分
    results = matcher.match_preset(refs, live, monitors)
    check("匹配数量一致", len(results) == len(refs))

    matched = [r for r in results if r.window is not None]
    check("全部匹配成功", len(matched) == len(refs),
          f"{len(matched)}/{len(refs)}")

    perfect = [r for r in results if r.score >= matcher.STRONG_MATCH]
    rate = len(perfect) / max(1, len(results)) * 100
    print(f"  ℹ️  高分匹配(≥{matcher.STRONG_MATCH}): {len(perfect)}/{len(results)}"
          f"  ({rate:.0f}%)")

    # 往返必须指向同一个 hwnd
    wrong = []
    for r, orig in zip(results, live):
        if r.window is None or r.window.hwnd != orig.hwnd:
            wrong.append((orig.exe_name, orig.title[:24],
                          r.window.title[:24] if r.window else "None", r.score))
    check("往返匹配指向同一个窗口", len(wrong) == 0,
          f"{len(wrong)} 个错配: {wrong[:3]}" if wrong else "全部正确")

    # 无重复占用
    hwnds = [r.window.hwnd for r in results if r.window]
    check("没有窗口被重复匹配", len(hwnds) == len(set(hwnds)),
          f"{len(hwnds)} vs {len(set(hwnds))}")

    ambiguous = [r for r in results if r.ambiguous]
    if ambiguous:
        note(f"{len(ambiguous)} 个窗口存在孪生候选（这是阶段0实测的已知现实）：")
        for r in ambiguous[:3]:
            print(f"      · {r.ref.exe_name} {r.ref.title_fp[:34]!r}  score={r.score}")
            for lw, s in r.candidates[:3]:
                print(f"         候选 {s:>3}分  {lw.title[:42]!r}")
    else:
        print("  ℹ️  本次没有孪生窗口歧义")

    return refs


# --------------------------------------------------------------------------
# T5 几何解析与越界保护
# --------------------------------------------------------------------------

def test_geometry_resolution(refs, monitors) -> None:
    section("T5  几何解析与越界保护")

    r0 = refs[0]
    geo = store.resolve_geometry(r0, monitors)
    check("几何解析返回结果", geo.w > 0 and geo.h > 0,
          f"{geo.strategy} → ({geo.x},{geo.y}) {geo.w}x{geo.h}")
    check("配置未变时应判定为精确", geo.exact, f"exact={geo.exact}")

    # 伪造一个「显示器已消失」的指纹
    from windowkeeper.matcher import WindowRef
    ghost = WindowRef(exe_name="ghost.exe", cls="Ghost", title="幽灵",
                      title_fp="幽灵", x=-9999, y=-9999, w=800, h=600,
                      monitor_name="\\\\.\\DISPLAY_GHOST", monitor_index=0,
                      offset_in_work=(100, 100), rel_in_work=(0.2, 0.2),
                      rel_size=(0.3, 0.4))
    ggeo = store.resolve_geometry(ghost, monitors)
    check("显示器消失时能回退", not ggeo.exact and ggeo.w > 0,
          f"{ggeo.strategy} → ({ggeo.x},{ggeo.y}) {ggeo.w}x{ggeo.h}")

    # 越界保护
    x, y, w, h, note_msg = store.clamp_to_monitors(-99999, -99999, 800, 600, monitors)
    on_screen = any(
        min(x + w, m.rect[2]) - max(x, m.rect[0]) >= 80
        and min(y + h, m.rect[3]) - max(y, m.rect[1]) >= 80
        for m in monitors)
    check("完全越界的窗口被拉回可见区域", on_screen,
          f"({x},{y}) {w}x{h}  {note_msg}")

    # 超大尺寸夹取
    x2, y2, w2, h2, _ = store.clamp_to_monitors(0, 0, 99999, 99999, monitors)
    max_w = max(m.width for m in monitors)
    check("超大尺寸被夹到显示器范围", w2 <= max_w, f"{w2} <= {max_w}")


def pick_responsive_window(live, monitors, skip_exe=("explorer.exe",)):
    """
    挑一个「确认响应 SetWindowPos」的窗口来做破坏性测试。

    为什么必须探测：实测踩过两次坑——
      · 首次 T6 抽到 msedge，它自报最小尺寸 612x697，尺寸被顶开
      · 再跑抽到 SLDWORKS.exe（SOLIDWORKS），几何恰为整块显示器，
        说明它还在初始化，SetWindowPos 完全无效
    这不是引擎的问题，是测试抽样的问题。真实用户不会把正在启动的
    重型 CAD 放进预设，但测试必须自己排除这种窗口，否则永远报红。
    """
    skip = {s.lower() for s in skip_exe}
    candidates = [
        w for w in live
        if w.cls not in engine.UNCONTROLLABLE_HINTS
        and not w.minimized
        and w.exe_name.lower() not in skip
        and w.width > 0 and w.height > 0
    ]
    prefer = ("notepad.exe", "msedge.exe", "chrome.exe", "code.exe",
              "devenv.exe", "windowsterminal.exe", "explorer.exe")
    candidates.sort(key=lambda w: (
        prefer.index(w.exe_name.lower()) if w.exe_name.lower() in prefer else 99))

    for w in candidates[:8]:
        orig = w32.get_window_rect(w.hwnd)
        probe_w = max(320, w.width - 120)
        probe_h = max(240, w.height - 120)
        try:
            w32.apply_geometry(w.hwnd, orig[0], orig[1], probe_w, probe_h, monitors)
            time.sleep(0.12)
            got = w32.get_window_rect(w.hwnd)
        except Exception:  # noqa: BLE001
            continue
        responds = (abs(got[2] - got[0] - probe_w) <= 2
                    and abs(got[3] - got[1] - probe_h) <= 2)
        try:
            w32.apply_geometry(w.hwnd, orig[0], orig[1],
                               orig[2] - orig[0], orig[3] - orig[1], monitors)
            time.sleep(0.1)
        except Exception:  # noqa: BLE001
            pass
        if responds:
            if w is not candidates[0]:
                note(f"已跳过不响应尺寸修改的窗口，改用 {w.exe_name}")
            return w

    note("没有找到确认响应 SetWindowPos 的窗口")
    return None


# --------------------------------------------------------------------------
# T6 真实还原 + 校验
# --------------------------------------------------------------------------

def test_actual_restore(monitors, live, keep: bool) -> None:
    section("T6  真实还原实测（含跨屏，验证两步协议）")

    if len(monitors) < 2:
        note("只有一块显示器，跳过跨屏还原测试")
        return

    target = pick_responsive_window(live, monitors)
    if target is None:
        note("没有合适的可测窗口")
        return

    print(f"  目标窗口: {target.exe_name}  {target.title[:40]!r}")
    print(f"  类名: {target.cls}   当前: {target.rect}")

    orig = w32.get_window_rect(target.hwnd)

    # 构造一个「当前屏 → 另一块屏」的还原任务
    cur_idx = target.monitor_index
    other = next((m for m in monitors if m.index != cur_idx), monitors[0])
    wl, wt_, wr, wb = other.work
    tw, th = 900, 650
    tx = wl + ((wr - wl) - tw) // 2
    ty = wt_ + ((wb - wt_) - th) // 2

    # 先问清这个窗口允许的最小尺寸，好判断偏差是不是它的合法约束
    min_track = w32.get_min_track_size(target.hwnd)
    if (min_track[0] > tw) or (min_track[1] > th):
        print(f"  ℹ️  该窗口自报最小 {min_track[0]}x{min_track[1]}，"
              f"测试目标相应调整")
        tw, th = max(tw, min_track[0]), max(th, min_track[1])
        tx = wl + ((wr - wl) - tw) // 2
        ty = wt_ + ((wb - wt_) - th) // 2

    print(f"  目标: 跨到显示器 [{other.index}] {other.name} → ({tx},{ty}) {tw}x{th}")

    ok1, ok2 = w32.apply_geometry(target.hwnd, tx, ty, tw, th, monitors)
    time.sleep(0.25)
    got = w32.get_window_rect(target.hwnd)
    gw, gh = got[2] - got[0], got[3] - got[1]

    print(f"  实际: ({got[0]},{got[1]}) {gw}x{gh}   调用返回={ok1}/{ok2}")

    pos_ok = abs(got[0] - tx) <= 2 and abs(got[1] - ty) <= 2
    size_ok = abs(gw - tw) <= 2 and abs(gh - th) <= 2

    check("跨屏后位置精确", pos_ok,
          f"期望({tx},{ty}) 实际({got[0]},{got[1]})")
    check("★ 跨屏后尺寸精确（两步协议生效）", size_ok,
          f"期望{tw}x{th} 实际{gw}x{gh}")

    if size_ok:
        print()
        print("  ★ 这证明阶段0发现的两步协议在真实流程里有效：")
        print("    如果没分两步，这里会看到尺寸被乘上目标屏的缩放率。")
    else:
        print()
        print("  ⚠️  尺寸仍不精确。可能是该窗口类型不接受尺寸修改，")
        print("     也可能是两步协议需要调整。请检查该窗口类名：", target.cls)

    # 同屏改尺寸（作为对照）
    if keep:
        print("\n  --keep：保留当前改动")
    else:
        w32.apply_geometry(target.hwnd, orig[0], orig[1],
                           orig[2] - orig[0], orig[3] - orig[1], monitors)
        time.sleep(0.15)
        back = w32.get_window_rect(target.hwnd)
        check("已恢复到原始几何", all(abs(a - b) <= 2 for a, b in zip(back, orig)),
              f"{back}")


# --------------------------------------------------------------------------
# T7 预设存储往返
# --------------------------------------------------------------------------

def test_store_roundtrip(refs, monitors) -> None:
    section("T7  预设存储往返（含原子写入与导入导出）")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        st = store.PresetStore(Path(td))
        check("空仓库可加载", st.load() == [])

        p = store.Preset(name="测试布局", windows=refs[:3],
                         monitor_snapshot=store.snapshot_monitors())
        st.add(p)
        check("预设已保存", st.path.exists(), str(st.path.name))

        st2 = store.PresetStore(Path(td))
        loaded = st2.load()
        check("重新加载成功", len(loaded) == 1, f"{len(loaded)} 条")
        if loaded:
            r = loaded[0]
            check("名称保持", r.name == "测试布局")
            check("窗口数保持", len(r.windows) == 3,
                  f"{len(r.windows)} 个")
            w0, w1 = refs[0], r.windows[0]
            check("指纹关键字段保持",
                  w0.exe_name == w1.exe_name and w0.cls == w1.cls
                  and w0.title == w1.title,
                  f"{w1.exe_name} / {w1.cls}")
            check("几何保持", (w0.x, w0.y, w0.w, w0.h) == (w1.x, w1.y, w1.w, w1.h),
                  f"({w1.x},{w1.y}) {w1.w}x{w1.h}")
            check("显示器归属保持", w0.monitor_name == w1.monitor_name,
                  w1.monitor_name)
            check("归一化字段保持",
                  abs(w0.rel_in_work[0] - w1.rel_in_work[0]) < 1e-9
                  and abs(w0.rel_size[0] - w1.rel_size[0]) < 1e-9,
                  f"rel_pos={w1.rel_in_work} rel_size={w1.rel_size}")

        # 导出/导入
        exp = Path(td) / "export.json"
        n = st.export_to(exp)
        check("导出成功", exp.exists() and n == 1, f"{n} 条")

        st3 = store.PresetStore(Path(td) / "sub")
        cnt, warns = st3.import_from(exp)
        check("导入成功", cnt == 1, f"{cnt} 条, 警告={warns}")

        # 同名冲突
        cnt2, warns2 = st3.import_from(exp)
        check("同名导入自动改名不覆盖", cnt2 == 1 and st3.presets[-1].name != "测试布局",
              f"改名为 {st3.presets[-1].name!r}")

        # 损坏文件
        bad = Path(td) / "broken.json"
        bad.write_text("{ this is not json", encoding="utf-8")
        st4 = store.PresetStore(Path(td))
        st4.path = bad
        st4.load()
        check("损坏文件不崩溃且有提示", bool(st4.load_error), st4.load_error[:50])

        # 显示器变化检测
        check("显示器配置未变时判定为未变",
              not store.monitors_changed(store.snapshot_monitors()))
        fake = [{"name": "\\\\.\\GONE", "index": 0, "rect": [0, 0, 800, 600],
                 "work": [0, 0, 800, 600], "dpi": 96, "primary": True}]
        check("显示器配置变化能被检出", store.monitors_changed(fake))


# --------------------------------------------------------------------------
# T8 完整预设流程（捕获 → 存 → 改 → 还原 → 校验）
# --------------------------------------------------------------------------

def test_full_flow(monitors, live, keep: bool) -> None:
    section("T8  端到端：捕获布局 → 保存 → 打乱 → 还原 → 校验")

    # 只对「确认响应几何修改」的窗口做端到端测试，否则打乱那一步就没意义
    chosen = []
    for w in live:
        if len(chosen) >= 3:
            break
        if w.cls in engine.UNCONTROLLABLE_HINTS or w.minimized:
            continue
        if w.exe_name.lower() in ("explorer.exe",):
            continue
        picked = pick_responsive_window([w], monitors)
        if picked is not None and picked.hwnd == w.hwnd:
            chosen.append(w)

    if not chosen:
        note("没有确认可控的窗口，跳过端到端测试")
        return

    targets = chosen[:2]
    print(f"  使用 {len(targets)} 个窗口做端到端测试")

    # 1. 捕获
    refs = [w.to_ref(monitors) for w in targets]
    for r in refs:
        print(f"    捕获 {r.exe_name:<22} ({r.x},{r.y}) {r.w}x{r.h} "
              f"@ {r.monitor_name}")
    original_rects = {w.hwnd: w.rect for w in targets}

    preset = store.Preset(name="端到端测试", windows=refs,
                          monitor_snapshot=store.snapshot_monitors())

    # 2. 打乱：把它们缩小并挪到别处
    print()
    print("  打乱布局…")
    for w in targets:
        w32.apply_geometry(w.hwnd, w.rect[0] + 40, w.rect[1] + 40,
                           max(300, w.width - 200), max(200, w.height - 200),
                           monitors)
    time.sleep(0.3)

    scrambled = {w.hwnd: w32.get_window_rect(w.hwnd) for w in targets}
    moved = sum(1 for h in scrambled if scrambled[h] != original_rects[h])
    check("布局确实被打乱了", moved > 0, f"{moved}/{len(targets)} 个窗口位置改变")

    # 3. 还原
    print()
    print("  还原预设…")
    report = engine.apply_preset(preset, activate=False, monitors=monitors)
    print(f"  {report.summary()}   耗时 {report.elapsed * 1000:.0f}ms")
    for o in report.outcomes:
        icon = {"ok": PASS, "constrained": WARN, "acceptable": WARN,
                "not_found": FAIL, "refused": FAIL, "error": FAIL}[o.kind.value]
        print(f"    {icon} {o.ref.exe_name:<22} score={o.match_score:<4}"
              f"{o.status_text:<14}{o.message}")

    check("至少还原了一个窗口", report.ok_count + report.acceptable_count > 0,
          f"{report.ok_count} 精确 + {report.acceptable_count} 有偏差")

    # 4. 回读校验
    print()
    restored = 0
    for o in report.outcomes:
        if not o.hwnd:
            continue
        cur = w32.get_window_rect(o.hwnd)
        exp = original_rects.get(o.hwnd)
        if exp and all(abs(a - b) <= 2 for a, b in zip(cur, exp)):
            restored += 1
            print(f"    {PASS} {o.ref.exe_name:<22} 已回到原始几何 {cur}")
        else:
            print(f"    {WARN} {o.ref.exe_name:<22} 期望 {exp} 实际 {cur}")

    check("窗口确实回到了捕获时的位置",
          restored > 0 and len(report.failed) == 0,
          f"{restored}/{len(targets)} 完全复位，失败 {len(report.failed)} 个")

    # 5. 预演模式不应改动窗口
    print()
    before = {o.hwnd: w32.get_window_rect(o.hwnd) for o in report.outcomes if o.hwnd}
    dry = engine.apply_preset(preset, activate=False, monitors=monitors, dry_run=True)
    after = {h: w32.get_window_rect(h) for h in before}
    check("dry_run 不改动任何窗口", before == after,
          f"全部 {len(before)} 个窗口未变" if before == after else "有窗口被改动")
    check("dry_run 仍返回完整计划", len(dry.outcomes) == len(refs),
          f"{len(dry.outcomes)} 条")


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------

def summarize() -> int:
    section("结果汇总")
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"  通过 {passed}/{total}")
    if passed < total:
        print()
        print("  失败项：")
        for name, ok, detail in _results:
            if not ok:
                print(f"    {FAIL} {name}  —  {detail}")
    print()
    if passed == total:
        print("  ✅ 核心引擎全部通过，可以开始做 UI 了。")
    else:
        print("  ❌ 有失败项，先修引擎再谈 UI。")
    return 0 if passed == total else 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="WindowKeeper 核心引擎测试")
    ap.add_argument("--keep", action="store_true", help="测试后保留窗口改动")
    args = ap.parse_args()

    # ★ 必须在任何窗口操作之前
    w32.enable_per_monitor_v2()

    print("=" * 88)
    print("WindowKeeper 核心引擎测试")
    print("=" * 88)

    monitors = w32.enum_monitors()
    test_dpi(monitors)
    live = test_enumeration(monitors)
    test_title_cleaning()

    if not live:
        print("\n没有可用窗口，无法继续。")
        return summarize()

    refs = test_capture_match_roundtrip(monitors, live)
    test_geometry_resolution(refs, monitors)
    test_actual_restore(monitors, live, args.keep)
    test_store_roundtrip(refs, monitors)
    test_full_flow(monitors, live, args.keep)

    return summarize()


if __name__ == "__main__":
    raise SystemExit(main())
