"""ScriptBrain — 拍摄版剧本组装、校验与导出 (Phase 2)

三份产物（多智能体文档 §2.2 协作要点）：
- `shooting_script.json`  完整拍摄版（含节拍、动作、台词）
- `scene_export.json`     面向统一资产大脑的精简视图（SceneDNA/IdentityDNA/PropDNA 直接消费）
- `character_list.json`   人物清单

`validate_shooting_script()` 是 ScriptBrain 与资产大脑之间的**结构契约**：
只校验"下游能不能接单"，不评价剧本好不好（那是 Script Critic 的事）。
把两者分开，是为了让外来剧本（人写的、别的工具产的）也能走同一道校验。
"""

from __future__ import annotations

import re
from typing import Any, Final

from asset_brain.common import schemas

from .agents import make_script_id
from .brief import ScriptBrief
from .library import get_genre

#: character_id / prop_id / scene_id 会变成 Bundle 内的目录名，必须是安全 ASCII
_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]+$")

#: 场景必备字段 —— 少一个下游就接不了单
REQUIRED_SCENE_FIELDS: Final[tuple[str, ...]] = (
    "scene_id",
    "location_description",
    "time_of_day",
    "mood",
    "required_atoms",
    "spatial_needs",
)


class ScriptValidationError(ValueError):
    """拍摄版剧本不满足结构契约。"""


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def assemble_shooting_script(
    brief: ScriptBrief,
    concept: dict[str, Any],
    show_bible: dict[str, Any],
    episodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """把各 Agent 的产物拼成完整 shooting_script。"""
    genre = get_genre(concept["genre"])
    all_scenes = [s for ep in episodes for s in ep["scenes"]]

    # 人物：补出场记录（没戏的人物会被 Critic 揪出来）
    characters = []
    for c in show_bible["characters"]:
        appears = [s["scene_id"] for s in all_scenes if c["character_id"] in s["characters"]]
        characters.append({**c, "appears_in": appears})

    # 道具：只声明剧本真正用到的，states_needed 也只取真正用到的状态
    props = []
    for spec in genre.props:
        pid = f"prop_{spec.key}"
        timeline = [
            {"scene_id": s["scene_id"], "state": s["prop_states"][pid]}
            for s in all_scenes
            if pid in (s.get("prop_states") or {})
        ]
        if not timeline:
            continue
        used_states = [st for st in spec.states if any(t["state"] == st for t in timeline)]
        props.append(
            {
                "prop_id": pid,
                "name": spec.name,
                "description": spec.description,
                "states_needed": used_states,
                "story_function": spec.story_function,
                "continuity_critical": spec.continuity_critical,
                "appears_in": [t["scene_id"] for t in timeline],
                "state_timeline": timeline,
            }
        )

    return {
        "schema_version": schemas.SCHEMA_SHOOTING_SCRIPT,
        "script_id": make_script_id(brief),
        "title": show_bible["title"],
        "theme": brief.theme,
        "genre": genre.key,
        "genre_label": genre.label,
        "logline": concept["logline"],
        "target_platform": brief.target_platform,
        "generator": "scriptbrain.mock.v1",
        "created_at": schemas.utc_now_iso(),
        "episodes": episodes,
        "characters": characters,
        "props": props,
        "hook_map": concept["hook_map"],
        "world_rules": show_bible["world_rules"],
        "relationship_map": show_bible["relationship_map"],
    }


# ---------------------------------------------------------------------------
# 校验（结构契约）
# ---------------------------------------------------------------------------


def _require_id(value: Any, field: str, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScriptValidationError(f"{where}: {field} 缺失或为空 -> {value!r}")
    if not _ID_RE.match(value):
        raise ScriptValidationError(
            f"{where}: {field} 含非法字符（会变成目录名，仅允许字母数字 . _ -）"
            f" -> {value!r}"
        )
    return value


def validate_shooting_script(script: dict[str, Any]) -> dict[str, Any]:
    """校验拍摄版剧本能否被资产大脑消费。不合格立即抛错，返回原对象便于链式调用。"""
    if not isinstance(script, dict):
        raise ScriptValidationError(f"剧本必须是 dict，实际 {type(script).__name__}")
    schemas.require_schema_version(
        script.get("schema_version"), schemas.SCHEMA_SHOOTING_SCRIPT
    )
    _require_id(script.get("script_id"), "script_id", "script")

    episodes = script.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise ScriptValidationError("script: episodes 不能为空")

    declared_chars = set()
    for c in script.get("characters") or []:
        cid = _require_id(c.get("character_id"), "character_id", "characters[]")
        if not c.get("name"):
            raise ScriptValidationError(f"人物 {cid} 缺少 name")
        if not c.get("role_type"):
            raise ScriptValidationError(f"人物 {cid} 缺少 role_type")
        declared_chars.add(cid)

    declared_props = set()
    for p in script.get("props") or []:
        pid = _require_id(p.get("prop_id"), "prop_id", "props[]")
        if not p.get("name"):
            raise ScriptValidationError(f"道具 {pid} 缺少 name")
        if not p.get("states_needed"):
            raise ScriptValidationError(f"道具 {pid} 的 states_needed 不能为空")
        declared_props.add(pid)

    seen_scene_ids: set[str] = set()
    for ep in episodes:
        ep_id = _require_id(ep.get("episode_id"), "episode_id", "episodes[]")
        scenes = ep.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ScriptValidationError(f"{ep_id}: scenes 不能为空")
        for s in scenes:
            where = f"{ep_id}/{s.get('scene_id', '?')}"
            sid = _require_id(s.get("scene_id"), "scene_id", where)
            if sid in seen_scene_ids:
                raise ScriptValidationError(f"scene_id 重复: {sid}")
            seen_scene_ids.add(sid)
            for f in REQUIRED_SCENE_FIELDS:
                if not s.get(f):
                    raise ScriptValidationError(f"{where}: 缺少必备字段 {f}")
            if not isinstance(s["required_atoms"], list):
                raise ScriptValidationError(f"{where}: required_atoms 必须是列表")
            for cid in s.get("characters") or []:
                if cid not in declared_chars:
                    raise ScriptValidationError(f"{where}: 人物 {cid} 未在顶层声明")
            for pid in s.get("props") or []:
                if pid not in declared_props:
                    raise ScriptValidationError(f"{where}: 道具 {pid} 未在顶层声明")
    return script


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def build_scene_export(script: dict[str, Any]) -> dict[str, Any]:
    """导出面向统一资产大脑的精简视图。

    结构与 `AssetBrainFacade` / `Orchestrator.dispatch_assets` 的入参完全对齐：
    顶层 scenes / characters / props，每项只保留资产生成需要的字段。
    """
    scenes = [
        {
            "scene_id": s["scene_id"],
            "episode_id": s.get("episode_id"),
            "location_description": s["location_description"],
            "time_of_day": s["time_of_day"],
            "mood": s["mood"],
            "camera_intent": s.get("camera_intent", ""),
            "required_atoms": list(s["required_atoms"]),
            "spatial_needs": s["spatial_needs"],
            "characters": list(s.get("characters") or []),
            "props": list(s.get("props") or []),
        }
        for ep in script["episodes"]
        for s in ep["scenes"]
    ]
    characters = [
        {
            "character_id": c["character_id"],
            "name": c["name"],
            "age_range": c.get("age_range", ""),
            "gender": c.get("gender", ""),
            "ethnicity_preference": c.get("ethnicity_preference", "东亚/中国"),
            "personality_keywords": list(c.get("personality_keywords") or []),
            "role_type": c["role_type"],
            "need_age_line": bool(c.get("need_age_line")),
            "need_family": bool(c.get("need_family")),
        }
        for c in script.get("characters") or []
    ]
    props = [
        {
            "prop_id": p["prop_id"],
            "name": p["name"],
            "description": p.get("description", ""),
            "states_needed": list(p["states_needed"]),
            "story_function": p.get("story_function", ""),
            "continuity_critical": bool(p.get("continuity_critical")),
            # 直接喂给 PropDNA 的跨镜头状态时间线
            "state_timeline": list(p.get("state_timeline") or []),
        }
        for p in script.get("props") or []
    ]
    return {
        "schema_version": schemas.SCHEMA_SCENE_EXPORT,
        "script_id": script["script_id"],
        "title": script.get("title", ""),
        "scenes": scenes,
        "characters": characters,
        "props": props,
    }


def build_character_list(script: dict[str, Any]) -> dict[str, Any]:
    """导出人物清单（Cast Universe / IdentityDNA 直接消费）。"""
    return {
        "schema_version": schemas.SCHEMA_CHARACTER_LIST,
        "script_id": script["script_id"],
        "characters": [dict(c) for c in script.get("characters") or []],
    }
