"""0 成本本地重收尾 —— 复用已付费的 11 个 Kling 镜头，修掉冻帧后重出成片。

    python scripts/plan_d_refinish.py

上一轮真片已付费下载 11 镜（含屏内合成），但装配阶段"定格末帧补时长"被商业闸
判成冻帧。改成"整体变速填满"（连续运动、无冻帧）后，**不重新生成、不再花钱**，
只在本地重新装配 + 混流 + 复检。

诚实输出四项验收：叙事连贯 / 信息镜状态 / 声音是否被裁&驱动 / 是否无厘头。
"""

from __future__ import annotations

import glob
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cinemadna.asset_brain.common.bundle import Bundle  # noqa: E402
from audio.mixer import AudioMixer  # noqa: E402
from audio.service import AudioDNAService  # noqa: E402
from final_review.media_probe import MediaProbe  # noqa: E402
from final_review.vision_judge import VisionJudge  # noqa: E402
from render.assembly import assemble_rough_cut, export_cut  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "workspace_plan_d"
SID = "drama_0001"


def h(t): print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def main() -> int:
    bundle = Bundle(OUT, "bundle_20260724_001")

    h("1. 复用已付费镜头（不重生成）")
    contracts = []
    for f in sorted(glob.glob(str(bundle.path_for("06_shots/contracts")) + "/*.json")):
        contracts.append(json.load(open(f, encoding="utf-8")))
    bound = 0
    for c in contracts:
        p = bundle.path_for(f"07_payloads/render/{c['shot_id']}.json")
        if p.is_file():
            c["render_result"] = json.load(open(p, encoding="utf-8"))   # 复用真片
            bound += 1
    print(f"  载入 {len(contracts)} 合约，绑定 {bound} 个已渲镜头")

    h("2. 重新装配（变速填满，无冻帧）")
    cut = assemble_rough_cut(contracts, story_id=SID, bundle_id=bundle.bundle_id,
                             title="全量商业样片")
    export_cut(cut, bundle, video=True)
    print(f"  粗剪：{cut['shot_count']} 镜 / {cut['total_duration_sec']}s "
          f"media_ready={cut['media_ready']}")

    h("3. 重新混流（复用已生成的 EdgeTTS 语音）")
    svc = AudioDNAService(bundle, tts_backend=None)
    svc.write_subtitles(contracts, story_id=SID)
    res = svc.compose_final(contracts, story_id=SID,
                            video_relpath="10_outputs/rough_cut.mp4",
                            mixer=AudioMixer(), bgm=True, ambience="room")
    final = bundle.path_for(f"10_outputs/{SID}_final.mp4")
    print(f"  混流 ok={res.get('ok')} 时长={res.get('duration_sec')}s "
          f"对白轨={res.get('dialogue_clips')} 烧字幕={res.get('burned_subtitles')}")

    h("4. 复检（冻帧/黑屏/视觉判断）")
    mp = MediaProbe().probe(final).to_dict()
    vj = VisionJudge().judge(final).to_dict()
    print(f"  冻帧={mp['freeze_events']} 黑屏={mp['black_events']} 丢帧={mp['dropped_frames']} "
          f"时长={mp['duration']}s")
    print(f"  视觉：fake={vj['fake']} motion={vj['motion']}")

    # 声音是否被裁：逐镜比对 语音时长 vs 镜头时长
    h("5. 声音驱动 & 未被裁切核对")
    cut_ok = True
    for c in contracts:
        prov = c.get("duration_provenance") or {}
        vt = prov.get("voice_total_sec")
        if vt is None:
            continue
        shot = float(c["duration_sec"])
        fit = vt <= shot + 0.01
        cut_ok = cut_ok and fit
        print(f"  {c['shot_id']}: 语音 {vt}s ≤ 镜头 {shot}s → {'✓未裁' if fit else '✗被裁'}"
              f"  ({prov.get('source')})")
    print(f"  结论：语音全部未被裁切 = {cut_ok}")

    # 抽帧供叙事/信息镜判断
    h("6. 抽帧")
    frames = {"open": "1.5", "bill_late": str(round(float(mp['duration']) - 4, 1)),
              "mid": str(round(float(mp['duration']) / 2, 1))}
    for name, ts in frames.items():
        dst = OUT / f"refin_{name}.png"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", ts,
                        "-i", str(final), "-frames:v", "1", str(dst)], check=False)
        print(f"  {dst.name} @ {ts}s")

    report = {"freeze_events": mp["freeze_events"], "black_events": mp["black_events"],
              "vision_fake": vj["fake"], "vision_motion": vj["motion"],
              "duration_sec": mp["duration"], "voice_never_cut": cut_ok,
              "final": str(final), "shot_count": cut["shot_count"]}
    (OUT / "refinish_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    h("完成")
    print(f"  成片：{final}")
    print(f"  报告：{OUT / 'refinish_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
