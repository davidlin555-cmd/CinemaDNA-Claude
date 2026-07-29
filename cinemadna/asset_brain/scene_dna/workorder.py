"""SceneDNA · Workorder 扩展字段构造 (Phase 1)

对应接口文档 §2.2：SceneDNA 在通用 Workorder 之上，只扩展
requirement 与 generation_plan 两个字典。把它们的构造逻辑单独放在这里，
使 service.py 专注流程编排。
"""

from __future__ import annotations

from typing import Any

#: 剧本没写明所需原子时的兜底集合（拍摄可用空间的最小构件）
DEFAULT_REQUIRED_ATOMS = ["室内布局", "光线", "家具陈设", "窗户"]

#: mood 字段的常见分隔符
_MOOD_SEPARATORS = ("、", ",", "，", "/", " ")


def split_mood(mood: str) -> list[str]:
    """把 "压抑、疲惫" 这类情绪串拆成标签列表。"""
    if not mood:
        return []
    tokens = [mood]
    for sep in _MOOD_SEPARATORS:
        tokens = [t for token in tokens for t in token.split(sep)]
    return [t.strip() for t in tokens if t.strip()]


def build_requirement(shooting_script_scene: dict[str, Any]) -> dict[str, Any]:
    """从 shooting_script 的单个场景提取标准化场景需求。"""
    s = shooting_script_scene
    return {
        "scene_id": s.get("scene_id"),
        "location_description": s.get("location_description") or s.get("location", ""),
        "time_of_day": s.get("time_of_day", ""),
        "mood": s.get("mood", ""),
        "camera_intent": s.get("camera_intent", ""),
        "required_atoms": list(s.get("required_atoms") or DEFAULT_REQUIRED_ATOMS),
        "spatial_needs": s.get("spatial_needs", "可支持人物走动与特写"),
        # 自然场景原子优先是产品核心差异化，恒为 True
        "must_be_natural": True,
    }


def build_generation_plan(requirement: dict[str, Any]) -> dict[str, Any]:
    """构造 SceneDNA generation_plan（Phase 1 固定 mock、暂不做 3D）。"""
    return {
        "search_sources": ["internal_db", "authorized_public"],
        "atom_extraction_needed": True,
        "layout_recompose": True,
        "3d_support": False,
        "mock_mode": True,
        # 默认走"自然原子重组"路线；若被下发参数改成 pure_ai_generated，
        # Gate 会按"自然场景原子优先"原则判失败（这正是我们要能验证的行为）
        "source_kind": "natural_atom_recompose",
    }


def scene_prompt(requirement: dict[str, Any]) -> str:
    """由场景需求拼出文生图提示词（竖屏短剧空镜，无人物）。

    刻意做成**空场景/环境镜**：只出空间/光线/陈设，不放人物（人物由 image2video
    的人脸参考驱动）。全英文关键词，出图更稳。
    """
    loc = requirement.get("location_description", "")
    tod = requirement.get("time_of_day", "")
    mood = requirement.get("mood", "")
    atoms = "、".join(requirement.get("required_atoms") or [])
    return (
        f"cinematic empty interior establishing shot, no people, "
        f"location: {loc}, time: {tod}, mood: {mood}, "
        f"elements: {atoms}, vertical 9:16 composition, realistic lighting, "
        f"film still, photorealistic, high detail, natural colors, SFW"
    )


def derive_tags(requirement: dict[str, Any]) -> list[str]:
    """从需求派生检索/回流用标签，例如 ["出租屋", "深夜", "压抑", "疲惫"]。"""
    tags: list[str] = []
    for key in ("location_description", "time_of_day"):
        v = str(requirement.get(key) or "").strip()
        if v:
            tags.append(v)
    tags.extend(split_mood(str(requirement.get("mood") or "")))
    # 去重并保持顺序
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
