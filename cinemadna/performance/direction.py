"""表演导演 + 对白语气 Agent (Phase H) —— 让表演更像短剧，而非念台词。

两个新 Agent：
- **对白语气 Agent** `assign_tone()`：给每句台词定语气（哀求/施压/讥讽/崩溃/克制/冷硬）
  + 语速节奏 + 关键台词前停顿；并产出 **prosody**（喂给 AudioDNA 的韵律参数，声画一致）。
- **表演导演 Agent** `direct_performance()`：跨镜统一——按情绪曲线在高峰前插停顿、
  给强情绪镜头升表演强度、确保反应镜真有反应、给对白挂语气。就地改合约的 performance/
  voice_contract 字段（未签发前）。

与 AudioDNA 协同：语气 → prosody 写进 voice_contract 绑定，AudioDNA 合成时直接采用。
"""

from __future__ import annotations

from typing import Any

TONE_PLEAD = "哀求"
TONE_PRESS = "施压"
TONE_MOCK = "讥讽"
TONE_BREAK = "崩溃"
TONE_CALM = "克制"
TONE_COLD = "冷硬"

_PRESS_KW = ("必须", "不然", "否则", "别想", "警告", "最后", "限你", "单位",
             "医院", "一分不能少", "今晚", "十二点")
_PLEAD_KW = ("求", "再给", "宽限", "拜托", "容我", "缓", "行行好", "立刻还")
_MOCK_KW = ("糊弄", "呵呵", "是吗", "装", "可笑", "笑话")

#: 语气 → 交付参数（pace）+ 韵律（喂 AudioDNA：rate/stability/style）
_DELIVERY: dict[str, dict[str, Any]] = {
    TONE_PLEAD: {"pace": "急促", "prosody": {"rate": 1.15, "stability": 0.3, "style": 0.72}},
    TONE_PRESS: {"pace": "缓而重", "prosody": {"rate": 0.9, "stability": 0.35, "style": 0.62}},
    TONE_MOCK:  {"pace": "拖沓上扬", "prosody": {"rate": 1.05, "stability": 0.4, "style": 0.7}},
    TONE_BREAK: {"pace": "失控", "prosody": {"rate": 1.2, "stability": 0.2, "style": 0.86}},
    TONE_CALM:  {"pace": "平稳", "prosody": {"rate": 0.95, "stability": 0.6, "style": 0.25}},
    TONE_COLD:  {"pace": "低沉", "prosody": {"rate": 0.92, "stability": 0.5, "style": 0.4}},
}


def assign_tone(text: str, emotion: dict[str, Any] | None,
                traits: list[str] | None = None) -> dict[str, Any]:
    """由台词内容 + 情绪 + 性格定语气，返回 {tone, pace, pause_before, prosody}。"""
    intensity = float((emotion or {}).get("intensity", 0.5) or 0.5)
    t = str(text or "")
    if any(k in t for k in _PRESS_KW) and intensity >= 0.55:
        tone = TONE_PRESS
    elif any(k in t for k in _PLEAD_KW):
        tone = TONE_PLEAD
    elif any(k in t for k in _MOCK_KW):
        tone = TONE_MOCK
    elif intensity >= 0.85:
        tone = TONE_BREAK
    elif intensity <= 0.4:
        tone = TONE_CALM
    else:
        tone = TONE_COLD
    d = _DELIVERY[tone]
    return {
        "tone": tone,
        "pace": d["pace"],
        # 关键台词（施压/崩溃）前给一个停顿，制造戏剧张力
        "pause_before": tone in (TONE_PRESS, TONE_BREAK),
        "prosody": dict(d["prosody"]),
    }


