"""叙事层 0 成本验证 —— LLM 作者化 + 叙事连贯硬门（不出视频、不花视频预算）。

    python scripts/narrative_validate.py

只做文本层：LLM 写故事 → 分镜 → NarrativeContinuityQA 硬门。用真实 LLM（几分钱），
mock 资产/渲染（免费）。打印作者化的台词与因果，供人工判"是否像故事"。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from council.commercial_review import CommercialCouncil  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from scriptbrain.llm_author import LLMNarrativeAuthor  # noqa: E402
from scriptbrain.service import ScriptBrainService  # noqa: E402

THEME = "县城女护士深夜被高利贷追债，翻出攥皱的缴费单与催债短信，最后逆袭翻身"
OUT = Path(__file__).resolve().parents[1] / "workspace_narrative"


def h(t): print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def main() -> int:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)

    h("1. LLM 作者化叙事（真实调用，几分钱）")
    author = LLMNarrativeAuthor()
    council = CommercialCouncil()
    print(f"  author 可用：{author.available()}  模型：{author.model}")
    sb = ScriptBrainService(narrative_author=author, commercial_council=council,
                            max_revise_iters=4)

    orc = PipelineOrchestrator(bundle_root=OUT, bundle_date="20260725")  # mock 资产/渲染=免费
    s = orc.create_story(title="叙事验证短样")
    sid = s.story_id
    orc.run_scriptbrain(sid, {"theme": THEME, "episode_count": 1,
                              "scenes_per_episode": 3}, scriptbrain=sb)
    print(f"  narrative_mode：{sb.narrative_mode}")
    if sb.council_result:
        print(f"  剧本层责编闭环终稿：{sb.council_result.verdict} 分{sb.council_result.score}")

    h("2. 作者化的高密度节拍表（人工判密度/连贯/不重复/贴情绪）")
    script = orc._shooting_scripts.get(sid) or {}
    nbeats = 0
    for ep in script.get("episodes") or []:
        for scn in ep.get("scenes") or []:
            nar = scn.get("narrative") or {}
            print(f"\n  【{scn['scene_id']}】mood={scn.get('mood')} "
                  f"因果:{nar.get('cause')} → {nar.get('effect')}")
            for b in scn.get("beats") or []:
                nbeats += 1
                spk = f"[{b.get('character_id')}]「{b.get('line')}」" if b.get("line") else "（无台词）"
                print(f"    {b.get('seconds')}s · {b.get('type'):<12} "
                      f"{b.get('description','')}  {spk}（{b.get('emotion')}）")
    print(f"\n  全片节拍数：{nbeats}")

    h("3. 驱动到分镜 + 叙事连贯硬门")
    # mock 资产/渲染，驱动器会一路跑；关注 narrative QA 结果
    orc.run_until_quiescent()
    nq = orc.narrative_qa_for(sid)
    dq = orc.density_qa_for(sid)
    st = orc.get_story(sid)
    print(f"  终态：stage={st['stage']} status={st['status']}")
    if nq:
        rep = nq.report()
        print(f"  NarrativeContinuityQA：{rep['verdict']}  查了 {rep['checked_shots']} 镜  "
              f"问题 {rep['issue_count']}  分类={rep['by_category']}")
        for i in rep["issues"][:6]:
            print(f"    ✗ [{i['category']}] {i['message']}")
    if dq:
        rep = dq.report()
        m = rep["metrics"]
        print(f"  DensityQA：{rep['verdict']}  {m['shots']}镜/{m['total_sec']}s "
              f"平均{m['avg_shot_sec']}s/镜  节拍类型={m['beat_types_present']}")
        for i in rep["issues"][:6]:
            print(f"    ✗ [{i['category']}] {i['message']}")
    cc = orc.council_for(sid)      # 已是 report dict（剧本层闭环终稿评审）
    if cc:
        print(f"  商业Council(LLM)：{cc['verdict']}  综合分 {cc['score']}  维度={cc['scores']}")
        for nt in cc["notes"][:3]:
            print(f"    · {nt}")

    h("4. 抽查渲染 prompt（动作是否进 prompt = 不再头像动画）")
    import json as _json
    import glob as _glob
    b = orc.bundle_for(sid)
    for f in sorted(_glob.glob(str(b.path_for("07_payloads/render")) + "/*.json"))[:3]:
        p = _json.load(open(f, encoding="utf-8")).get("prompt", "")
        print(f"    {p[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
