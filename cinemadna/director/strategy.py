"""DirectorDNA · Shot Strategy Agent (Phase 3)

把一场戏拆成若干镜头：定镜头数、景别、运镜、时长、情绪强度、谁在画面里。

规则而非模型（Phase 3 不接 LLM），但规则是有电影逻辑的：
- 镜头数按场景时长推导（约 5 秒一个镜头），钳制在 2~4 之间
- 每种节拍有自己的镜头语言模板（钩子先建立空间、冲突用正反打、悬念收特写）
- 模板会**按实际条件降级**：没有道具就不排插入镜，只有一个人就不排反应镜
  —— 标杆样片正好是「1 场景 1 主角」，单人场景必须能排出合法镜头表
- 每个道具至少保证有一个镜头带到，否则道具白生成了
"""

from __future__ import annotations

from typing import Any, Final

from asset_brain.common.hashing import stable_score

from .shot_contract import (
    SHOT_CLOSEUP,
    SHOT_ESTABLISHING,
    SHOT_INSERT,
    SHOT_MEDIUM,
    SHOT_REACTION,
)

#: 平均每个镜头覆盖的秒数（短剧节奏）
SECONDS_PER_SHOT: Final = 5.0
MIN_SHOTS_PER_SCENE: Final = 2
#: 上限取 8：标杆样片是「1 场景 5–8 镜头 30–60 秒」，
#: 单场戏必须能一口气排到 8 个镜头
MAX_SHOTS_PER_SCENE: Final = 8

#: 各节拍的镜头语言模板
_BEAT_PATTERNS: Final[dict[str, tuple[str, ...]]] = {
    "HOOK": (SHOT_ESTABLISHING, SHOT_CLOSEUP, SHOT_MEDIUM, SHOT_INSERT),
    "CONFLICT": (SHOT_MEDIUM, SHOT_CLOSEUP, SHOT_REACTION, SHOT_CLOSEUP),
    "DEVELOPMENT": (SHOT_MEDIUM, SHOT_CLOSEUP, SHOT_REACTION, SHOT_MEDIUM),
    "CLIFFHANGER": (SHOT_CLOSEUP, SHOT_INSERT, SHOT_REACTION, SHOT_MEDIUM),
}
_DEFAULT_PATTERN: Final[tuple[str, ...]] = (
    SHOT_ESTABLISHING, SHOT_MEDIUM, SHOT_CLOSEUP, SHOT_MEDIUM,
)

#: 各节拍的情绪基准强度
_BEAT_INTENSITY: Final[dict[str, float]] = {
    "HOOK": 0.60, "CONFLICT": 0.85, "DEVELOPMENT": 0.50, "CLIFFHANGER": 0.95,
}

#: 景别 / 运镜 / 焦段
_CAMERA_BY_TYPE: Final[dict[str, dict[str, Any]]] = {
    SHOT_ESTABLISHING: {"shot_size": "全景", "movement": "缓推", "angle": "平视", "lens_mm": 24},
    SHOT_MEDIUM: {"shot_size": "中景", "movement": "固定", "angle": "平视", "lens_mm": 35},
    SHOT_CLOSEUP: {"shot_size": "特写", "movement": "微推", "angle": "略仰", "lens_mm": 85},
    SHOT_REACTION: {"shot_size": "近景", "movement": "固定", "angle": "过肩", "lens_mm": 50},
    SHOT_INSERT: {"shot_size": "大特写", "movement": "固定", "angle": "俯视", "lens_mm": 100},
}

#: 时长权重：建立镜给足，特写收紧
_DURATION_WEIGHT: Final[dict[str, float]] = {
    SHOT_ESTABLISHING: 1.25, SHOT_MEDIUM: 1.0, SHOT_CLOSEUP: 0.85,
    SHOT_REACTION: 0.75, SHOT_INSERT: 0.7,
}


def plan_shot_count(duration_sec: float) -> int:
    """按场景时长推导镜头数。"""
    raw = round(float(duration_sec) / SECONDS_PER_SHOT)
    return max(MIN_SHOTS_PER_SCENE, min(MAX_SHOTS_PER_SCENE, int(raw)))


def _degrade(shot_type: str, *, has_props: bool, char_count: int) -> str:
    """按场景实际条件降级镜头类型。"""
    if shot_type == SHOT_INSERT and not has_props:
        return SHOT_CLOSEUP
    if shot_type == SHOT_REACTION and char_count < 2:
        return SHOT_CLOSEUP
    return shot_type


