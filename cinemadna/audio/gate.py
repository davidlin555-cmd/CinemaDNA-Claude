"""AudioDNA/VocalDNA · Vocal Quality Gate (Phase 1)

Evaluates audio clarity, emotion match, and lip-sync markers.
"""

from __future__ import annotations

from typing import Any, Final

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.gate import GateReport

MIN_AUDIO_CLARITY: Final = 0.80
MIN_EMOTION_MATCH: Final = 0.70

def run_audio_gate(
    *,
    gate_id: str,
    workorder_id: str,
    audio_asset: dict[str, Any],
) -> GateReport:
    """Executes the Audio/Vocal Gate, returning a GateReport."""
    issues: list[str] = []

    audio_clarity = round(float(audio_asset.get("audio_clarity", 0.0)), 4)
    emotion_match = round(float(audio_asset.get("emotion_match", 0.0)), 4)

    if audio_clarity < MIN_AUDIO_CLARITY:
        issues.append(f"Audio clarity is too low: {audio_clarity} < {MIN_AUDIO_CLARITY}")

    if emotion_match < MIN_EMOTION_MATCH:
        issues.append(f"Emotion match is too low: {emotion_match} < {MIN_EMOTION_MATCH}")

    if audio_asset.get("has_dialogue") and not audio_asset.get("lip_sync_markers"):
        issues.append("Dialogue is present but lip sync markers are missing.")

    if bool(audio_asset.get("muffled_words", False)):
        issues.append("Detected muffled or swallowed words in the generated audio.")

    passed = not issues

    return GateReport(
        schema_version=schemas.SCHEMA_AUDIO_REVIEW,
        gate_id=gate_id,
        workorder_id=workorder_id,
        passed=passed,
        scores={
            "audio_clarity": audio_clarity,
            "emotion_match": emotion_match,
        },
        issues=issues,
        human_review_required=not passed,
    )
