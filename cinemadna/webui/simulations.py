"""模拟场景 (Phase 5) —— 让审核/阻塞/红线三条路径在网页上可复现

默认流水线是"一路顺利"的：资产 Gate 全过、没有待人工复核的工单。
那样固然好，但**没法在浏览器里验证审核链路到底通不通**。

所以这里提供几个明确标注的模拟场景。它们只往 `generation_plan_override`
里写参数（这个机制本来就是为"接真实生成器"和"模拟违规场景验证 Gate"而设计的），
**不触碰任何 requirement 里的红线策略**，也不改 Gate 的判定逻辑。

页面上必须显示成「模拟」，不能让人误以为是真实生产结果。
"""

from __future__ import annotations

from typing import Any, Final

from cinemadna.asset_brain.identity_dna import fusion_mock

SIM_NONE: Final = "none"
SIM_SCENE_REVIEW: Final = "scene_rights_review"
SIM_SCENE_REJECT: Final = "scene_ai_degrade"
SIM_IDENTITY_CLONE: Final = "identity_clone"

SIMULATIONS: Final[dict[str, dict[str, Any]]] = {
    SIM_NONE: {
        "label": "无（正常生产）",
        "expect": "全部 Gate 自动通过，一路跑到成片",
    },
    SIM_SCENE_REVIEW: {
        "label": "模拟：场景版权风险需人工复核",
        "expect": "场景 Gate 通过但需人工确认 → 故事挂起 WAITING_HUMAN，"
                  "工单出现「待审」→ 可在审核页或工单页放行",
        "scene": {"rights_risk_override": 0.22},
    },
    SIM_SCENE_REJECT: {
        "label": "模拟：场景退化为纯 AI 生图（应被拒）",
        "expect": "违反「自然场景原子优先」→ Gate 判负 → 故事 BLOCKED，"
                  "可在审核页按修复计划打回",
        "scene": {"source_kind": "pure_ai_generated"},
    },
    SIM_IDENTITY_CLONE: {
        "label": "模拟：单一真人克隆（红线，应被硬拦截）",
        "expect": "IdentityDNA 硬性拦截 → 故事 BLOCKED，"
                  "网页上「通过」按钮被禁用，强行调接口也会 409",
        "identity": {"fusion_sources": fusion_mock.simulate_single_real_clone_sources()},
    },
}


def describe() -> list[dict[str, Any]]:
    """给页面下拉框用的选项清单。"""
    return [
        {"value": k, "label": v["label"], "expect": v["expect"]}
        for k, v in SIMULATIONS.items()
    ]


def apply_to_export(
    scene_export: dict[str, Any], simulate: str | None, *, story_id: str = ""
) -> dict[str, Any]:
    """把模拟参数注入 scene_export 的副本（不修改原对象）。

    身份类模拟会给角色一个**每故事唯一**的 id。原因：三大库跨剧共享，
    若上一部剧已经把同名角色回流进 Registry，这部剧的 identity 请求会命中
    复用而**根本不进入生成环节**，模拟的违规参数就永远不会被 Gate 看到。
    加故事前缀保证每次模拟都是一张全新的脸，必定触发生成、必定撞上红线。
    """
    if not simulate or simulate == SIM_NONE:
        return scene_export
    if simulate not in SIMULATIONS:
        raise ValueError(
            f"未知模拟场景 {simulate!r}，可选 {sorted(SIMULATIONS)}"
        )
    spec = SIMULATIONS[simulate]
    out = dict(scene_export)
    if spec.get("scene"):
        # 场景按标签（地点+时间+情绪）重合率复用。只改地点不够——时间和情绪
        # 标签仍会与上一部剧回流的同类场景重合过半而命中复用，跳过生成。
        # 所以三个标签源都加故事前缀，让标签完全唯一、必定触发新生成。
        tag = f"[模拟{story_id}]" if story_id else "[模拟]"
        out["scenes"] = [
            {
                **s,
                "location_description": f"{tag}{s.get('location_description', '')}",
                "time_of_day": f"{tag}{s.get('time_of_day', '')}",
                "mood": f"{tag}{s.get('mood', '')}",
                "generation_plan_override": dict(spec["scene"]),
            }
            for s in scene_export.get("scenes") or []
        ]
    if spec.get("identity"):
        suffix = f"_sim_{story_id}" if story_id else "_sim"
        out["characters"] = [
            {
                **c,
                "character_id": f"{c['character_id']}{suffix}",
                "generation_plan_override": dict(spec["identity"]),
            }
            for c in scene_export.get("characters") or []
        ]
    return out
