"""验证 E1 Runway Act-Two：音频驱动(对白音频→角色开口说话)。~$1-2。

nurse 锚脸(character image) + 对白音频(reference audio) → Act-Two → 护士说这句话。
确认: API 调通 + 输出是护士说话视频。云端,可与 Plan A 并行。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from performance_driving.runway import RunwayActOneBackend  # noqa: E402
from render.budget import BudgetGate, confirm_yes  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "workspace_verify_runway"
ANCHOR = (Path("C:/Users/david/AppData/Local/Temp/claude/E--CinemaDNA-Claude"
               "/a56c3783-e42b-4aca-9daa-c9cfa0dbb148/scratchpad/pulid/close.png"))
LINE = "这钱我不还了，你别想再碰我。"


async def _tts(dst: Path) -> None:
    import edge_tts
    c = edge_tts.Communicate(LINE, "zh-CN-XiaoxiaoNeural")
    await c.save(str(dst))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"锚脸: {ANCHOR.name} 存在={ANCHOR.is_file()}")
    mp3 = OUT / "line.mp3"
    asyncio.run(_tts(mp3))
    wav = OUT / "line.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
                    "-ar", "44100", "-ac", "1", str(wav)], timeout=60)
    print(f"对白音频: {wav}  {wav.stat().st_size} bytes")

    # character_performance(Act-Two)是**视频驱动**——用驱动表演视频(tail_clip 有头/脸运动)
    driving = (Path(__file__).resolve().parents[1]
               / "workspace_verify_tail/tail_clip.mp4")
    print(f"驱动视频: {driving.name} 存在={driving.is_file()}")
    gate = BudgetGate(max_units=5.0, confirm=confirm_yes,
                      allowed_durations_sec=(), cost_table={})
    be = RunwayActOneBackend(budget=gate, model="act_two")
    print(f"Runway available={be.available()}  base={be.base}  ver={be.version}")
    dest = OUT / "act_two_out.mp4"
    try:
        be.perform(character_image=ANCHOR, reference=driving, dest=dest,
                   item_id="verify_act_two", reference_type="video",
                   ratio="720:1280", expression_intensity=3)
    except Exception as e:  # noqa: BLE001
        print(f"❌ Runway 调用失败: {e}")
        return 1
    print(f"✅ 成品: {dest}  {dest.stat().st_size} bytes")
    print(f"预算记账: {gate.spent_units} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
