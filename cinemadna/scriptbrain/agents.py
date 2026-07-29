"""ScriptBrain V4.1 · 多智能体 (Phase 2 最小可运行版)

对应多智能体文档 §2.2 的 Agent 分工。Phase 2 每个 Agent 都是**纯函数**：
输入上一环的结构化产物，输出自己的结构化产物，不持有状态、不调用外部模型。

    Idea Miner → Showrunner Brain → Plot Architect → Episode Writer
      → Dialogue Master → Script Critic + Market Hook Critic

接真实编剧模型时逐个替换函数体即可，Supervisor（service.py）与下游契约不变。

所有随机性都走 hashing.stable_unit：同一 brief 永远生成同一部剧，
剧本可回归、Critic 结论可复现。
"""

from __future__ import annotations

from typing import Any, Sequence, TypeVar

from asset_brain.common import schemas
from asset_brain.common.hashing import sha256_hex, stable_score, stable_unit

from .brief import ScriptBrief
from .library import (
    BEAT_CLIFFHANGER,
    BEAT_CONFLICT,
    BEAT_DEVELOPMENT,
    BEAT_HOOK,
    REQUIRED_BEATS,
    GenreTemplate,
    get_genre,
    match_genre,
)

T = TypeVar("T")

#: 每场戏的估计时长（秒）——短剧节奏，用于后续 Director/Render 估算
_SCENE_SECONDS = {BEAT_HOOK: 12, BEAT_CONFLICT: 20, BEAT_DEVELOPMENT: 15,
                  BEAT_CLIFFHANGER: 10}

#: 留存分及格线（低于此值需要人工看一眼）
MIN_RETENTION_SCORE = 0.60


def _pick(seq: Sequence[T], *seed_parts: Any) -> T:
    """从序列里确定性地挑一个元素。"""
    if not seq:
        raise ValueError("空序列无法挑选")
    idx = int(stable_unit(*seed_parts) * len(seq))
    return seq[min(idx, len(seq) - 1)]


# ---------------------------------------------------------------------------
# 1. Idea Miner —— 题材爆点挖掘
# ---------------------------------------------------------------------------


def idea_miner(brief: ScriptBrief) -> dict[str, Any]:
    """输出 concept_brief：题材归类 + 钩子图谱 + 市场角度。"""
    genre: GenreTemplate = get_genre(brief.genre) if brief.genre else match_genre(brief.theme)

    hook_map = [
        {"position": "first_3s", "hook": _pick(genre.hooks, brief.seed, "hook", 0)},
        {"position": "episode_end", "hook": _pick(genre.cliffhangers, brief.seed, "cliff", 0)},
    ]
    return {
        "schema_version": schemas.SCHEMA_CONCEPT_BRIEF,
        "theme": brief.theme,
        "genre": genre.key,
        "genre_label": genre.label,
        "logline": f"{brief.theme}——{_pick(genre.conflicts, brief.seed, 'logline')}",
        "hook_map": hook_map,
        "market_angle": {
            "platform": brief.target_platform,
            "episode_count": brief.episode_count,
            "core_emotion": genre.locations[0].mood,
        },
        "auto_classified": brief.genre is None,
    }


# ---------------------------------------------------------------------------
# 2. Showrunner Brain —— 世界观与人物关系
# ---------------------------------------------------------------------------


