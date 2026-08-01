"""Phase J 端到端样片：剧本 → 资产 → 声音 → 成片 → 商业闸（PASS_FULL / 精准打回）。

    python scripts/phase_j_endtoend.py

演示"可连续自动生产的工厂闭环"：run_until_quiescent 一把把故事驱动到商业闸，
全程**不依赖人工点继续**；失败一律走自动修复映射；最终落在 3 个人工点之一。

用 placeholder 后端出真实 MP4，让**多模态视觉判断真跑**（会把占位静帧判为假感）；
唇形状态**诚实可见**（未接真实模型 → lip_synced=False）。
另附：对真实运动视频 vs 静帧的视觉判断对比，证明"至少一类真实可跑结果"。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio.mixer import AudioMixer  # noqa: E402
from final_review.vision_judge import VisionJudge  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.backend import ffmpeg_available  # noqa: E402
from render.registry import BackendRegistry  # noqa: E402

THEME = "县城女护士深夜被高利贷追债，最后逆袭翻身"


def h(t): print(f"\n{'=' * 66}\n {t}\n{'=' * 66}")


def main() -> int:
    h("0. 前置")
    if not ffmpeg_available():
        print("✗ 无 ffmpeg，无法出真实 MP4 / 跑视觉判断"); return 1
    from render.registry import placeholder_registry  # 项目自带占位后端
    out = Path(__file__).resolve().parents[1] / "workspace_phase_j"
    orc = PipelineOrchestrator(bundle_root=out, bundle_date="20260724",
                               audio_mixer=AudioMixer())
    orc.render_registry = placeholder_registry()
    print("✓ placeholder 渲染（真实 MP4）+ ffmpeg 混流 + 视觉判断就绪")

    h("1. 端到端自动驱动：剧本 → 资产 → 声音 → 成片 → 商业闸（run_until_quiescent）")
    s = orc.create_story(title="端到端样片")
    orc.run_scriptbrain(s.story_id, THEME)
    orc.run_until_quiescent(export_media=True)

    st = orc.get_story(s.story_id)
    fr = orc.final_review_for(s.story_id)
    events = [e["event"] for e in st["events"]]
    repairs = [e for e in st["events"] if e["event"] == "AUTO_REPAIR_ENTER"]
    print(f"  终态：stage={st['stage']} status={st['status']}")
    print(f"  自动修复次数：{len(repairs)}（全自动，无人工点继续）")
    print(f"  落点：{st.get('hold', {}).get('reason') if st['status']=='WAITING_HUMAN' else st['stage']}")

    h("2. 成片总审：结构 vs 商业可发布")
    if fr:
        print(f"  裁决：{fr.verdict}  商业可发布={fr.commercial_ready}  结构通过={fr.structural_passed}")
        print("  商业清单：")
        for c in fr.checklist:
            print(f"    {'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}")
        print(f"  唇形状态（诚实）：{json.dumps(orc._lip_sync.get(s.story_id, {}), ensure_ascii=False)}")
        vj = None
        ctx = orc._final_review_ctx(s.story_id)
        vj = ctx.get("vision_judge")
        if vj:
            print(f"  视觉判断（真跑 ffmpeg 像素层）：fake={vj['fake']} "
                  f"motion={vj['motion']} luma_spread={vj['luma_spread']} 深判={vj['deep_judged']}")
            print(f"    理由：{vj.get('reason')}")

    h("3. 视觉判断（至少一类真实可跑结果）：真实运动 vs 静帧对比")
    sp = out / "_vjtest"; sp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "testsrc=s=640x360:d=3:r=24", "-pix_fmt", "yuv420p",
                    str(sp / "moving.mp4")], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "color=c=0x203040:s=640x360:d=3:r=24", "-pix_fmt", "yuv420p",
                    str(sp / "static.mp4")], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    vj = VisionJudge()
    for name in ("moving", "static"):
        r = vj.judge(sp / f"{name}.mp4")
        verdict = "假感/崩坏" if r.fake else "通过（像真拍）"
        print(f"  {name:<7} → {verdict}  motion={r.motion} spread={r.luma_spread}  {r.reasons}")

    h("完成")
    print(f"Bundle：{orc.bundle_for(s.story_id).root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
