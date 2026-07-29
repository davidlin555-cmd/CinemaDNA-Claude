"""SceneDNA · Scene Rights & Quality Gate (Phase 1)

对应接口文档 §2.3。四项打分：
- naturalness       自然度（是否像真实拍到的空间，而非 AI 味贴图）
- script_match      与剧本场景需求的匹配度
- layout_usability  布局可用性（能否支持人物走动、特写等拍摄意图）
- rights_risk       版权风险（越低越好）

另有两条会直接判失败的结构性检查：
1. **版权来源**：来源不在授权白名单内 → rights_risk 拉满并判失败。
2. **自然场景原子优先原则**：requirement.must_be_natural 为真时，
   若资产退化为纯 AI 生图（source_kind = pure_ai_generated），判失败。
   这是交接文档"绝对不能踩的坑 #2"的机器化执行点——否则核心差异化会
   在工程压力下被悄悄稀释。
"""

from __future__ import annotations

from typing import Any, Final

from ..common import schemas
from ..common.gate import GateReport

MIN_NATURALNESS: Final = 0.70
MIN_SCRIPT_MATCH: Final = 0.65
MIN_LAYOUT_USABILITY: Final = 0.60
MAX_RIGHTS_RISK: Final = 0.30
#: 低于此风险值无需人工复核
AUTO_PASS_RIGHTS_RISK: Final = 0.15

#: 允许的场景素材来源
ALLOWED_SOURCES: Final[frozenset[str]] = frozenset(
    {"internal_db", "authorized_public", "licensed_stock", "synthetic"}
)

#: 违反"自然原子优先"的来源类型
PURE_AI_SOURCE_KIND: Final = "pure_ai_generated"


def run_scene_gate(
    *,
    gate_id: str,
    workorder_id: str,
    generated_asset: dict[str, Any],
    requirement: dict[str, Any],
) -> GateReport:
    """执行 SceneDNA Gate，返回通用 GateReport（scene_gate.v1）。"""
    issues: list[str] = []

    naturalness = round(float(generated_asset.get("naturalness", 0.0)), 4)
    script_match = round(float(generated_asset.get("script_match", 0.0)), 4)
    layout_usability = round(float(generated_asset.get("layout_usability", 0.0)), 4)

    # --- 版权来源 -----------------------------------------------------------
    sources_used = list(generated_asset.get("sources_used") or [])
    illegal_sources = [s for s in sources_used if s not in ALLOWED_SOURCES]
    if illegal_sources:
        rights_risk = 0.95
        issues.append(f"存在未授权素材来源: {illegal_sources}")
    else:
        rights_risk = round(float(generated_asset.get("rights_risk", 0.05)), 4)

    # --- 自然原子优先原则 ---------------------------------------------------
    if requirement.get("must_be_natural") and (
        generated_asset.get("source_kind") == PURE_AI_SOURCE_KIND
    ):
        issues.append(
            "违反自然场景原子优先原则：要求自然场景却退化为纯 AI 生图"
        )

    # --- 原子覆盖度 ---------------------------------------------------------
    required_atoms = list(requirement.get("required_atoms") or [])
    produced = {a.get("atom_type") for a in generated_asset.get("atoms") or []}
    missing = [a for a in required_atoms if a not in produced]
    if missing:
        issues.append(f"缺少剧本要求的场景原子: {missing}")

    # --- 分数阈值 -----------------------------------------------------------
    if naturalness < MIN_NATURALNESS:
        issues.append(f"自然度不足: {naturalness} < {MIN_NATURALNESS}")
    if script_match < MIN_SCRIPT_MATCH:
        issues.append(f"剧本匹配度不足: {script_match} < {MIN_SCRIPT_MATCH}")
    if layout_usability < MIN_LAYOUT_USABILITY:
        issues.append(f"布局可用性不足: {layout_usability} < {MIN_LAYOUT_USABILITY}")
    if rights_risk > MAX_RIGHTS_RISK:
        issues.append(f"版权风险过高: {rights_risk} > {MAX_RIGHTS_RISK}")

    passed = not issues

    return GateReport(
        schema_version=schemas.SCHEMA_SCENE_GATE,
        gate_id=gate_id,
        workorder_id=workorder_id,
        passed=passed,
        scores={
            "naturalness": naturalness,
            "script_match": script_match,
            "layout_usability": layout_usability,
            "rights_risk": rights_risk,
        },
        issues=issues,
        human_review_required=(not passed) or rights_risk > AUTO_PASS_RIGHTS_RISK,
    )