def showrunner(concept: dict[str, Any], brief: ScriptBrief) -> dict[str, Any]:
    """输出 show_bible：直接喂给 IdentityDNA 的人物设定 + 关系图。"""
    genre = get_genre(concept["genre"])
    # 场景数越多，出场人物越多；但至少主角 + 对手
    role_count = 2 if brief.scenes_per_episode <= 3 else min(3, len(genre.roles))
    # 按结构必要性排序取人：主角 → 对手 → 配角。
    # 对手是硬需求（冲突场与悬念场都要有人对戏）；配角只在发展场出现，
    # 三场戏的短剧根本没有发展场，先取配角会让他一场戏都没有。
    _priority = {"主角": 0, "对手": 1}
    ordered = sorted(
        enumerate(genre.roles),
        key=lambda pair: (_priority.get(pair[1].role_type, 2), pair[0]),
    )
    roles = [r for _, r in ordered[:role_count]]

    characters = [
        {
            "character_id": f"char_{r.key}",
            "role_key": r.key,
            "name": r.name,
            "age_range": r.age_range,
            "gender": r.gender,
            "ethnicity_preference": "东亚/中国",
            "personality_keywords": list(r.personality_keywords),
            "role_type": r.role_type,
            "need_age_line": r.need_age_line,
            "need_family": r.need_family,
        }
        for r in roles
    ]
    protagonist = characters[0]["character_id"]
    relationships = [
        {"from": protagonist, "to": c["character_id"],
         "relation": "对立" if c["role_type"] == "对手" else "牵绊"}
        for c in characters[1:]
    ]
    return {
        "schema_version": schemas.SCHEMA_SHOW_BIBLE,
        "genre": genre.key,
        "title": f"{concept['genre_label']}·{brief.theme[:12].rstrip('，,。.、 ')}",
        "characters": characters,
        "protagonist_id": protagonist,
        "relationship_map": relationships,
        "world_rules": [
            "现实世界，无超自然设定",
            "所有冲突必须能在有限场景内完成",
        ],
    }


# ---------------------------------------------------------------------------
# 3. Plot Architect —— 分集结构与节拍
# ---------------------------------------------------------------------------


def plan_beats(scenes_per_episode: int) -> list[str]:
    """三段式节拍表：开头钩子 + 中段冲突（+ 若干发展）+ 结尾悬念。"""
    middle = [BEAT_DEVELOPMENT] * (scenes_per_episode - 3)
    return [BEAT_HOOK, BEAT_CONFLICT, *middle, BEAT_CLIFFHANGER]


def plot_architect(
    show_bible: dict[str, Any], concept: dict[str, Any], brief: ScriptBrief
) -> dict[str, Any]:
    """输出 episode_outline：每集的节拍表、场地与出场人物分配。"""
    genre = get_genre(concept["genre"])
    beat_types = plan_beats(brief.scenes_per_episode)
    char_ids = [c["character_id"] for c in show_bible["characters"]]
    protagonist = show_bible["protagonist_id"]
    antagonist = next(
        (c["character_id"] for c in show_bible["characters"] if c["role_type"] == "对手"),
        None,
    )
    supporting = [c for c in char_ids if c not in (protagonist, antagonist)]

    episodes = []
    for ep_i in range(brief.episode_count):
        ep_id = f"EP{ep_i + 1:03d}"
        beats = []
        for b_i, beat_type in enumerate(beat_types):
            loc = genre.locations[(ep_i + b_i) % len(genre.locations)]
            summary_pool = {
                BEAT_HOOK: genre.hooks,
                BEAT_CONFLICT: genre.conflicts,
                BEAT_DEVELOPMENT: genre.developments,
                BEAT_CLIFFHANGER: genre.cliffhangers,
            }[beat_type]

            cast = [protagonist]
            if beat_type in (BEAT_CONFLICT, BEAT_CLIFFHANGER) and antagonist:
                cast.append(antagonist)
            elif beat_type == BEAT_DEVELOPMENT and supporting:
                cast.append(_pick(supporting, brief.seed, ep_id, b_i, "cast"))

            # 主道具贯穿钩子与悬念（连续性来源），次道具进冲突
            prop_keys: list[str] = []
            if genre.props:
                if beat_type in (BEAT_HOOK, BEAT_CLIFFHANGER):
                    prop_keys.append(genre.props[0].key)
                elif len(genre.props) > 1:
                    prop_keys.append(genre.props[1].key)
                else:
                    prop_keys.append(genre.props[0].key)

            beats.append(
                {
                    "beat_id": f"{ep_id}_B{b_i + 1:02d}",
                    "beat_type": beat_type,
                    "summary": _pick(summary_pool, brief.seed, ep_id, b_i, beat_type),
                    "location_key": loc.key,
                    "character_ids": cast,
                    "prop_keys": prop_keys,
                }
            )
        episodes.append(
            {
                "episode_id": ep_id,
                "title": f"第{ep_i + 1}集",
                "beats": beats,
                "turning_point": beats[1]["summary"],
                "cliffhanger": beats[-1]["summary"],
            }
        )

    return {
        "schema_version": schemas.SCHEMA_EPISODE_OUTLINE,
        "genre": genre.key,
        "episodes": episodes,
    }


# ---------------------------------------------------------------------------
# 4. Episode Writer —— 分集初稿（结构化场景列表）
# ---------------------------------------------------------------------------


