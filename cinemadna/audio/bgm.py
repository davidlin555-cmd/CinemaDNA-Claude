"""内置 BGM 曲库 (Phase E5) —— 按情绪合成的配乐垫底，供混流器现成选用。

不再只有一个和弦垫底：按镜头情绪自动选配乐（紧张/悲伤/逆袭/温情/中性），
全部用 ffmpeg 音源+滤镜合成（无需外部素材）。也可 `bgm_path` 接真实曲子替换。

每个曲目返回一段 filter_complex 片段，产出标签 [bgm]（44100 立体声）。
"""

from __future__ import annotations

from typing import Any

MOOD_TENSION = "tension"    # 紧张/对峙
MOOD_SAD = "sad"            # 悲伤/低落
MOOD_TRIUMPH = "triumph"    # 逆袭/高燃
MOOD_WARM = "warm"          # 温情/舒缓
MOOD_NEUTRAL = "neutral"    # 中性铺底

BGM_MOODS = (MOOD_TENSION, MOOD_SAD, MOOD_TRIUMPH, MOOD_WARM, MOOD_NEUTRAL)

#: 剧本节拍 → 配乐情绪
_BEAT_TO_MOOD = {
    "CONFLICT": MOOD_TENSION, "CRISIS": MOOD_TENSION, "TWIST": MOOD_TENSION,
    "SETUP": MOOD_NEUTRAL, "CALM": MOOD_WARM, "REUNION": MOOD_WARM,
    "LOSS": MOOD_SAD, "LOW": MOOD_SAD, "DEFEAT": MOOD_SAD,
    "VICTORY": MOOD_TRIUMPH, "COMEBACK": MOOD_TRIUMPH, "RISE": MOOD_TRIUMPH,
}


def mood_for_emotion(emotion: dict[str, Any] | None) -> str:
    """由镜头情绪（beat + intensity）挑一个配乐情绪。"""
    e = emotion or {}
    beat = str(e.get("beat", "")).upper()
    if beat in _BEAT_TO_MOOD:
        return _BEAT_TO_MOOD[beat]
    intensity = float(e.get("intensity", 0.5) or 0.5)
    return MOOD_TENSION if intensity >= 0.75 else MOOD_NEUTRAL


def _tone(freq: float, dur: float, vol: float, label: str) -> str:
    return (f"sine=frequency={freq}:duration={dur}:sample_rate=44100,"
            f"volume={vol}[{label}]")


def bed_for(mood: str, duration: float) -> list[str]:
    """返回某情绪配乐的 filter_complex 片段列表（末尾产出 [bgm]）。"""
    d = round(float(duration), 2)
    if mood == MOOD_TENSION:
        # 低频脉动 + 小二度不谐 + 快颤音 → 压迫感
        return [
            f"aevalsrc='0.16*sin(110*2*PI*t)*(1+0.4*sin(5*2*PI*t))':d={d}:s=44100[t1]",
            _tone(116.54, d, 0.06, "t2"),   # 与 110 形成不谐
            "[t1][t2]amix=inputs=2:normalize=0,tremolo=f=0.4:d=0.3,"
            "afade=t=in:st=0:d=0.8[bgm]",
        ]
    if mood == MOOD_SAD:
        # A 小三和弦 + 回声空间 + 慢淡入 → 低落
        return [
            _tone(220.0, d, 0.11, "s1"), _tone(261.63, d, 0.09, "s2"),
            _tone(329.63, d, 0.08, "s3"),
            "[s1][s2][s3]amix=inputs=3:normalize=0,aecho=0.8:0.7:60:0.4,"
            "lowpass=f=2000,afade=t=in:st=0:d=1.6[bgm]",
        ]
    if mood == MOOD_TRIUMPH:
        # A 大三和弦 + 明亮高音 + 微上扬颤音 → 高燃
        return [
            _tone(220.0, d, 0.10, "v1"), _tone(277.18, d, 0.10, "v2"),
            _tone(329.63, d, 0.10, "v3"), _tone(440.0, d, 0.07, "v4"),
            "[v1][v2][v3][v4]amix=inputs=4:normalize=0,tremolo=f=0.2:d=0.2,"
            "afade=t=in:st=0:d=0.6[bgm]",
        ]
    if mood == MOOD_WARM:
        # C 大三和弦 柔和低量 → 温情
        return [
            _tone(261.63, d, 0.10, "w1"), _tone(329.63, d, 0.08, "w2"),
            _tone(392.0, d, 0.07, "w3"),
            "[w1][w2][w3]amix=inputs=3:normalize=0,lowpass=f=2500,"
            "afade=t=in:st=0:d=1.2[bgm]",
        ]
    # neutral：A 小三和弦轻颤铺底
    return [
        _tone(220.0, d, 0.12, "n1"), _tone(261.63, d, 0.10, "n2"),
        _tone(329.63, d, 0.09, "n3"),
        "[n1][n2][n3]amix=inputs=3:normalize=0,tremolo=f=0.15:d=0.3,"
        "afade=t=in:st=0:d=1.0[bgm]",
    ]


__all__ = ["BGM_MOODS", "MOOD_TENSION", "MOOD_SAD", "MOOD_TRIUMPH",
           "MOOD_WARM", "MOOD_NEUTRAL", "mood_for_emotion", "bed_for"]
