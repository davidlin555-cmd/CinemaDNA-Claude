"""DirectorDNA + Shot Contract (Phase 3)。"""

from .continuity import build_ledger, check_continuity
from .service import DirectorDNAService, DirectorError, DirectorResult
from .shot_contract import (
    MAX_SHOT_DURATION_SEC,
    SHOT_TYPES,
    STATUS_DRAFT,
    STATUS_FAILED,
    STATUS_PERFORMANCE_READY,
    STATUS_RENDERED,
    RenderNotAllowedError,
    ShotContractError,
    bind_render_result,
    compute_contract_hash,
    new_shot_contract,
    require_render_ready,
    sign_contract,
    validate_contract,
)
from .strategy import plan_scene_shots, plan_shot_count

__all__ = [
    "DirectorDNAService",
    "DirectorResult",
    "DirectorError",
    "ShotContractError",
    "RenderNotAllowedError",
    "new_shot_contract",
    "sign_contract",
    "validate_contract",
    "require_render_ready",
    "bind_render_result",
    "compute_contract_hash",
    "build_ledger",
    "check_continuity",
    "plan_scene_shots",
    "plan_shot_count",
    "SHOT_TYPES",
    "STATUS_DRAFT",
    "STATUS_PERFORMANCE_READY",
    "STATUS_RENDERED",
    "STATUS_FAILED",
    "MAX_SHOT_DURATION_SEC",
]
