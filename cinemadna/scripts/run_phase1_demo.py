"""Phase 1 端到端演示：一条命令跑完整个工厂骨架。

    python scripts/run_phase1_demo.py [--out 目录]

演示内容（全部为真实执行，不是打印稿）：
  1. 建两部剧 + 各自的 Active Production Bundle
  2. 并行信号：A 的剧本一提交，B 立刻可以开工
  3. A 的三大资产闭环：查库 → 缺失建单 → mock 生成 → Gate → 回流
  4. B 直接复用 A 沉淀的资产（零新工单）
  5. IdentityDNA 红线：单一真人克隆被拦 → 换合规融合源重生成 → 通过
  6. SceneDNA 红线：要求自然场景却退化成纯 AI 生图 → 被拦
  7. 工厂看板 factory_status + Bundle 落盘结构
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cinemadna.asset_brain.identity_dna import fusion_mock  # noqa: E402
from orchestrator import ParallelCapacityError, PipelineOrchestrator  # noqa: E402

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "mocks" / "sample_shooting_script.json"


def h(title: str) -> None:
    print(f"\n{'=' * 66}\n {title}\n{'=' * 66}")


def jd(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="Bundle 根目录（默认建临时目录）")
    args = ap.parse_args()
    root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="cinemadna_"))
    root.mkdir(parents=True, exist_ok=True)

    script = json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))
    orc = PipelineOrchestrator(
        bundle_root=root, bundle_date="20260723", max_concurrent_scripts=1
    )

    # ---- 1. 建故事 -------------------------------------------------------
    h("1. 创建两部剧（各自独立 Bundle）")
    a = orc.create_story(title="深夜出租屋")
    b = orc.create_story(title="海边渔村", priority="high")
    print(f"A = {a.story_id} / {a.bundle_id}")
    print(f"B = {b.story_id} / {b.bundle_id}")

    # ---- 2. 并行信号 -----------------------------------------------------
    h("2. 并行信号：剧本提交后立刻可开下一部")
    orc.start_script(a.story_id)
    try:
        orc.start_script(b.story_id)
    except ParallelCapacityError as e:
        print(f"[B 被挡住] {e}")
    signal = orc.submit_script(a.story_id, script_ref="shooting_script_A.json")
    print(jd(signal))
    orc.start_script(b.story_id)
    print(f"B 已开工；此时 A 仅到 {orc.get_story(a.story_id)['stage']}（远未完成）")

    # ---- 3. A 的资产闭环 --------------------------------------------------
    h("3. A 的三大资产闭环：查库 → 建单 → mock 生成 → Gate → 回流")
    res = orc.dispatch_assets(a.story_id, script)
    print(f"结局: {res['state']}  统计: {res['summary']['by_outcome']}")
    for kind, rows in res["results"].items():
        for r in rows:
            print(f"  [{kind:8}] {r['workorder_id']} -> {r['outcome']}"
                  f"  资产={r['asset_ids']}")
    print("\n三大库沉淀:", jd(orc.store.stats()))

    # ---- 4. B 复用 --------------------------------------------------------
    h("4. B 直接复用 A 沉淀的资产（回流的价值）")
    orc.submit_script(b.story_id, script_ref="shooting_script_B.json")
    res_b = orc.dispatch_assets(b.story_id, script)
    print(f"结局: {res_b['state']}  统计: {res_b['summary']['by_outcome']}")
    print(f"工单总数仍为 {len(orc.store.workorders)}（B 一张新工单都没建）")

    # ---- 5. IdentityDNA 红线 ---------------------------------------------
    h("5. IdentityDNA 红线：单一真人克隆")
    c = orc.create_story(title="克隆脸测试")
    orc.start_script(c.story_id)
    orc.submit_script(c.story_id, script_ref="C.json")
    clone_script = {
        "scenes": [],
        "characters": [
            {
                "character_id": "char_clone",
                "name": "克隆脸",
                "generation_plan_override": {
                    "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
                },
            }
        ],
        "props": [],
    }
    res_c = orc.dispatch_assets(c.story_id, clone_script)
    ident = res_c["results"]["identity"][0]
    print(f"结局: {res_c['state']}  outcome={ident['outcome']}")
    print(f"hard_blocks = {ident['hard_blocks']}")
    print(f"分数 = {jd(ident['gate_scores'])}")
    print(f"注意 overall={ident['gate_scores']['overall']} 高于及格线 0.70，"
          f"仍被拦下 —— 硬拦截不参与打分")
    print(f"Character Registry 中是否有 char_clone: "
          f"{'char_clone' in orc.store.character_registry}")

    print("\n-- 不改参数直接重试 --")
    print(f"结局: {orc.retry_assets(c.story_id)['state']}（红线不因重试而松动）")
    print("\n-- 换成合规的多源融合后重试 --")
    fixed = orc.retry_assets(c.story_id, plan_override={"fusion_sources": []})
    print(f"结局: {fixed['state']}；Registry 已收录: "
          f"{'char_clone' in orc.store.character_registry}")

    # ---- 6. SceneDNA 红线 -------------------------------------------------
    h("6. SceneDNA 红线：要求自然场景却退化成纯 AI 生图")
    d = orc.create_story(title="纯AI退化测试")
    orc.start_script(d.story_id)
    orc.submit_script(d.story_id, script_ref="D.json")
    ai_script = {
        "scenes": [
            {
                "scene_id": "EP009_SC001",
                "location_description": "废弃工厂",
                "time_of_day": "凌晨",
                "mood": "阴冷",
                "required_atoms": ["厂房结构", "冷光"],
                "generation_plan_override": {"source_kind": "pure_ai_generated"},
            }
        ],
        "characters": [],
        "props": [],
    }
    res_d = orc.dispatch_assets(d.story_id, ai_script)
    scene_r = res_d["results"]["scene"][0]
    print(f"结局: {res_d['state']}  outcome={scene_r['outcome']}")
    print(f"issues = {jd(scene_r['issues'])}")
    fixed_d = orc.retry_assets(
        d.story_id, plan_override={"source_kind": "natural_atom_recompose"}
    )
    print(f"改回自然原子重组后重试: {fixed_d['state']}")

    # ---- 7. 看板与落盘 ----------------------------------------------------
    h("7. 工厂看板 factory_status")
    st = orc.factory_status()
    print(jd({k: st[k] for k in
              ("stories_total", "by_stage", "by_status", "script_slots", "asset_brain")}))
    print("\n可推进故事:",
          [f"{s['story_id']}:{s['state']}" for s in orc.next_actionable()])

    h("8. Active Production Bundle 落盘")
    for bundle_dir in sorted(root.iterdir()):
        files = sorted(p.relative_to(bundle_dir).as_posix()
                       for p in bundle_dir.rglob("*.json"))
        print(f"\n{bundle_dir.name}  ({len(files)} 个 JSON)")
        for f in files[:12]:
            print(f"    {f}")
        if len(files) > 12:
            print(f"    ... 其余 {len(files) - 12} 个")

    print(f"\n完整产物目录: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
