"""PropDNA · Prop Rights & Quality Gate (Phase 1)

对应接口文档 §4.3。四项打分：
- naturalness        自然度
- script_match       与剧本道具需求匹配度
- state_consistency  跨镜头状态连续性（本模型的核心价值）
- rights_risk        版权风险（真实品牌 logo 等）

结构性检查：
1. 剧本要求的每个状态都必须有对应资产版本；continuity_critical 为真时
   缺任何一个状态直接判失败（否则后期一定穿帮）。
2. 出现真实品牌标识 → 版权风险拉满。
"""

from __future__ import annotations

from typing import Any, Final

from ..common import schemas
from ..common.gate import GateReport

MIN_NATURALNESS: Final = 0.70
MIN_SCRIPT_MATCH: Final = 0.65
MIN_STATE_CONSISTENCY: Final = 0.80
MAX_RIGHTS_RISK: Final = 0.30

ALLOWED_SOURCES: Final[frozenset[str]] = frozenset(
    {"internal_db", "authorized_public", "licensed_stock", "synthetic"}
)


def run_prop_gate(
    *,
    gate_id: str,
    workorder_id: str,
    prop_asset: dict[str, Any],
    requirement: dict[str, Any],
) -> GateReport:
    """执行 PropDNA Gate，返回通用 GateReport（prop_gate.v1）。"""
    issues: list[str] = []

    naturalness = round(float(prop_asset.get("naturalness", 0.0)), 4)
    script_match = round(float(prop_asset.get("script_match", 0.0)), 4)

    # --- 状态覆盖度 ---------------------------------------------------------
    states_needed = list(requirement.get("states_needed") or [])
    produced = dict(prop_asset.get("asset_ids_by_state") or {})
    missing = [s for s in states_needed if s not in produced]
    if states_needed:
        state_consistency = round(
            (len(states_needed) - len(missing)) / len(states_needed), 4
        )
    else:
        state_consistency = 1.0

    if missing:
        issues.append(f"缺少剧本要求的道具状态版本: {missing}")
    if requirement.get("continuity_critical") and missing:
        issues.append("该道具为连续性关键道具，状态版本不得缺失")

    # --- 版权 ---------------------------------------------------------------
    sources_used = list(prop_asset.get("sources_used") or [])
    illegal_sources = [s for s in sources_used if s not in ALLOWED_SOURCES]
    if illegal_sources:
        rights_risk = 0.95
        issues.append(f"存在未授权素材来源: {illegal_sources}")
    else:
        rights_risk = round(float(prop_asset.get("rights_risk", 0.05)), 4)

    if prop_asset.get("real_brand_marks"):
        rights_risk = max(rights_risk, 0.90)
        issues.append(
            f"道具含真实品牌标识: {prop_asset.get('real_brand_marks')}"
        )

    # --- 分数阈值 -----------------------------------------------------------
    if naturalness < MIN_NATURALNESS:
        issues.append(f"自然度不足: {naturalness} < {MIN_NATURALNESS}")
    if script_match < MIN_SCRIPT_MATCH:
        issues.append(f"剧本匹配度不足: {script_match} < {MIN_SCRIPT_MATCH}")
    if state_consistency < MIN_STATE_CONSISTENCY:
        issues.append(
            f"状态连续性不足: {state_consistency} < {MIN_STATE_CONSISTENCY}"
        )
    if rights_risk > MAX_RIGHTS_RISK:
        issues.append(f"版权风险过高: {rights_risk} > {MAX_RIGHTS_RISK}")

    passed = not issues

    return GateReport(
        schema_version=schemas.SCHEMA_PROP_GATE,
        gate_id=gate_id,
        workorder_id=workorder_id,
        passed=passed,
        scores={
            "naturalness": naturalness,
            "script_match": script_match,
            "state_consistency": state_consistency,
            "rights_risk": rights_risk,
        },
        issues=issues,
        # 道具默认无需人工复核（接口文档 §4.3 默认 false），仅失败时需要人看
        human_review_required=not passed,
    )