def amplify_prosody_by_beats(
    prosody: dict[str, Any], speaking_beats: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], bool]:
    """把第2阶段"表演规划"的说话节拍折进 TTS 韵律（表演语气→真实语音）。

    - 爆发(burst)：更快 + 更外放 + 更不稳 → 冲击力（reversal/反击台词）
    - 加重(emphasis)：风格略提，关键词有态度
    - 停顿(pause)：返回 True → 让语音前挂一个停顿
    返回 (调整后 prosody, 是否需要停顿前置)。
    """
    p = dict(prosody or {})
    types = {b.get("type") for b in (speaking_beats or [])}
    if "burst" in types:
        p["rate"] = round(min(1.3, float(p.get("rate", 1.0)) + 0.06), 3)
        p["style"] = round(min(0.95, float(p.get("style", 0.5)) + 0.10), 3)
        p["stability"] = round(max(0.15, float(p.get("stability", 0.4)) - 0.06), 3)
    elif "emphasis" in types:
        p["style"] = round(min(0.95, float(p.get("style", 0.5)) + 0.05), 3)
    return p, ("pause" in types)


def direct_performance(
    contracts: list[dict[str, Any]],
    characters_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """表演导演：跨镜给对白挂语气、插停顿、升强度、补反应。就地改合约（未签发）。

    返回导演说明（哪些镜头加了什么），供审计。
    """
    characters_by_id = characters_by_id or {}
    notes: list[dict[str, Any]] = []
    ordered = sorted(contracts, key=lambda c: c.get("order", 0))
    n = len(ordered)

    for idx, c in enumerate(ordered):
        emotion = c.get("emotion") or {}
        perf = c.get("performance") or {}
        beats = perf.get("acting_beats") or []
        vc = c.get("voice_contract") or {}
        bindings = {b.get("line"): b for b in (vc.get("bindings") or [])}
        speaking_beats = c.get("speaking_beats") or []   # 第2阶段·表演规划下传
        added_pause = 0
        toned = 0

        new_beats: list[dict[str, Any]] = []
        for b in beats:
            if b.get("type") == "LINE":
                traits = list((characters_by_id.get(b.get("character_id")) or {})
                              .get("personality_keywords") or [])
                tv = assign_tone(b.get("cue"), emotion, traits)
                # 表演规划的说话节拍折进韵律（爆发/加重/停顿），并可强制停顿前置
                tv["prosody"], beat_pause = amplify_prosody_by_beats(
                    tv["prosody"], speaking_beats)
                if beat_pause:
                    tv["pause_before"] = True
                # 关键台词前插一个"停顿"节拍
                if tv["pause_before"]:
                    new_beats.append({
                        "beat_index": len(new_beats), "type": "PAUSE",
                        "character_id": b.get("character_id"),
                        "cue": "台词前一个停顿，蓄势",
                        "micro_expression": {"primary": "屏住呼吸", "intensity":
                                             round(float(emotion.get("intensity", 0.5)), 3)},
                        "body": "停顿半秒"})
                    added_pause += 1
                b = {**b, "tone": tv["tone"], "pace": tv["pace"], "prosody": tv["prosody"]}
                toned += 1
                # 语气 + 韵律写进声音绑定 → AudioDNA 直接采用（声画一致）
                bd = bindings.get(b.get("cue"))
                if bd is not None:
                    bd["tone"] = tv["tone"]
                    bd["prosody"] = tv["prosody"]
            b = {**b, "beat_index": len(new_beats)}
            new_beats.append(b)

        # 高潮镜头（情绪最强的后半段）表演强度上抬一档标注
        is_climax = float(emotion.get("intensity", 0.5)) >= 0.8 and idx >= n // 2
        perf["acting_beats"] = new_beats
        perf["direction"] = {
            "tones": toned, "pauses_added": added_pause,
            "is_climax": is_climax,
            "reaction_beats": sum(1 for b in new_beats if b.get("type") == "REACTION"),
        }
        c["performance"] = perf
        notes.append({"shot_id": c["shot_id"], **perf["direction"]})

    return {"directed_shots": len(ordered), "notes": notes}


__all__ = ["assign_tone", "direct_performance", "amplify_prosody_by_beats",
           "TONE_PLEAD", "TONE_PRESS", "TONE_MOCK", "TONE_BREAK",
           "TONE_CALM", "TONE_COLD"]
