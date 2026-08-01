"""Shot Production Contract V5 (Phase C) —— 每镜头完整生产合同

对应 PDF §3。现有 Shot Contract 已有大部分数据（场景/人物/道具/机位/情绪/
表演/台词/口型），V5 在此之上补齐 PDF 要求但之前缺的命名子合同：

- story_function：镜头的剧情功能（为什么存在、推动什么）
- voice_contract：谁说话、声音性别/年龄（VoiceDNA 绑定）
- route_contract：本地/云端/Kling 路由决策（CostRouter/ProviderRouter）
- qa_contract：本镜头的机器可判通过标准

这些字段在 DirectorDNA 建合约时（DRAFT 态）写入，随签发一起进 contract_hash，
因此不可篡改。`contract_v5_view()` 给 WebUI/审计一个按 PDF 结构组织的只读投影。
"""

from __future__ import annotations

from typing import Any

from director.shot_contract import (
    MOUTH_OFFSCREEN_VO,
    MOUTH_PHONE_SCREEN,
    SHOT_CLOSEUP,
    SHOT_REACTION,
)
from render.router import route_shot

#: 性别 → 声音性别
_VOICE_GENDER = {"female": "female", "male": "male"}


def _voice_contract(contract: dict[str, Any],
                    characters_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """由台词说话人 + 人物设定推导声音绑定（VoiceDNA）。"""
    lines = contract.get("dialogue") or []
    if not lines:
        return {"has_dialogue": False, "bindings": []}
    bindings = []
    for line in lines:
        cid = line["character_id"]
        ch = characters_by_id.get(cid, {})
        gender = ch.get("gender", "")
        bindings.append({
            "speaker": cid,
            "voice_gender": _VOICE_GENDER.get(gender, "unspecified"),
            "age_feel": ch.get("age_range", ""),
            "line": line.get("line", ""),
            "mode": contract.get("mouth_policy"),
        })
    return {"has_dialogue": True, "bindings": bindings}


def _story_function(contract: dict[str, Any]) -> str:
    existing = contract.get("story_function")
    if existing:
        return existing          # 导演已给每镜具体功能，保留（叙事层）
    beat = contract["emotion"].get("beat", "")
    return f"[{beat}] {contract.get('notes') or '推动剧情'}"


def _qa_contract(contract: dict[str, Any]) -> list[str]:
    """本镜头机器可判的通过标准。"""
    checks = ["角色人脸一致", "无角色漂移"]
    if contract["assets"].get("props"):
        checks.append("道具清晰可读、无乱码")
    if contract.get("dialogue"):
        checks.append("对白由正确角色说出、声音性别匹配")
    checks.append("口型策略被遵守")
    if contract["shot_type"] in (SHOT_CLOSEUP, SHOT_REACTION):
        checks.append("特写/反应镜有微表情")
    return checks


def enrich_contract_v5(
    contract: dict[str, Any], *, characters_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """给 DRAFT 合约补齐 V5 子合同。就地修改并返回。"""
    contract["story_function"] = _story_function(contract)
    contract["voice_contract"] = _voice_contract(contract, characters_by_id)
    d = route_shot(contract)
    contract["route_contract"] = {
        "model": d["model"], "tier": d["tier"], "reason": d["reason"]}
    contract["qa_contract"] = _qa_contract(contract)
    return contract


def contract_v5_view(contract: dict[str, Any]) -> dict[str, Any]:
    """按 PDF §3 结构组织的只读投影（给 WebUI / 审计）。"""
    a = contract.get("assets") or {}
    cam = contract.get("camera") or {}
    return {
        "shot_id": contract["shot_id"],
        "story_function": contract.get("story_function"),
        "scene_contract": {"scene_id": contract.get("scene_id"),
                           "asset_id": (a.get("scene") or {}).get("asset_id"),
                           "scene_image": (a.get("scene") or {}).get("scene_image"),
                           "is_real": bool((a.get("scene") or {}).get("is_real_image"))},
        "identity_contract": {
            "characters": [c["character_id"] for c in a.get("characters") or []],
            "face_images": [c.get("base_face_image") for c in a.get("characters") or []],
            "is_real": [bool(c.get("is_real_image")) for c in a.get("characters") or []]},
        "prop_contract": {"props": [p["prop_id"] for p in a.get("props") or []],
                          "states": {p["prop_id"]: p.get("state") for p in a.get("props") or []},
                          "prop_images": {p["prop_id"]: p.get("prop_image") for p in a.get("props") or []},
                          "is_real": {p["prop_id"]: bool(p.get("is_real_image")) for p in a.get("props") or []}},
        "performance_contract": {"beats": len((contract.get("performance") or {}).get("acting_beats") or [])},
        "dialogue_contract": contract.get("dialogue") or [],
        "voice_contract": contract.get("voice_contract"),
        "mouth_policy": contract.get("mouth_policy"),
        "camera_contract": {"shot_size": cam.get("shot_size"),
                            "movement": cam.get("movement"), "intent": cam.get("intent")},
        "route_contract": contract.get("route_contract"),
        "qa_contract": contract.get("qa_contract"),
    }