def _distribute_durations(total: float, types: list[str]) -> list[float]:
    """把场景总时长按权重分配到各镜头，保证求和等于总时长。"""
    weights = [_DURATION_WEIGHT[t] for t in types]
    unit = float(total) / sum(weights)
    durations = [round(unit * w, 1) for w in weights]
    # 四舍五入的误差全部补到最后一个镜头，保证总时长不漂移
    drift = round(float(total) - sum(durations), 1)
    durations[-1] = round(durations[-1] + drift, 1)
    return durations


def _story_function(stype: str, has_line: bool, props: list, beat: str) -> str:
    """每镜叙事功能（供 NarrativeContinuityQA 完整性硬查）。"""
    if stype == SHOT_ESTABLISHING:
        return "建立场景与人物处境"
    if stype == SHOT_INSERT and props:
        return f"信息镜：呈现关键道具（{props[0]}）"
    if stype == SHOT_REACTION:
        return "情绪反应/转折"
    if stype == SHOT_CLOSEUP:
        return "推进对白与情绪" if has_line else "特写强化情绪"
    return "推进对白" if has_line else f"呈现{beat or '情节'}"


#: beat.type → 景别（内容驱动：有台词=特写，动作/权力=中景，反应=反应镜…）
def _beat_shot_type(beat: dict, has_props: bool) -> str:
    t = (beat.get("type") or "").lower()
    # 有台词 = 说话镜，必须能张嘴（特写），绝不能落到无声景别（否则"闭嘴出声"）。
    # 这条**优先于**节拍类型：reaction/info 等一旦带台词也走特写。
    if beat.get("line"):
        return SHOT_CLOSEUP
    if t == "setup":
        return SHOT_ESTABLISHING
    if t == "info" and has_props:
        return SHOT_INSERT
    if t == "reaction":
        return SHOT_REACTION
    if t in ("reversal", "cliffhanger"):
        return SHOT_CLOSEUP
    return SHOT_MEDIUM


def _plan_shots_from_beats(
    scene: dict[str, Any], beats: list[dict], *, seed: str, shot_index_start: int
) -> list[dict[str, Any]]:
    """内容驱动分镜：每个节拍 = 一个紧凑短镜（2–4s），高密度、不凑时长。"""
    characters = list(scene.get("characters") or [])
    props = list(scene.get("props") or [])
    prop_states = dict(scene.get("prop_states") or {})
    beat_val = scene.get("beat_type", "")
    base_intensity = _BEAT_INTENSITY.get(beat_val, 0.55)
    narrative = dict(scene.get("narrative") or {})
    shots: list[dict[str, Any]] = []
    props_left = list(props)

    for i, b in enumerate(beats):
        stype = _beat_shot_type(b, bool(props_left))
        dur = round(min(4.0, max(2.0, float(b.get("seconds") or 3.0))), 1)
        shot_id = f"{scene['scene_id']}_SH{shot_index_start + i:03d}"
        line = ({"character_id": b["character_id"], "line": b["line"]}
                if b.get("line") and b.get("character_id") else None)
        if stype in (SHOT_CLOSEUP, SHOT_REACTION) and characters:
            focus = (line["character_id"] if line
                     else (b.get("character_id") or characters[i % len(characters)]))
            in_frame = [focus] if focus in characters else [characters[0]]
        else:
            in_frame = list(characters)
        # 信息镜带上道具（把剧本道具铺到 info/insert 拍）
        shot_props = []
        if stype in (SHOT_INSERT, SHOT_ESTABLISHING, SHOT_MEDIUM) and props_left:
            shot_props = [props_left.pop(0)]
        cam = dict(_CAMERA_BY_TYPE[stype])
        cam["intent"] = f"{scene.get('camera_intent', '')}｜{b.get('type', '')}".strip("｜")
        shots.append({
            "shot_id": shot_id, "scene_id": scene["scene_id"],
            "episode_id": scene.get("episode_id", ""),
            "order": shot_index_start + i, "shot_type": stype, "duration_sec": dur,
            "camera": cam,
            "emotion": {"beat": beat_val,
                        "intensity": round(min(1.0, base_intensity + 0.03 * i
                                     + stable_score(-0.02, 0.02, seed, shot_id)), 4),
                        "mood": scene.get("mood", "")},
            "characters": in_frame, "props": shot_props,
            "prop_states": {p: prop_states.get(p, "") for p in shot_props},
            "dialogue": [line] if line else [],
            "difficulty": scene.get("production_difficulty", "normal"),
            "story_function": (b.get("description") or "").strip()
                              or _story_function(stype, bool(line), shot_props, beat_val),
            "internal_shift": (b.get("internal_shift") or "").strip(),
            "narrative": narrative,
            "beat_type": (b.get("type") or "info").lower(),
        })
    # 兜底：未铺到的道具塞进第一个镜
    covered = {p for s in shots for p in s["props"]}
    for missing in [p for p in props if p not in covered]:
        if shots:
            shots[0]["props"].append(missing)
            shots[0]["prop_states"][missing] = prop_states.get(missing, "")
    return shots


