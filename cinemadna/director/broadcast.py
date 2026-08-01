"""DirectorDNA 发单 —— 把锁定的总谱镜头化成下游只执行的 shot 单。

总谱 = 唯一真源。broadcast 把每个 ShotPlan 展开成下游（Scene/Identity/Prop/
Performance/Render）能直接执行的 shot dict：携带 story_function、可执行 action_beats、
镜头语言、道具接触、进出状态、以及 **plan_hash**（任何产物可追溯到总谱）。

下游只读这些字段，不得改叙事、不得发明总谱里没有的角色/道具/动作。
"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan, ShotPlan
from director.shot_contract import (
    SHOT_CLOSEUP, SHOT_ESTABLISHING, SHOT_INSERT, SHOT_MEDIUM, SHOT_REACTION,
)

#: 景别（中文）→ 内部 shot_type
_SIZE_TO_TYPE = {
    "全景": SHOT_ESTABLISHING, "远景": SHOT_ESTABLISHING,
    "中景": SHOT_MEDIUM, "中近景": SHOT_MEDIUM,
    "近景": SHOT_CLOSEUP, "特写": SHOT_CLOSEUP, "大特写": SHOT_INSERT,
}
_CAM_MOVE_KEYS = ("shot_size", "movement", "angle", "intent")
_INTENSITY = {"钩子": 0.7, "施压": 0.8, "揭示": 0.6, "反应": 0.7,
              "反转": 0.9, "决策": 0.75, "收束": 0.85, "铺垫": 0.5}


def _shot_type_for(sp: ShotPlan) -> str:
    # 有台词=说话镜（必须能张嘴，不能落无声景别）
    if sp.line and sp.dialogue_owner:
        t = _SIZE_TO_TYPE.get(sp.camera.get("shot_size", ""), SHOT_CLOSEUP)
        return SHOT_MEDIUM if t in (SHOT_INSERT, SHOT_REACTION, SHOT_ESTABLISHING) else t
    if sp.story_function == "揭示" and sp.prop_usage.get("prop_id"):
        return SHOT_INSERT
    if sp.story_function == "反应" and not sp.line:
        return SHOT_REACTION
    return _SIZE_TO_TYPE.get(sp.camera.get("shot_size", ""), SHOT_MEDIUM)


def plan_to_shots(plan: DirectorMasterPlan) -> list[dict[str, Any]]:
    """锁定总谱 → 下游执行用 shot 单（携带 plan_hash 追溯）。"""
    layout = {x["scene_id"]: x for x in plan.scene_layout}
    shots: list[dict[str, Any]] = []
    for sp in plan.shots:
        stype = _shot_type_for(sp)
        # 「谁在画面」唯一真源 = staging 写入的 action_beats[].actors（执行合同
        # in_frame_slots 亦读此）。以前这里按场景 cast + 特写启发式重算,与①正反打
        # 单人镜脱节 → 中景对峙被当双人 → character_images 双人 → 漏锁 PuLID → 同角色
        # 渲成多张脸(身份漂移根因)。改为直接采信 staging 分配,三处(broadcast/执行
        # 合同/渲染请求)同源。未过 staging 的裸总谱(无 actors 键)回退旧启发式。
        beats = sp.action_beats or []
        staged = any("actors" in ab for ab in beats)
        if staged:
            in_frame = []
            for ab in beats:
                for a in (ab.get("actors") or []):
                    if a and a not in in_frame:
                        in_frame.append(a)
        else:
            in_frame = list((layout.get(sp.scene_id) or {}).get("characters") or [])
            if sp.dialogue_owner and stype in (SHOT_CLOSEUP,):
                in_frame = ([sp.dialogue_owner] if sp.dialogue_owner in in_frame
                            else in_frame)
        props = [sp.prop_usage["prop_id"]] if sp.prop_usage.get("prop_id") else []
        shots.append({
            "shot_id": sp.shot_id, "scene_id": sp.scene_id,
            "episode_id": sp.scene_id.split("_")[0], "order": sp.order,
            "shot_type": stype, "duration_sec": float(sp.camera.get("duration_sec") or 3),
            "camera": {**{k: sp.camera.get(k, "") for k in _CAM_MOVE_KEYS},
                       "shot_type": stype},
            "emotion": {"beat": sp.beat_type, "mood": sp.emotion,
                        "intensity": _INTENSITY.get(sp.story_function, 0.6)},
            "characters": in_frame or ([sp.dialogue_owner] if sp.dialogue_owner else []),
            "props": props,
            "prop_states": {props[0]: sp.prop_usage.get("state", "")} if props else {},
            "dialogue": ([{"character_id": sp.dialogue_owner, "line": sp.line}]
                         if sp.line and sp.dialogue_owner else []),
            "difficulty": "normal",
            "story_function": sp.story_function,
            "internal_shift": sp.internal_shift,
            "narrative": {
                "cause": (layout.get(sp.scene_id) or {}).get("cause", ""),
                "effect": (layout.get(sp.scene_id) or {}).get("effect", ""),
                "entry_state": sp.entry_state, "exit_state": sp.exit_state,
                "emotional_tone": (layout.get(sp.scene_id) or {}).get(
                    "emotional_tone", sp.emotion),
                "authored_by": "llm"},   # 总谱源自 LLM 作者化剧本 → 硬门生效
            "beat_type": sp.beat_type,
            "action_beats": sp.action_beats,          # 可执行动作（Performance 执行）
            "performance_intent": sp.performance_intent,
            "prop_usage": sp.prop_usage,
            "plan_hash": plan.plan_hash,              # 追溯到总谱
        })
    return shots


__all__ = ["plan_to_shots"]
