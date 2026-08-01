"""CinemaDNA / DramaOS-X Factory Core — 通用 Workorder 与状态机 (Phase 1)

对应"三大资产模型 Phase 1 接口文档 §1 通用 Workorder 数据结构"。
三大资产模型（SceneDNA / IdentityDNA / PropDNA）共用本基础结构，各自只在
requirement / generation_plan 里扩展字段。

状态机（接口文档 §0.2 / §1 状态流转规则）：

    PENDING → RUNNING → GATE_REVIEW → APPROVED  → BACKFLOWED
                                    ↘ REJECTED → (RUNNING 重生成)
        任意非终态 → FAILED（异常）

终态：BACKFLOWED、FAILED。
非法迁移一律抛 InvalidStateTransition，杜绝"跳过 Gate 直接回流"等违规。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from .quad import Quad
from . import schemas


class WorkorderStatus(str, Enum):
    """通用 Workorder 状态。继承 str 便于直接 JSON 序列化为字符串。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    GATE_REVIEW = "GATE_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BACKFLOWED = "BACKFLOWED"
    FAILED = "FAILED"


# 合法迁移表：key 可迁移到 value 集合内的任意状态。
# 说明：FAILED 是"任意非终态皆可进入"的逃生舱，统一在下方注入。
_ALLOWED: Final[dict[WorkorderStatus, frozenset[WorkorderStatus]]] = {
    WorkorderStatus.PENDING: frozenset({WorkorderStatus.RUNNING}),
    WorkorderStatus.RUNNING: frozenset({WorkorderStatus.GATE_REVIEW}),
    WorkorderStatus.GATE_REVIEW: frozenset(
        {WorkorderStatus.APPROVED, WorkorderStatus.REJECTED}
    ),
    WorkorderStatus.APPROVED: frozenset({WorkorderStatus.BACKFLOWED}),
    # Gate 失败后允许重新生成（回到 RUNNING）
    WorkorderStatus.REJECTED: frozenset({WorkorderStatus.RUNNING}),
    # 终态：无出边
    WorkorderStatus.BACKFLOWED: frozenset(),
    WorkorderStatus.FAILED: frozenset(),
}

# 终态集合
TERMINAL_STATES: Final[frozenset[WorkorderStatus]] = frozenset(
    {WorkorderStatus.BACKFLOWED, WorkorderStatus.FAILED}
)

# 任意"非终态"都可以因异常进入 FAILED
_FAILABLE: Final[frozenset[WorkorderStatus]] = frozenset(
    s for s in WorkorderStatus if s not in TERMINAL_STATES
)

VALID_PRIORITIES: Final[frozenset[str]] = frozenset({"normal", "high", "critical"})


class InvalidStateTransition(RuntimeError):
    """非法状态迁移时抛出。"""


def can_transition(src: WorkorderStatus, dst: WorkorderStatus) -> bool:
    """判断 src → dst 是否为合法迁移。"""
    if dst == WorkorderStatus.FAILED:
        return src in _FAILABLE
    return dst in _ALLOWED[src]


