"""ScriptBrain · Script Rights & Quality Gate (Phase 1)

Ensures that the output of ScriptBrainMicroDismantler is valid, continuous, and conforms to the expected format.
"""

from __future__ import annotations

from typing import Any

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.gate import GateReport

def run_script_gate(
    *,
    gate_id: str,
    workorder_id: str,
    script_output: dict[str, Any],
) -> GateReport:
    """Executes the ScriptBrain Gate, returning a GateReport."""
    issues: list[str] = []

    # Format checking
    if not isinstance(script_output.get("shots"), list):
        issues.append("Missing or invalid 'shots' array in script output.")
    elif len(script_output["shots"]) == 0:
        issues.append("The 'shots' array is empty.")
    else:
        for i, shot in enumerate(script_output["shots"]):
            if not shot.get("shot_id"):
                issues.append(f"Shot at index {i} is missing 'shot_id'.")
            if not shot.get("scene"):
                issues.append(f"Shot at index {i} is missing 'scene' definition.")
            if not shot.get("action"):
                issues.append(f"Shot at index {i} is missing 'action' definition.")

    passed = not issues

    return GateReport(
        schema_version=schemas.SCHEMA_SCRIPT_REVIEW,
        gate_id=gate_id,
        workorder_id=workorder_id,
        passed=passed,
        scores={},
        issues=issues,
        human_review_required=not passed,
    )
