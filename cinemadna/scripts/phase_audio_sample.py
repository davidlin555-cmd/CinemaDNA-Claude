"""AudioDNA 真实样例：女声/男声真实 TTS + 口型实测 + 字幕 + 音量关系。

    python scripts/phase_audio_sample.py --budget 1 --yes
    python scripts/phase_audio_sample.py               # 未开预算 → 只验结构，不出声

预算闸默认关（=不出声）；--budget N 才真实调 ElevenLabs，--yes 才自动确认。
一段催债对话：女主（女声）+ 催债人（男声），各出真实音频，出字幕，跑声音专业审核。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from asset_brain.common.bundle import Bundle  # noqa: E402
from asset_brain.common.quad import Quad  # noqa: E402
from audio.service import AudioDNAService  # noqa: E402
from audio.tts import ElevenLabsTTSBackend  # noqa: E402
from director.shot_contract import (  # noqa: E402
    MOUTH_REACTION,
    MOUTH_SPEAKING_LIPSYNC,
    SHOT_CLOSEUP,
    SHOT_MEDIUM,
    SHOT_REACTION,
    STATUS_DRAFT,
    new_shot_contract,
    sign_contract,
)
from render.budget import BudgetGate, confirm_interactive, confirm_yes  # noqa: E402

CHARS = {"char_linwan": {"character_id": "char_linwan", "name": "林晚", "gender": "female"},
         "char_debt": {"character_id": "char_debt", "name": "催债人", "gender": "male"}}

# 三个镜头：女主特写(说话) / 催债人中景(说话) / 女主反应(无声闭嘴)
SHOTS_ZH = [
    ("SH1", SHOT_CLOSEUP, MOUTH_SPEAKING_LIPSYNC,
     [{"character_id": "char_linwan", "line": "再给我两天，工资一发我立刻还。"}]),
    ("SH2", SHOT_MEDIUM, MOUTH_SPEAKING_LIPSYNC,
     [{"character_id": "char_debt", "line": "别拿工资糊弄我，今晚十二点前必须到账。"}]),
    ("SH3", SHOT_REACTION, MOUTH_REACTION, []),   # 无声反应镜：必须闭嘴、无音频
]
# 英文版（本机只装英文 SAPI 音色时用，出可听样例）
SHOTS_EN = [
    ("SH1", SHOT_CLOSEUP, MOUTH_SPEAKING_LIPSYNC,
     [{"character_id": "char_linwan", "line": "Give me two more days, I'll pay you back the moment my salary comes."}]),
    ("SH2", SHOT_MEDIUM, MOUTH_SPEAKING_LIPSYNC,
     [{"character_id": "char_debt", "line": "Don't fool me with your salary. The money must arrive before midnight."}]),
    ("SH3", SHOT_REACTION, MOUTH_REACTION, []),
]


def h(t): print(f"\n{'=' * 66}\n {t}\n{'=' * 66}")


def _contract(sid, shot_type, mouth, dialogue, order):
    c = new_shot_contract(
        shot_id=sid, scene_id="EP001_SC001", episode_id="EP001", order=order,
        shot_type=shot_type,
        quad=Quad(story_id="drama_audio", bundle_id="bundle_audio", task_id="t1", asset_hash=None),
        camera={"shot_size": "特写", "movement": "固定", "angle": "平视", "intent": "对峙"},
        duration_sec=4.0, emotion={"beat": "CONFLICT", "intensity": 0.8, "mood": "压抑"},
        scene_asset={"asset_id": "sc", "asset_hash": "sha256:" + "a"*64,
                     "gate_passed": True, "tags": []},
        character_assets=[{"character_id": d["character_id"], "master_pack_id": "cmp",
                           "asset_hash": "sha256:" + "b"*64, "gate_passed": True}
                          for d in dialogue] or [{"character_id": "char_linwan",
                           "master_pack_id": "cmp", "asset_hash": "sha256:" + "b"*64,
                           "gate_passed": True}],
        prop_assets=[], dialogue=dialogue, mouth_policy=mouth)
    c["performance"] = {"acting_beats": [
        {"type": "LINE", "character_id": d["character_id"], "cue": "",
         "micro_expression": {"primary": "紧张"}} for d in dialogue]
        or [{"type": "REACTION", "character_id": "char_linwan", "cue": "",
             "micro_expression": {"primary": "绝望"}}]}
    c["voice_contract"] = {"has_dialogue": bool(dialogue), "bindings": [
        {"speaker": d["character_id"],
         "voice_gender": CHARS[d["character_id"]]["gender"], "line": d["line"]}
        for d in dialogue]}
    if c["status"] != STATUS_DRAFT:
        c["status"] = STATUS_DRAFT; c["contract_hash"] = None
    sign_contract(c)
    return c


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=0.0, help="声音预算(units)")
    ap.add_argument("--local", action="store_true",
                    help="用本地 Windows SAPI（免费、无需 Key）出声")
    ap.add_argument("--edge", action="store_true",
                    help="用 Edge TTS（免费中文神经音色，需联网）出声")
    ap.add_argument("--ambience", default="night", help="环境音预设(room/rain/street/night)")
    ap.add_argument("--en", action="store_true",
                    help="用英文台词（本机只装英文 SAPI 音色时出可听样例）")
    ap.add_argument("--mux", action="store_true",
                    help="真实音画合成：ffmpeg 把对白/BGM/环境音/字幕合进 MP4")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "workspace_audio"))
    args = ap.parse_args()

    h("0. 前置")
    confirm = confirm_yes if args.yes else confirm_interactive
    budget = BudgetGate(max_units=args.budget or 0.0, confirm=confirm,
                        allowed_durations_sec=(5,))
    if args.edge:
        from audio.tts_edge import EdgeTTSBackend
        tts = EdgeTTSBackend()
        real = True
        print("✓ 真实出声（Edge TTS 免费中文神经音色）")
    elif args.local:
        from audio.tts_local import SapiTTSBackend
        tts = SapiTTSBackend()
        real = True
        print("✓ 真实出声（本地 Windows SAPI，免费）")
    elif args.budget > 0:
        if not config.has("ELEVENLABS_API_KEY"):
            print("✗ 缺 ELEVENLABS_API_KEY"); return 1
        tts = ElevenLabsTTSBackend(budget=budget)
        real = True
        print("✓ 真实出声（ElevenLabs）")
    else:
        tts, real = None, False
        print("⚠ 未开预算 → 只验结构，不出声（--budget N 用 ElevenLabs / --local 用本地）")

    bundle = Bundle(Path(args.out), "bundle_audio").ensure()
    svc = AudioDNAService(bundle, tts_backend=tts)
    chars = CHARS
    shots = SHOTS_EN if args.en else SHOTS_ZH
    contracts = [_contract(*s, i + 1) for i, s in enumerate(shots)]

    h("1. 声音性别真实绑定 → 具体音色")
    svc.repair_contracts(contracts, characters_by_id=chars)
    for c in contracts:
        for b in c["voice_contract"]["bindings"]:
            print(f"  {c['shot_id']} {b['speaker']}({b['voice_gender']}) → "
                  f"{b.get('voice_name')} [{b.get('voice_id')}]")

    if real:
        h("2. 真实 TTS 出声（每句台词一条音频）")
        done = svc.synthesize(contracts, characters_by_id=chars)
        for c in contracts:
            for b in c["voice_contract"]["bindings"]:
                if b.get("audio_file"):
                    p = bundle.path_for(b["audio_file"])
                    print(f"  {c['shot_id']} 「{b['line']}」→ {b['audio_file']} "
                          f"({p.stat().st_size:,}B, {b['duration_sec']}s)")
                elif b.get("audio_error"):
                    print(f"  {c['shot_id']} ✗ {b['audio_error']}")

    h("3. 字幕（WebVTT，真实时长驱动）")
    cues = svc.write_subtitles(contracts, story_id="drama_audio")
    for cue in cues:
        print(f"  [{cue['start']:.2f}-{cue['end']:.2f}] {cue['speaker']}: {cue['text']}")
    print(f"  字幕文件：10_outputs/drama_audio_dialogue.vtt")

    h("4. 音量关系（对白/BGM）")
    for c in contracts:
        if c.get("audio_mix"):
            print(f"  {c['shot_id']}: {json.dumps(c['audio_mix'], ensure_ascii=False)}")

    h("5. 声音专业审核（口型实测：有声才张嘴 / 无声必闭嘴）")
    r = svc.review(contracts, story_id="drama_audio", characters_by_id=chars,
                   require_real_audio=real)
    print(f"  裁决：{r.verdict}  音频 {r.audio_files} 条 / 台词 {r.dialogue_lines} 句 / "
          f"字幕 {r.subtitle_cues} cue")
    if r.issues:
        for i in r.issues:
            print(f"  · [{i['code']}] {i['message']}")
    else:
        print("  ✓ 全过：性别正确、有声镜头有音频、无声反应镜无音频、字幕齐全")

    if args.mux:
        h("6. 真实音画合成（ffmpeg：对白 + BGM ducking + 环境音 + 烧录字幕 → MP4）")
        import subprocess
        from audio.mixer import AudioMixer
        mx = AudioMixer()
        if not mx.available():
            print("✗ 无 ffmpeg，跳过音画合成")
        else:
            dur = max((cue["end"] for cue in cues), default=8.0) + 0.8
            video_rel = "10_outputs/rough_cut.mp4"
            vpath = bundle.path_for(video_rel)
            vpath.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi",
                 "-i", f"color=c=0x101820:s=768x1024:d={dur:.2f}",
                 "-vf", "drawtext=text='CinemaDNA':fontcolor=white:fontsize=44:"
                        "x=(w-tw)/2:y=110",
                 "-r", "24", "-pix_fmt", "yuv420p", str(vpath)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            res = svc.compose_final(contracts, story_id="drama_audio",
                                    video_relpath=video_rel, mixer=mx,
                                    ambience=args.ambience)
            if res.get("ok"):
                print(f"  ✓ 成片：{res['relpath']}  ({res['bytes']:,}B, "
                      f"{res['duration_sec']}s, 对白 {res['dialogue_clips']} 段, "
                      f"和弦BGM+{args.ambience}环境音+loudnorm+烧录字幕)")
            else:
                print(f"  ✗ {res.get('error')}")

    h("完成")
    print(f"声音预算：{json.dumps(budget.summary(), ensure_ascii=False)}")
    print(f"Bundle：{bundle.root}")
    return 0 if (not real or r.passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