@dataclass
class Workorder:
    """通用 Workorder（cinemadna.workorder.v1）。

    三大资产模型复用本类；各自的差异只体现在 requirement / generation_plan
    两个自由字典里，不改变外层骨架与状态机。
    """

    workorder_id: str
    workorder_type: str  # SCENE | IDENTITY | PROP
    quad: Quad
    requested_by: str  # scriptbrain | director | human
    priority: str = "normal"
    status: WorkorderStatus = WorkorderStatus.PENDING
    requirement: dict[str, Any] = field(default_factory=dict)
    generation_plan: dict[str, Any] = field(default_factory=dict)
    internal_search_result: dict[str, Any] = field(
        default_factory=lambda: {
            "found": False,
            "matched_asset_ids": [],
            "similarity_scores": [],
        }
    )
    gate_report: dict[str, Any] | None = None
    result_asset_ids: list[str] = field(default_factory=list)
    backflow_record_id: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=schemas.utc_now_iso)
    updated_at: str = field(default_factory=schemas.utc_now_iso)
    schema_version: str = schemas.SCHEMA_WORKORDER

    # -- 校验 ---------------------------------------------------------------

    def __post_init__(self) -> None:
        schemas.require_schema_version(self.schema_version, schemas.SCHEMA_WORKORDER)
        schemas.require_workorder_type(self.workorder_type)
        if not isinstance(self.workorder_id, str) or not self.workorder_id:
            raise schemas.SchemaValidationError(
                f"workorder_id 不能为空: {self.workorder_id!r}"
            )
        if self.priority not in VALID_PRIORITIES:
            raise schemas.SchemaValidationError(
                f"priority 必须是 {sorted(VALID_PRIORITIES)} 之一，"
                f"实际: {self.priority!r}"
            )
        # 状态机初始化阶段四元组只做上下文级校验（asset_hash 可空）
        self.quad.validate()
        # 允许传入字符串状态，规范化为枚举
        if not isinstance(self.status, WorkorderStatus):
            self.status = WorkorderStatus(self.status)

    # -- 状态迁移 -----------------------------------------------------------

    def transition_to(self, dst: WorkorderStatus, *, error: str | None = None) -> "Workorder":
        """执行状态迁移。非法迁移抛 InvalidStateTransition。

        迁移成功时刷新 updated_at；迁移到 FAILED 时记录 error。
        """
        dst = WorkorderStatus(dst)  # 容忍字符串入参
        if not can_transition(self.status, dst):
            raise InvalidStateTransition(
                f"非法状态迁移: {self.status.value} → {dst.value}"
                f"（Workorder {self.workorder_id}）"
            )
        self.status = dst
        if dst == WorkorderStatus.FAILED and error is not None:
            self.error = error
        self.updated_at = schemas.utc_now_iso()
        return self

    @property
    def is_terminal(self) -> bool:
        """是否已到终态（BACKFLOWED / FAILED）。"""
        return self.status in TERMINAL_STATES

    # -- 便捷语义化迁移（可选糖，内部仍走 transition_to 统一校验）------------

    def mark_running(self) -> "Workorder":
        return self.transition_to(WorkorderStatus.RUNNING)

    def mark_gate_review(self, gate_report: dict[str, Any] | None = None) -> "Workorder":
        if gate_report is not None:
            self.gate_report = gate_report
        return self.transition_to(WorkorderStatus.GATE_REVIEW)

    def mark_approved(self) -> "Workorder":
        return self.transition_to(WorkorderStatus.APPROVED)

    def mark_rejected(self) -> "Workorder":
        return self.transition_to(WorkorderStatus.REJECTED)

    def mark_backflowed(self, backflow_record_id: str) -> "Workorder":
        self.backflow_record_id = backflow_record_id
        return self.transition_to(WorkorderStatus.BACKFLOWED)

    def mark_failed(self, error: str) -> "Workorder":
        return self.transition_to(WorkorderStatus.FAILED, error=error)

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """输出与接口文档 §1 完全对齐的结构化 JSON dict。"""
        q = self.quad
        return {
            "schema_version": self.schema_version,
            "workorder_id": self.workorder_id,
            "workorder_type": self.workorder_type,
            "story_id": q.story_id,
            "bundle_id": q.bundle_id,
            "task_id": q.task_id,
            "asset_hash": q.asset_hash,
            "status": self.status.value,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "requested_by": self.requested_by,
            "requirement": self.requirement,
            "internal_search_result": self.internal_search_result,
            "generation_plan": self.generation_plan,
            "gate_report": self.gate_report,
            "result_asset_ids": self.result_asset_ids,
            "backflow_record_id": self.backflow_record_id,
            "error": self.error,
        }


def new_workorder(
    *,
    workorder_id: str,
    workorder_type: str,
    quad: Quad,
    requested_by: str,
    requirement: dict[str, Any] | None = None,
    generation_plan: dict[str, Any] | None = None,
    priority: str = "normal",
) -> Workorder:
    """工厂函数：创建一张 PENDING 状态的通用 Workorder。

    三大 Service 的 create_workorder() 都应通过本入口构造，保证结构一致。
    """
    return Workorder(
        workorder_id=workorder_id,
        workorder_type=workorder_type,
        quad=quad,
        requested_by=requested_by,
        requirement=requirement or {},
        generation_plan=generation_plan or {},
        priority=priority,
    )
