"""Phase 2 端到端演示：一句题材 → 拍摄版剧本 → 三大资产回流。

    python scripts/run_phase2_demo.py [--theme "县城女护士被高利贷追债"] [--out 目录]

演示内容（真实执行）：
  1. ScriptBrain：一句题材跑完 7 个 Agent + 3 道闸门，产出拍摄版
  2. 产物落进本剧 Bundle 的 01_script/ 与 11_review/
  3. Gate 3 通过 → 立刻提交 → 并行信号；B 剧当场开工
  4. scene_export 直接喂给三大资产模型，全部回流
  5. 剧本自带的道具状态时间线驱动 PropDNA 的跨场景连续性
  6. 剧本闸门没过的情况（水下场景当前做不了）→ 挂人工，但不阻塞别的剧
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator import PipelineOrchestrator  # noqa: E402
from scriptbrain import ScriptBrainService, ScriptBrief  # noqa: E402

DEFAULT_THEME = "县城女护士被高利贷追债，最后逆袭翻身"


def h(title: str) -> None:
    print(f"\n{'=' * 68}\n {title}\n{'=' * 68}")


def jd(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=DEFAULT_THEME)
    ap.add_argument("--out", default=None)
    ap.add_argument("--episodes", type=int, default=1)
    ap.add_argument("--scenes", type=int, default=3)
    args = ap.parse_args()

    root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="cinemadna_p2_"))
    root.mkdir(parents=True, exist_ok=True)

    # ---- 1. ScriptBrain 单独跑一遍 ---------------------------------------
    h("1. ScriptBrain：一句题材进")
    print(f"输入题材：{args.theme}")
    brief = ScriptBrief(
        theme=args.theme, episode_count=args.episodes, scenes_per_episode=args.scenes
    )
    result = ScriptBrainService().run(brief)
    print("\n概要:", jd(result.summary()))
    print("\n三道闸门:")
    for g in result.gates:
        print(f"  {g['gate']:22} passed={g['passed']}  "
              f"human_overridable={g['human_overridable']}  issues={g['issues']}")

    h("2. 拍摄版剧本（第一场）")
    print(jd(result.shooting_script["episodes"][0]["scenes"][0]))

    h("3. scene_export —— 资产大脑直接消费的视图")
    print("scenes:", [s["scene_id"] for s in result.scene_export["scenes"]])
    print("characters:", [c["character_id"] for c in result.scene_export["characters"]])
    print("props:", jd(result.scene_export["props"]))

    # ---- 4. 接进工厂 -----------------------------------------------------
    h("4. 接进 Orchestrator：剧本提交 → 并行信号 → 资产回流")
    orc = PipelineOrchestrator(bundle_root=root, bundle_date="20260723",
                               max_concurrent_scripts=1)
    a = orc.create_story(title="A")
    res_a = orc.run_scriptbrain(a.story_id, brief)
    print("A 剧本阶段:", res_a["state"])
    print("并行信号:", jd(res_a["parallel_signal"]))

    b = orc.create_story(title="B")
    res_b = orc.run_scriptbrain(b.story_id, "婆媳矛盾与养老困局")
    print(f"\nB 立刻开工并完成剧本: {res_b['state']} / "
          f"{res_b['summary']['genre_label']} / {res_b['summary']['scenes']} 场")

    disp = orc.dispatch_assets(a.story_id)
    print(f"\nA 资产分发: {disp['state']}  {disp['summary']['by_outcome']}")
    for kind, rows in disp["results"].items():
        for r in rows:
            print(f"  [{kind:8}] {r['outcome']:12} {r['asset_ids']}")

    h("5. 道具跨场景连续性（由剧本时间线驱动）")
    for pid, rec in orc.store.prop_library.items():
        print(f"{pid}: continuity_supported={rec['continuity_supported']}  "
              f"states={list(rec['asset_ids_by_state'])}")

    # ---- 6. 剧本闸门没过的情况 -------------------------------------------
    h("6. 剧本闸门没过：水下场景当前生成能力做不了")
    c = orc.create_story(title="C")
    res_c = orc.run_scriptbrain(
        c.story_id, ScriptBrief(theme="悬疑追凶：河边失踪案", scenes_per_episode=4)
    )
    print("C 状态:", res_c["state"], " 并行信号:", res_c["parallel_signal"])
    print("Gate3 问题:", res_c["summary"]["gate3_issues"])
    print("待人工队列:", [(x["story_id"], x["reason"]) for x in orc.pending_holds()])
    print("此时仍可推进的故事:",
          [f"{s['story_id']}:{s['state']}" for s in orc.next_actionable()])

    approved = orc.approve_script_gate(
        c.story_id, {"approved": True, "decided_by": "human_lin"}
    )
    print("\n人工放行后:", approved["state"],
          "并行信号:", approved["parallel_signal"]["new_story_allowed"])
    print("（对照：IdentityDNA 的单一真人克隆红线，人工放行会直接抛错）")

    # ---- 7. 落盘 ---------------------------------------------------------
    h("7. 本剧 Bundle 内的剧本产物")
    root_a = orc.bundle_for(a.story_id).root
    for p in sorted(root_a.rglob("*.json")):
        rel = p.relative_to(root_a).as_posix()
        if rel.startswith(("01_script", "11_review")):
            print(f"    {rel}")

    print(f"\n完整产物目录: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
