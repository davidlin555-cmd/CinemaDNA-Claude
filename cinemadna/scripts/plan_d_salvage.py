"""0 成本 salvage：用已付费的 12 镜 + 修好的 ③熔断校准，重装配成完整片。

复盘：stage1 真跑成片只剩 4 镜——因为旧 ③熔断把"塑料感(ai_fake)"当坏镜逐个踢了 8 镜。
校准修好后（塑料感不逐镜踢，只踢真崩坏：静帧/变脸/手脸畸变/乱码），用**已付费素材**
本地重装配，应从 4 镜恢复到 ~11 镜。**不重生成、不再花钱**（视觉判用 ffmpeg 层，$0）。

    python scripts/plan_d_salvage.py
"""

from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.common.bundle import Bundle  # noqa: E402
from audio.mixer import AudioMixer  # noqa: E402
from audio.service import AudioDNAService  # noqa: E402
from final_review.media_probe import MediaProbe  # noqa: E402
from final_review.vision_judge import VisionJudge  # noqa: E402
from render.assembly import assemble_rough_cut, export_cut  # noqa: E402
from render.vision_quarantine import VisionQuarantine  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "workspace_plan_d"
SID = "drama_0001"


def h(t): print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def main() -> int:
    bundle = Bundle(OUT, "bundle_20260724_001")

    h("1. 复用已付费镜头（不重生成）")
    contracts = []
    for f in sorted(glob.glob(str(bundle.path_for("06_shots/contracts")) + "/*.json")):
        contracts.append(json.load(open(f, encoding="utf-8")))
    rendered = []
    for c in contracts:
        p = bundle.path_for(f"07_payloads/render/{c['shot_id']}.json")
        if p.is_file():
            c["render_result"] = json.load(open(p, encoding="utf-8"))
            rr = c["render_result"]
            rendered.append({"shot_id": c["shot_id"],
                             "file_relpath": rr.get("file_relpath"),
                             "is_generated_footage": rr.get("is_generated_footage", True)})
    print(f"  载入 {len(contracts)} 合约，绑定 {len(rendered)} 个已渲镜头")

    h("2. ③熔断（校准后）：只踢真崩坏，塑料感保留")
    # ffmpeg 层视觉判（$0）：抓静帧/纯色（含冻帧）；塑料感 ai_fake 已不逐镜踢
    q = VisionQuarantine(judge=VisionJudge()).screen(rendered, bundle=bundle)
    print(f"  通过 {len(q.passed)} 镜 / 隔离 {len(q.quarantined)} 镜")
    for x in q.quarantined:
        print(f"    隔离 {x['shot_id']}: {'; '.join(x['reasons'])}")
    exclude = set(q.quarantined_ids)

    h("3. 重新装配（只拼 passed 镜）")
    cut = assemble_rough_cut(contracts, story_id=SID, bundle_id=bundle.bundle_id,
                             title="全量商业样片", exclude_shot_ids=exclude)
    export_cut(cut, bundle, video=True)
    print(f"  粗剪：{cut['shot_count']} 镜 / {cut['total_duration_sec']}s "
          f"media_ready={cut['media_ready']}")

    h("4. 混流（复用已生成 EdgeTTS 语音 + 烧字幕）")
    svc = AudioDNAService(bundle, tts_backend=None)
    svc.write_subtitles(contracts, story_id=SID)
    res = svc.compose_final(contracts, story_id=SID,
                            video_relpath="10_outputs/rough_cut.mp4",
                            mixer=AudioMixer(), bgm=True, ambience="room")
    final = bundle.path_for(f"10_outputs/{SID}_final.mp4")
    print(f"  混流 ok={res.get('ok')} 时长={res.get('duration_sec')}s "
          f"对白轨={res.get('dialogue_clips')} 烧字幕={res.get('burned_subtitles')}")

    h("5. 复检")
    mp = MediaProbe().probe(final).to_dict()
    vj = VisionJudge().judge(final).to_dict()
    print(f"  冻帧={mp['freeze_events']} 黑屏={mp['black_events']} "
          f"丢帧={mp['dropped_frames']} 时长={mp['duration']}s")
    print(f"  视觉：fake={vj['fake']} motion={vj['motion']}")
    print(f"\n  成片：{final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