def plan_scene_shots(
    scene: dict[str, Any], *, seed: str, shot_index_start: int = 1
) -> list[dict[str, Any]]:
    """为一场戏排出镜头表（尚未绑定资产，那是 Shot Contract Generator 的事）。"""
    # 内容驱动优先：LLM 写了节拍表就按节拍出紧凑短镜（高密度）
    beats = scene.get("beats")
    if beats:
        return _plan_shots_from_beats(
            scene, beats, seed=seed, shot_index_start=shot_index_start)
    beat = scene.get("beat_type", "")
    characters = list(scene.get("characters") or [])
    props = list(scene.get("props") or [])
    prop_states = dict(scene.get("prop_states") or {})
    duration = float(scene.get("estimated_duration_sec") or SECONDS_PER_SHOT * 2)

    n = plan_shot_count(duration)
    pattern = _BEAT_PATTERNS.get(beat, _DEFAULT_PATTERN)
    types = [
        _degrade(pattern[i % len(pattern)], has_props=bool(props), char_count=len(characters))
        for i in range(n)
    ]
    durations = _distribute_durations(duration, types)
    base_intensity = _BEAT_INTENSITY.get(beat, 0.55)
    dialogue = list(scene.get("dialogue") or [])

    shots: list[dict[str, Any]] = []
    for i, (stype, dur) in enumerate(zip(types, durations)):
        shot_id = f"{scene['scene_id']}_SH{shot_index_start + i:03d}"
        line = dialogue[i] if i < len(dialogue) else None
        # 无台词镜封顶 6s，禁止静态凑秒（叙事连贯 QA 会硬查 >6.5s 的静默镜）
        if not line and stype not in (SHOT_ESTABLISHING, SHOT_INSERT):
            dur = round(min(dur, 6.0), 2)

        # 画面里有谁：特写/反应镜聚焦一个人，其余带上全部在场人物
        if stype in (SHOT_CLOSEUP, SHOT_REACTION) and characters:
            focus = line["character_id"] if line else characters[i % len(characters)]
            in_frame = [focus] if focus in characters else [characters[0]]
        else:
            in_frame = list(characters)

        # 道具：只在能看见东西的景别里带（脸部特写不带）
        shot_props = [] if stype in (SHOT_CLOSEUP, SHOT_REACTION) else list(props)

        cam = dict(_CAMERA_BY_TYPE[stype])
        cam["intent"] = f"{scene.get('camera_intent', '')}｜{cam['shot_size']}{cam['movement']}".strip("｜")

        shots.append(
            {
                "shot_id": shot_id,
                "scene_id": scene["scene_id"],
                "episode_id": scene.get("episode_id", ""),
                "order": shot_index_start + i,
                "shot_type": stype,
                "duration_sec": dur,
                "camera": cam,
                "emotion": {
                    "beat": beat,
                    # 场内情绪随镜头推进小幅上扬
                    "intensity": round(
                        min(1.0, base_intensity + 0.03 * i
                            + stable_score(-0.02, 0.02, seed, shot_id)),
                        4,
                    ),
                    "mood": scene.get("mood", ""),
                },
                "characters": in_frame,
                "props": shot_props,
                "prop_states": {p: prop_states.get(p, "") for p in shot_props},
                "dialogue": [line] if line else [],
                "difficulty": scene.get("production_difficulty", "normal"),
                # 叙事层：每镜的功能 + 继承本场因果/进出状态（叙事连贯 QA 会硬查）
                "story_function": _story_function(stype, bool(line), shot_props, beat),
                "narrative": dict(scene.get("narrative") or {}),
            }
        )

    # 兜底：每个道具至少要有一个镜头带到，否则这件道具白生成了
    covered = {p for s in shots for p in s["props"]}
    for missing in [p for p in props if p not in covered]:
        target = shots[0]
        target["props"].append(missing)
        target["prop_states"][missing] = prop_states.get(missing, "")

    return shots


def build_emotion_curve(shots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """情绪曲线：给 BGM / 剪辑节奏用。"""
    return [
        {
            "shot_id": s["shot_id"],
            "scene_id": s["scene_id"],
            "order": s["order"],
            "intensity": s["emotion"]["intensity"],
            "beat": s["emotion"]["beat"],
        }
        for s in shots
    ]