def episode_writer(
    outline: dict[str, Any], show_bible: dict[str, Any], brief: ScriptBrief
) -> list[dict[str, Any]]:
    """把节拍表展开成结构化场景列表（尚无台词）。"""
    genre = get_genre(outline["genre"])
    loc_by_key = {loc.key: loc for loc in genre.locations}
    prop_by_key = {p.key: p for p in genre.props}
    name_by_id = {c["character_id"]: c["name"] for c in show_bible["characters"]}

    episodes: list[dict[str, Any]] = []
    for ep in outline["episodes"]:
        scenes = []
        for s_i, beat in enumerate(ep["beats"]):
            loc = loc_by_key[beat["location_key"]]
            scene_id = f"{ep['episode_id']}_SC{s_i + 1:03d}"

            # 道具状态：按节拍推进（完整 → 被破坏 → 被安置），跨场景连续
            prop_states: dict[str, str] = {}
            for pk in beat["prop_keys"]:
                spec = prop_by_key[pk]
                idx = min(s_i, len(spec.states) - 1)
                prop_states[f"prop_{pk}"] = spec.states[idx]

            actors = "、".join(name_by_id[c] for c in beat["character_ids"])
            scenes.append(
                {
                    "scene_id": scene_id,
                    "episode_id": ep["episode_id"],
                    "beat_type": beat["beat_type"],
                    "beat_summary": beat["summary"],
                    "location_key": loc.key,
                    "location_description": loc.name,
                    "time_of_day": loc.time_of_day,
                    "mood": loc.mood,
                    "camera_intent": loc.camera_intent,
                    "required_atoms": list(loc.required_atoms),
                    "spatial_needs": loc.spatial_needs,
                    "production_difficulty": loc.difficulty,
                    "characters": list(beat["character_ids"]),
                    "props": [f"prop_{k}" for k in beat["prop_keys"]],
                    "prop_states": prop_states,
                    "action": f"{loc.name}，{loc.time_of_day}。{actors}：{beat['summary']}。",
                    "dialogue": [],
                    "estimated_duration_sec": _SCENE_SECONDS[beat["beat_type"]],
                }
            )
        episodes.append({**{k: v for k, v in ep.items() if k != "beats"},
                         "beats": ep["beats"], "scenes": scenes})
    return episodes


# ---------------------------------------------------------------------------
# 5. Dialogue Master —— 台词润色（短、狠、有情绪）
# ---------------------------------------------------------------------------


def dialogue_master(
    episodes: list[dict[str, Any]], outline: dict[str, Any], brief: ScriptBrief
) -> list[dict[str, Any]]:
    """给每场戏配台词。原地返回新结构，不修改入参。"""
    genre = get_genre(outline["genre"])
    bank = genre.dialogue_bank or ("……",)

    out = []
    for ep in episodes:
        scenes = []
        for scene in ep["scenes"]:
            lines = [
                {
                    "character_id": cid,
                    "line": _pick(bank, brief.seed, scene["scene_id"], i),
                }
                for i, cid in enumerate(scene["characters"])
            ]
            scenes.append({**scene, "dialogue": lines})
        out.append({**ep, "scenes": scenes})
    return out


# ---------------------------------------------------------------------------
# 6. Script Critic —— 逻辑 / 连续性 / 拍摄可行性
# ---------------------------------------------------------------------------


