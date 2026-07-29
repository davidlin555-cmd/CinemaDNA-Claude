"""Plan A：1 分钟自动化工厂验证样片 —— 全免费后端跑通商业闸。

    python scripts/plan_a_60s_sample.py

全自动：剧本 → 三大资产 → 分镜/表演 → 声音 → 成片 → 商业闸，
run_until_quiescent 一把驱动，中途不点继续；自动修复允许发生，
最终停在商业闸（PASS_STRUCTURAL / PASS_FULL）或人工终确认点。

后端全免费、不烧预算：
  - 画面：placeholder（真实 MP4，但非模型生成 → 诚实判 PASS_STRUCTURAL）
  - 声音：Edge TTS（免费中文神经音色）
  - 屏内道具：Chrome 模板（免费）
  - 混流/字幕/视觉判断/黑屏探测：ffmpeg（免费）

诚实输出：PASS_STRUCTURAL/PASS_FULL、lip_synced、视觉判断、自动修复了哪些。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.prop_dna import screen_templates  # noqa: F401,E402  (确保可用)
from assets_real.screen_render import ChromeScreenshotBackend  # noqa: E402
from audio.mixer import AudioMixer  # noqa: E402
from audio.tts_edge import EdgeTTSBackend  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.backend import ffmpeg_available  # noqa: E402
from render.registry import placeholder_registry  # noqa: E402

THEME = "县城女护士深夜被高利贷追债，翻出攥皱的缴费单与催债短信，最后逆袭翻身"


def h(t): print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def main() -> int:
    h("0. 前置（全免费后端）")
    if not ffmpeg_available():
        print("✗ 无 ffmpeg，无法出真实 MP4"); return 1
    out = Path(__file__).resolve().parents[1] / "workspace_plan_a"
    import shutil
    shutil.rmtree(out, ignore_errors=True)

    screen = ChromeScreenshotBackend()
    orc = PipelineOrchestrator(
        bundle_root=out, bundle_date="20260724",
        audio_backend=EdgeTTSBackend(),            # 免费中文配音
        audio_mixer=AudioMixer(),                  # ffmpeg 混流
        screen_backend=screen if screen.available() else None)
    orc.render_registry = placeholder_registry()   # 真实 MP4（占位画面）
    print(f"✓ 画面=placeholder  声音=EdgeTTS  屏内道具="
          f"{'Chrome' if screen.available() else '无'}  混流=ffmpeg")

    h("1. 全自动驱动：剧本 → 资产 → 声音 → 成片 → 商业闸")
    s = orc.create_story(title="1分钟验证样片")
    orc.run_scriptbrain(s.story_id, {"theme": THEME, "episode_count": 1,
                                     "scenes_per_episode": 4})
    orc.run_until_quiescent(export_media=True)

    st = orc.get_story(s.story_id)
    fr = orc.final_review_for(s.story_id)
    b = orc.bundle_for(s.story_id)
    ctx = orc._final_review_ctx(s.story_id)
    vj = ctx.get("vision_judge") or {}
    lip = orc._lip_sync.get(s.story_id, {})
    cut = ctx.get("rough_cut") or {}

    # 自动修复了哪些
    repairs = [{"code": e.get("code"), "target": e.get("target")}
               for e in st["events"] if e["event"] == "AUTO_REPAIR_ENTER"]

    h("2. 诚实报告")
    print(f"  终态：stage={st['stage']}  status={st['status']}  "
          f"落点={(st.get('hold') or {}).get('reason') or st['stage']}")
    print(f"  成片：镜头 {cut.get('shot_count')} 个 · 总时长 {cut.get('total_duration_sec')}s")
    if fr:
        print(f"  商业闸裁决：{fr.verdict}  商业可发布={fr.commercial_ready}  "
              f"结构通过={fr.structural_passed}")
        print("  PASS_FULL 硬清单：")
        for c in fr.checklist:
            print(f"    {'✓' if c['ok'] else '✗'} {c['name']}: {c['detail']}")
    print(f"  lip_synced：{lip.get('lip_synced')}（{lip.get('note','')}）")
    if vj:
        print(f"  视觉判断（ffmpeg 真跑）：fake={vj.get('fake')} motion={vj.get('motion')} "
              f"动态范围={vj.get('luma_spread')} 深判={vj.get('deep_judged')}")
        if vj.get("reasons"):
            print(f"    理由：{'；'.join(vj['reasons'])}")
    print(f"  自动修复：{len(repairs)} 次 → {repairs if repairs else '无（一路直过）'}")

    # 找可播放成片
    playable = None
    for rel in (f"10_outputs/{s.story_id}_final.mp4", "10_outputs/drama_audio_final.mp4",
                "10_outputs/rough_cut.mp4"):
        if b.exists(rel):
            playable = b.path_for(rel); break

    h("3. 达到 PASS_FULL 需要的真实生成（预算预估，待你确认再开）")
    n = cut.get("shot_count") or 0
    print(f"  当前 PASS_STRUCTURAL 卡在：占位画面（非模型生成）→ no_obvious_fake / 黑屏冻帧")
    print(f"  升级到 PASS_FULL 需真实生成 {n} 个镜头（image2video）：")
    print(f"    · IdentityDNA 真实人脸（Together FLUX）：~{max(1,2)} 张 ≈ 0.02 units（约几分钱）")
    print(f"    · 每镜 image2video（Kling 5s，~1 unit/镜）：{n} 镜 ≈ {n} units")
    print(f"    · 合计约 {n+1} units（Kling trial 100 units 内）——确认后我再开预算闸")

    # 落一份完整报告
    report = {
        "verdict": fr.verdict if fr else None,
        "commercial_ready": fr.commercial_ready if fr else None,
        "structural_passed": fr.structural_passed if fr else None,
        "lip_synced": lip.get("lip_synced"),
        "vision_judge": vj,
        "auto_repairs": repairs,
        "checklist": fr.checklist if fr else [],
        "shot_count": cut.get("shot_count"),
        "total_duration_sec": cut.get("total_duration_sec"),
        "final_stage": st["stage"], "final_status": st["status"],
        "hold_reason": (st.get("hold") or {}).get("reason"),
        "playable": str(playable) if playable else None,
    }
    rpath = out / "plan_a_report.json"
    rpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    h("完成")
    print(f"  可播放成片：{playable}")
    print(f"  完整报告：{rpath}")
    print(f"  Bundle：{b.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
