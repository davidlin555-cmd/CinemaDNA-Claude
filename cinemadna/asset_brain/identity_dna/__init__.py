"""IdentityDNA 自然多人脸合成大模型 (Phase 1)。"""

from .gate import (
    HardBlockOverrideError,
    IdentityGateReport,
    detect_single_real_clone,
    run_identity_gate,
)
from .service import IdentityDNAService

__all__ = [
    "IdentityDNAService",
    "IdentityGateReport",
    "HardBlockOverrideError",
    "run_identity_gate",
    "detect_single_real_clone",
]
