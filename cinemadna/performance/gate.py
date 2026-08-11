"""PerformanceDNA · Performance Quality Gate (Phase 1)

Checks if the generated micro-expressions and actions align with the character and script intent.
"""

from __future__ import annotations

from typing import Any, Final

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.gate import GateReport

MIN_INTENT_MATCH: Final = 0.70
MIN_EXPRESSION_NATURALNESS: Final = 0.65

def run_performance_gate(
    *,
    gate_id: str,
    workorder_id: str,
    performance_asset: dict[str, Any],
) -> GateReport:
    """Executes the PerformanceDNA Gate, returning a GateReport."""
    issues: list[str] = []

    intent_match = round(float(performance_asset.get("intent_match", 0.0)), 4)
    expression_naturalness = round(float(performance_asset.get("expression_naturalness", 0.0)), 4)

    if intent_match < MIN_INTENT_MATCH:
        issues.append(f"Intent match is too low: {intent_match} < {MIN_INTENT_MATCH}")

    if expression_naturalness < MIN_EXPRESSION_NATURALNESS:
        issues.append(f"Expression naturalness is too low: {expression_naturalness} < {MIN_EXPRESSION_NATURALNESS}")

    if not performance_asset.get("beats"):
        issues.append("Performance asset is missing acting beats.")

    passed = not issues

    return GateReport(
        schema_version=schemas.SCHEMA_PERFORMANCE_QA,
        gate_id=gate_id,
        workorder_id=workorder_id,
        passed=passed,
        scores={
            "intent_match": intent_match,
            "expression_naturalness": expression_naturalness,
        },
        issues=issues,
        human_review_required=not passed,
    )
