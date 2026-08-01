"""ShowrunnerDirector（总导演）—— 理解剧本，汇总各专业输出，签发 DirectorMasterPlan。

从 ScriptBrain 的拍摄版剧本（含 LLM 作者化的高密度节拍）出发，导演层把它**理解**成
一份可执行的《导演总谱》：提炼冲突主线、锁定人物立场/权力、把每个节拍升成含
**可执行动作**（body/face/micro）+ 镜头语言 + 道具接触 + 进出状态的镜头计划。

产物是**唯一真源**；下游只执行。总导演还负责仲裁（专业智能体给建议，这里落定）。
"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan, ShotPlan

#: 节拍类型 → 镜头叙事功能（导演层的"这一镜是干嘛的"）
_BEAT_TO_FUNCTION = {
    "setup": "铺垫", "info": "揭示", "action": "施压", "reaction": "反应",
    "power_shift": "施压", "interruption": "施压", "obstruction": "施压",
    "counterattack": "反转", "reversal": "反转", "cliffhanger": "收束",
}
#: 节拍类型 → 景别/角度/运动 意图（镜头语言由 CameraLanguageAgent 复核）
_BEAT_TO_CAMERA = {
    "setup": ("全景", "平视", "缓推", "建立场景与处境"),
    "info": ("大特写", "俯视", "固定", "呈现关键信息/道具"),
    "action": ("中景", "平视", "手持", "施压动作"),
    "reaction": ("近景", "平视", "固定", "情绪反应"),
    "power_shift": ("中近景", "仰视", "缓推", "权力转移"),
    "interruption": ("中景", "平视", "快切", "打断"),
    "obstruction": ("中景", "平视", "固定", "目标受阻"),
    "counterattack": ("中近景", "平视", "推近", "主动反击"),
    "reversal": ("特写", "平视", "推近", "反转"),
    "cliffhanger": ("特写", "平视", "缓拉", "强悬念收束"),
}


class ShowrunnerDirector:
    """总导演：把剧本理解成导演总谱骨架，供专业智能体细化、验收官签发。"""

    def build_skeleton(self, shooting_script: dict[str, Any], *, story_id: str
                       ) -> DirectorMasterPlan:
        chars = {c["character_id"]: c for c in shooting_script.get("characters") or []}
        scenes = [s for ep in shooting_script.get("episodes") or []
                  for s in ep.get("scenes") or []]

        # 故事主线（冲突/关系/目标）——从 logline + 场因果提炼
        spine = {
            "logline": shooting_script.get("logline", ""),
            "central_conflict": shooting_script.get("theme", ""),
            "protagonist_goal": _first(scenes, "narrative", "effect"),
            "relationships": _relationship_map(chars),
        }
        # 人物锁定（立场/权力由 RelationshipContinuityAgent 复核细化）
        cast_lock = {
            cid: {"name": c.get("name") or cid, "role": c.get("role", ""),
                  "stance": "", "power": "", "appearance": c.get("appearance", "")}
            for cid, c in chars.items()}
        # 场景布局（走位由 StagingBlockingAgent 复核）
        scene_layout = [{
            "scene_id": s.get("scene_id"),
            "location": s.get("location_description") or s.get("location_key", ""),
            "mood": s.get("mood", ""), "characters": list(s.get("characters") or []),
            "spatial": s.get("spatial_needs", ""), "blocking": "",
            # 场级因果（供 broadcast 回填每镜 narrative，保连续性完整）
            "cause": (s.get("narrative") or {}).get("cause", ""),
            "effect": (s.get("narrative") or {}).get("effect", ""),
            "emotional_tone": (s.get("narrative") or {}).get("emotional_tone",
                                                             s.get("mood", ""))}
            for s in scenes]
        # 道具计划（状态+接触由 PropInformationAgent 复核）
        prop_plan = [{"prop_id": p.get("prop_id"), "name": p.get("name", ""),
                      "state": _prop_state(p), "contact": ""}
                     for p in shooting_script.get("props") or []]

        shot_list: list[ShotPlan] = []
        emotion_curve: list[dict[str, Any]] = []
        order = 1
        for s in scenes:
            nar = s.get("narrative") or {}
            for b in s.get("beats") or []:
                bt = (b.get("type") or "info").lower()
                fn = "钩子" if order == 1 else _BEAT_TO_FUNCTION.get(bt, "铺垫")
                size, angle, mov, intent = _BEAT_TO_CAMERA.get(
                    bt, ("中景", "平视", "固定", "推进"))
                line = (b.get("line") or "").strip()
                owner = b.get("character_id") if line else None
                sid = f"{s['scene_id']}_SH{order:03d}"
                shot_list.append(ShotPlan(
                    shot_id=sid, order=order, scene_id=s["scene_id"],
                    story_function=fn,
                    action_beats=_action_beats(b),             # 可执行动作（ActionAgent 复核）
                    performance_intent=b.get("emotion", "") or nar.get("emotional_tone", ""),
                    dialogue_owner=owner, line=line,
                    camera={"shot_size": size, "angle": angle, "movement": mov,
                            "duration_sec": _dur(b), "intent": intent},
                    prop_usage={}, entry_state=nar.get("entry_state", ""),
                    exit_state=nar.get("exit_state", ""), beat_type=bt,
                    internal_shift=(b.get("internal_shift") or "").strip(),
                    emotion=b.get("emotion", "")))
                emotion_curve.append({"shot_id": sid, "beat_type": bt,
                                      "emotion": b.get("emotion", "")})
                order += 1

        density_plan = {
            "hook_shot": shot_list[0].shot_id if shot_list else None,
            "reveals": [x.shot_id for x in shot_list if x.beat_type in ("info", "power_shift")],
            "turns": [x.shot_id for x in shot_list
                      if x.beat_type in ("reversal", "counterattack", "cliffhanger")],
            "target_shots": len(shot_list)}

        return DirectorMasterPlan(
            story_id=story_id, story_spine=spine, cast_lock=cast_lock,
            scene_layout=scene_layout, prop_plan=prop_plan,
            emotion_curve=emotion_curve, density_plan=density_plan,
            shot_list=shot_list)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _dur(b: dict) -> float:
    try:
        return round(min(4.0, max(2.0, float(b.get("seconds") or 3.0))), 1)
    except (TypeError, ValueError):
        return 3.0


def _action_beats(b: dict) -> list[dict[str, Any]]:
    """把节拍描述升成可执行动作（起→转→结），ActionPerformanceIntentAgent 会复核。"""
    desc = (b.get("description") or "").strip()
    shift = (b.get("internal_shift") or "").strip()
    beats: list[dict[str, Any]] = []
    if desc:
        beats.append({"body_action": desc, "face_action": b.get("emotion", ""),
                      "micro_beats": [x for x in shift.split("→") if x][:3]})
    if shift and not desc:
        parts = [x for x in shift.split("→") if x]
        beats.append({"body_action": shift, "face_action": b.get("emotion", ""),
                      "micro_beats": parts[:3]})
    return beats or [{"body_action": "（待导演补动作）", "face_action": b.get("emotion", ""),
                      "micro_beats": []}]


def _relationship_map(chars: dict) -> list[str]:
    ids = list(chars)
    if len(ids) >= 2:
        return [f"{chars[ids[0]].get('name', ids[0])} ↔ {chars[ids[1]].get('name', ids[1])}"]
    return []


def _prop_state(p: dict) -> str:
    st = p.get("states_needed") or []
    return st[0] if st else "默认"


def _first(scenes: list, *keys: str) -> str:
    for s in scenes:
        v = s
        for k in keys:
            v = (v or {}).get(k) if isinstance(v, dict) else None
        if v:
            return str(v)
    return ""


__all__ = ["ShowrunnerDirector"]