def script_critic(script: dict[str, Any]) -> dict[str, Any]:
    """审查剧本本身是否可被下游消费。对任意剧本 dict 都能跑（含外来剧本）。"""
    logic_issues: list[str] = []
    continuity_issues: list[str] = []
    difficulty_issues: list[str] = []

    declared_chars = {c["character_id"] for c in script.get("characters") or []}
    declared_props = {p["prop_id"] for p in script.get("props") or []}
    prop_states_allowed = {
        p["prop_id"]: set(p.get("states_needed") or [])
        for p in script.get("props") or []
    }
    seen_chars: set[str] = set()

    for ep in script.get("episodes") or []:
        ep_id = ep.get("episode_id", "?")
        scenes = ep.get("scenes") or []
        if not scenes:
            logic_issues.append(f"{ep_id} 没有任何场景")
            continue

        beats = {s.get("beat_type") for s in scenes}
        missing = REQUIRED_BEATS - beats
        if missing:
            logic_issues.append(
                f"{ep_id} 缺少必需节拍 {sorted(missing)}（每集必须有钩子/冲突/悬念）"
            )
        if scenes[0].get("beat_type") != BEAT_HOOK:
            logic_issues.append(f"{ep_id} 开场不是钩子场")
        if scenes[-1].get("beat_type") != BEAT_CLIFFHANGER:
            logic_issues.append(f"{ep_id} 结尾不是悬念场")

        for s in scenes:
            sid = s.get("scene_id", "?")
            if not s.get("required_atoms"):
                logic_issues.append(f"{sid} 没有声明 required_atoms，SceneDNA 无法接单")
            for cid in s.get("characters") or []:
                seen_chars.add(cid)
                if cid not in declared_chars:
                    continuity_issues.append(f"{sid} 引用了未声明的人物 {cid}")
            for pid in s.get("props") or []:
                if pid not in declared_props:
                    continuity_issues.append(f"{sid} 引用了未声明的道具 {pid}")
            for pid, state in (s.get("prop_states") or {}).items():
                allowed = prop_states_allowed.get(pid)
                if allowed is not None and state not in allowed:
                    continuity_issues.append(
                        f"{sid} 使用了道具 {pid} 的未登记状态「{state}」"
                    )
            if s.get("production_difficulty") == "hard":
                difficulty_issues.append(
                    f"{sid}（{s.get('location_description')}）当前生成能力难以完成"
                )

    for cid in sorted(declared_chars - seen_chars):
        continuity_issues.append(f"人物 {cid} 一场戏都没有，不应进入 IdentityDNA 生成")

    issues = logic_issues + continuity_issues + difficulty_issues
    return {
        "schema_version": schemas.SCHEMA_SCRIPT_REVIEW,
        "script_id": script.get("script_id"),
        "passed": not issues,
        "issues": issues,
        "logic_issues": logic_issues,
        "continuity_issues": continuity_issues,
        "production_difficulty": {
            "level": "hard" if difficulty_issues else "normal",
            "hard_scenes": difficulty_issues,
        },
    }


# ---------------------------------------------------------------------------
# 7. Market Hook Critic —— 留存与钩子强度
# ---------------------------------------------------------------------------


def market_hook_critic(script: dict[str, Any]) -> dict[str, Any]:
    """前 3 秒钩子、结尾悬念、单集时长的爆款留存审查（mock 打分）。"""
    seed = script.get("script_id", "unknown")
    episodes = script.get("episodes") or []
    issues: list[str] = []

    hook_ok = all(
        (ep.get("scenes") or [{}])[0].get("beat_type") == BEAT_HOOK for ep in episodes
    )
    cliff_ok = all(
        (ep.get("scenes") or [{}])[-1].get("beat_type") == BEAT_CLIFFHANGER
        for ep in episodes
    )
    if not hook_ok:
        issues.append("存在没有前 3 秒钩子的集数")
    if not cliff_ok:
        issues.append("存在没有结尾悬念的集数")

    # 单集时长：短剧 60~120 秒为宜
    for ep in episodes:
        total = sum(s.get("estimated_duration_sec", 0) for s in ep.get("scenes") or [])
        if total > 180:
            issues.append(f"{ep.get('episode_id')} 预计时长 {total}s 偏长，留存有风险")

    hook_strength = stable_score(0.70, 0.92, seed, "hook") if hook_ok else 0.30
    cliff_strength = stable_score(0.68, 0.90, seed, "cliff") if cliff_ok else 0.30
    retention = round(0.5 * hook_strength + 0.5 * cliff_strength, 4)

    return {
        "schema_version": schemas.SCHEMA_MARKET_REVIEW,
        "script_id": script.get("script_id"),
        "retention_score": retention,
        "hook_strength": hook_strength,
        "cliffhanger_strength": cliff_strength,
        "platform_adaptation": script.get("target_platform", "shortdrama"),
        "passed": retention >= MIN_RETENTION_SCORE and not issues,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# 工具：稳定 script_id
# ---------------------------------------------------------------------------


def make_script_id(brief: ScriptBrief) -> str:
    """由 brief 派生稳定的 script_id（ASCII，可作文件名）。"""
    return f"script_{sha256_hex(brief.seed)[:12]}"
