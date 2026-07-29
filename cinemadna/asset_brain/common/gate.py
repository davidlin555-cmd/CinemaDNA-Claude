"""CinemaDNA / DramaOS-X Factory Core — Gate Report 基类 (Phase 1)

对应接口文档 §2.3 / §3.3 / §4.3 三份 Gate Report 结构。
三者骨架一致（gate_id / workorder_id / passed / scores / issues /
human_review_required / decided_at / decided_by），只有 scores 键集不同，
且 IdentityDNA 额外携带 hard_blocks（硬性拦截项）。

铁律（主规格 §八）：
- 未过 Gate 的资产禁止进入渲染，也禁止回流。
- IdentityDNA 的 hard_blocks 一旦非空，**任何人工审核都不得推翻**。
  该保证由 IdentityGateReport.has_hard_block + facade.approve_gate 共同实施。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import schemas


@dataclass
class GateReport:
    """通用 Gate Report 基类。"""

    schema_version: str
    gate_id: str
    workorder_id: str
    passed: bool
    scores: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    human_review_required: bool = True
    decided_at: str | None = None
    decided_by: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version not in schemas.KNOWN_SCHEMA_VERSIONS:
            raise schemas.SchemaValidationError(
                f"未登记的 Gate schema_version: {self.schema_version!r}"
            )
        if not self.gate_id or not isinstance(self.gate_id, str):
            raise schemas.SchemaValidationError(f"gate_id 不能为空: {self.gate_id!r}")
        if not self.workorder_id or not isinstance(self.workorder_id, str):
            raise schemas.SchemaValidationError(
                f"gate_report.workorder_id 不能为空: {self.workorder_id!r}"
            )

    # -- 硬拦截语义（基类无硬拦截，由 IdentityGateReport 覆写）----------------

    @property
    def has_hard_block(self) -> bool:
        return False

    # -- 审核决策 -----------------------------------------------------------

    def decide(self, decided_by: str) -> "GateReport":
        """登记决策人与决策时间（人工或 auto_gate）。"""
        self.decided_by = decided_by
        self.decided_at = schemas.utc_now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "gate_id": self.gate_id,
            "workorder_id": self.workorder_id,
            "passed": self.passed,
            "scores": dict(self.scores),
            "issues": list(self.issues),
            "human_review_required": self.human_review_required,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
        }
