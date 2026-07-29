"""Render Brain · Model Router + Cost Optimizer (Phase 3)

对应多智能体文档 §2.8：按镜头类型动态路由最优模型，本地优先、云端补强。

Phase 3 的模型表是**占位登记**，不代表真实接入。真实接入时改这张表 + 写一个
`RenderBackend` 实现即可，路由规则本身不用动。

路由原则（有明确取舍，不是随机）：
- 人脸特写 / 反应镜 → 一致性要求最高，走云端强模型，**不允许为省钱降级**
- 建立镜 / 中景     → 本地优先，本地扛不住（高难度）才上云
- 道具插入镜        → 本地足够
"""

from __future__ import annotations

from typing import Any, Final

from asset_brain.common import schemas
from director.shot_contract import (
    SHOT_CLOSEUP,
    SHOT_ESTABLISHING,
    SHOT_INSERT,
    SHOT_MEDIUM,
    SHOT_REACTION,
)

#: 模型登记表（Phase 3 占位，非真实接入）
MODEL_REGISTRY: Final[dict[str, dict[str, Any]]] = {
    "cloud-face-v1": {
        "tier": "cloud",
        "good_at": (SHOT_CLOSEUP, SHOT_REACTION),
        "cost_per_sec": 0.35,
        "max_duration_sec": 10.0,
        "note": "人脸一致性最好，用于特写与反应镜",
    },
    "cloud-motion-v1": {
        "tier": "cloud",
        "good_at": (SHOT_MEDIUM, SHOT_ESTABLISHING),
        "cost_per_sec": 0.22,
        "max_duration_sec": 12.0,
        "note": "连续动作与空间感，用于高难度中景/建立镜",
    },
    "local-video-v1": {
        "tier": "local",
        "good_at": (SHOT_ESTABLISHING, SHOT_MEDIUM, SHOT_INSERT),
        "cost_per_sec": 0.02,
        "max_duration_sec": 12.0,
        "note": "本地推理，成本极低，用于非人脸镜头",
    },
}

#: 一致性要求最高、禁止为省钱降级的镜头类型
FACE_CRITICAL_TYPES: Final[frozenset[str]] = frozenset({SHOT_CLOSEUP, SHOT_REACTION})

#: 可被成本优化器降级到本地的镜头类型
DOWNGRADABLE_TYPES: Final[frozenset[str]] = frozenset(
    {SHOT_ESTABLISHING, SHOT_MEDIUM, SHOT_INSERT}
)

#: 云端镜头占比上限（Cost Governor 的 Phase 3 简化版）
DEFAULT_MAX_CLOUD_RATIO: Final = 0.5


class RoutingError(RuntimeError):
    """无法为该镜头找到可用模型。"""


def route_shot(
    contract: dict[str, Any], *, local_first: bool = True
) -> dict[str, Any]:
    """为单个镜头选模型，返回 routing_decision。"""
    stype = contract["shot_type"]
    duration = float(contract["duration_sec"])
    hard = contract.get("difficulty") == "hard"

    if stype in FACE_CRITICAL_TYPES:
        model, reason = "cloud-face-v1", "人脸特写/反应镜，一致性优先，强制云端"
    elif hard:
        model, reason = "cloud-motion-v1", "高难度场景，本地扛不住，上云补强"
    elif stype == SHOT_INSERT or local_first:
        model, reason = "local-video-v1", "非人脸镜头，本地优先以控成本"
    else:
        model, reason = "cloud-motion-v1", "默认云端"

    spec = MODEL_REGISTRY[model]
    if duration > spec["max_duration_sec"]:
        raise RoutingError(
            f"{contract['shot_id']}: 时长 {duration}s 超过 {model} 上限 "
            f"{spec['max_duration_sec']}s"
        )

    return {
        "schema_version": schemas.SCHEMA_ROUTING_DECISION,
        "shot_id": contract["shot_id"],
        "shot_type": stype,
        "model": model,
        "tier": spec["tier"],
        "reason": reason,
        "estimated_cost": round(duration * spec["cost_per_sec"], 4),
        "downgradable": stype in DOWNGRADABLE_TYPES,
    }


def optimize_cost(
    decisions: list[dict[str, Any]], *, max_cloud_ratio: float = DEFAULT_MAX_CLOUD_RATIO
) -> list[dict[str, Any]]:
    """成本优化：云端占比超标时，把**可降级**的镜头压回本地。

    人脸特写永远不降级 —— 省下的钱不值得角色漂移。
    因此实际云端占比可能仍高于上限，此时如实记录 `over_budget`。
    """
    total = len(decisions)
    if total == 0:
        return decisions

    cloud = [d for d in decisions if d["tier"] == "cloud"]
    allowed = int(total * max_cloud_ratio)
    excess = len(cloud) - allowed
    if excess <= 0:
        return decisions

    for d in decisions:
        if excess <= 0:
            break
        if d["tier"] == "cloud" and d["downgradable"]:
            spec = MODEL_REGISTRY["local-video-v1"]
            if float(spec["max_duration_sec"]) <= 0:
                continue
            d["model"] = "local-video-v1"
            d["tier"] = "local"
            d["reason"] += "｜成本优化：降级到本地"
            d["estimated_cost"] = round(
                d["estimated_cost"] / MODEL_REGISTRY["cloud-motion-v1"]["cost_per_sec"]
                * spec["cost_per_sec"],
                4,
            )
            excess -= 1
    return decisions


def cost_summary(
    decisions: list[dict[str, Any]], *, max_cloud_ratio: float = DEFAULT_MAX_CLOUD_RATIO
) -> dict[str, Any]:
    total = len(decisions) or 1
    cloud = sum(1 for d in decisions if d["tier"] == "cloud")
    return {
        "shots": len(decisions),
        "cloud_shots": cloud,
        "local_shots": len(decisions) - cloud,
        "cloud_ratio": round(cloud / total, 4),
        "max_cloud_ratio": max_cloud_ratio,
        "over_budget": (cloud / total) > max_cloud_ratio,
        "estimated_cost_total": round(sum(d["estimated_cost"] for d in decisions), 4),
        "by_model": {
            m: sum(1 for d in decisions if d["model"] == m) for m in MODEL_REGISTRY
        },
    }
