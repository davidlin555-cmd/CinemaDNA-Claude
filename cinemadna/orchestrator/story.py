"""Pipeline Orchestrator — 单个故事的状态模型 (Phase 1)

对应主规格 L2「多故事状态机、并行调度、Human Review Hold、自动续跑」。

## 为什么是"两个轴"而不是一个大枚举

一个故事在任意时刻有两件互相独立的事实：

- **stage（生产进度）**：走到哪一步了 —— CREATED / SCRIPT_RUNNING / SCRIPT_DONE /
  ASSET_MATCHING / ... / PUBLISHED / ARCHIVED
- **status（运行状况）**：此刻能不能自己往前跑 —— RUNNING / WAITING_HUMAN /
  BLOCKED / CLOSED

如果把 WAITING_HUMAN 塞进 stage 枚举，人一挂起就丢掉了"挂起前在哪一步"，
"人工通过后自动续跑"（主规格明确要求）就只能靠额外字段补救。
拆成两轴后，hold / release 只动 status，stage 原地不动，续跑天然成立。

对外仍提供扁平视图 `Story.state`：挂起/阻塞时返回 WAITING_HUMAN / BLOCKED，
否则返回 stage 名，供 Web 看板与简单查询直接使用。

## 阶段迁移表

    CREATED → SCRIPT_RUNNING → SCRIPT_DONE → ASSET_MATCHING → ASSET_READY
      → DIRECTING → PERFORMANCE → RENDERING → QA → POST → PUBLISHED → ARCHIVED

    QA / POST 可按 Repair Planner 的结论**精准打回**到
    {ASSET_MATCHING, DIRECTING, PERFORMANCE, RENDERING} 之一（不是重跑全剧）。
    任意非终态 → FAILED / CANCELLED。

终态：ARCHIVED / FAILED / CANCELLED（PUBLISHED 不是终态，还要关 Bundle）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.quad import Quad


class StoryStage(str, Enum):
    """故事的生产进度。继承 str 便于直接 JSON 序列化。"""

    CREATED = "CREATED"
    SCRIPT_RUNNING = "SCRIPT_RUNNING"
    SCRIPT_DONE = "SCRIPT_DONE"
    DIRECTOR_PLANNING = "DIRECTOR_PLANNING"   # 总导演出总谱（宪法层上游）
    DIRECTOR_QA = "DIRECTOR_QA"               # 验收官签发+锁定总谱
    ASSET_MATCHING = "ASSET_MATCHING"
    ASSET_READY = "ASSET_READY"
    DIRECTING = "DIRECTING"
    PERFORMANCE = "PERFORMANCE"
    RENDERING = "RENDERING"
    QA = "QA"
    POST = "POST"
    #: 成片级总审核（QA Council 5 维裁决）——PUBLISHED 前的最后硬门
    FINAL_REVIEW = "FINAL_REVIEW"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StoryStatus(str, Enum):
    """故事的运行状况（与 stage 正交）。"""

    RUNNING = "RUNNING"
    #: 卡在人工审核闸门。铁规：不得阻塞其它故事。仅 3 个点（剧本/人脸/成片）。
    WAITING_HUMAN = "WAITING_HUMAN"
    #: 系统自修复中：机器判负后自动打回对应模块重做，**不停厂、不等人**。
    #: 携带 repair_directive；驱动器负责执行修复动作并在成功后自动续跑。
    AUTO_REPAIRING = "AUTO_REPAIRING"
    #: 被机器判负卡住。稳态下应被驱动器立刻转成 AUTO_REPAIRING / 升级人工 / FAILED，
    #: 只作为"红线硬拦/自修复耗尽"的瞬时中间态。
    BLOCKED = "BLOCKED"
    #: 故事已进入终态（ARCHIVED / FAILED / CANCELLED）。
    CLOSED = "CLOSED"


_ALLOWED_STAGE: Final[dict[StoryStage, frozenset[StoryStage]]] = {
    StoryStage.CREATED: frozenset({StoryStage.SCRIPT_RUNNING}),
    StoryStage.SCRIPT_RUNNING: frozenset({StoryStage.SCRIPT_DONE}),
    StoryStage.SCRIPT_DONE: frozenset({StoryStage.DIRECTOR_PLANNING}),
    StoryStage.DIRECTOR_PLANNING: frozenset({StoryStage.DIRECTOR_QA}),
    # 验收不过可回退到规划重做；过了进资产
    StoryStage.DIRECTOR_QA: frozenset({StoryStage.ASSET_MATCHING,
                                       StoryStage.DIRECTOR_PLANNING}),
    StoryStage.ASSET_MATCHING: frozenset({StoryStage.ASSET_READY}),
    StoryStage.ASSET_READY: frozenset({StoryStage.DIRECTING}),
    StoryStage.DIRECTING: frozenset({StoryStage.PERFORMANCE}),
    StoryStage.PERFORMANCE: frozenset({StoryStage.RENDERING}),
    StoryStage.RENDERING: frozenset({StoryStage.QA}),
    StoryStage.QA: frozenset({StoryStage.POST}),
    StoryStage.POST: frozenset({StoryStage.FINAL_REVIEW}),
    StoryStage.FINAL_REVIEW: frozenset({StoryStage.PUBLISHED}),
    StoryStage.PUBLISHED: frozenset({StoryStage.ARCHIVED}),
    StoryStage.ARCHIVED: frozenset(),
    StoryStage.FAILED: frozenset(),
    StoryStage.CANCELLED: frozenset(),
}

#: 终态：不可再迁移
TERMINAL_STAGES: Final[frozenset[StoryStage]] = frozenset(
    {StoryStage.ARCHIVED, StoryStage.FAILED, StoryStage.CANCELLED}
)

#: Repair Planner 允许打回的目标阶段（只打回最小必要模块）
REPAIR_TARGETS: Final[frozenset[StoryStage]] = frozenset(
    {
        StoryStage.ASSET_MATCHING,
        StoryStage.DIRECTING,
        StoryStage.PERFORMANCE,
        StoryStage.RENDERING,
    }
)

#: 允许发起打回的阶段
#: RENDERING 也在内：镜头级 QA 就发生在渲染之后、粗剪之前，
#: 发现问题应当当场打回，而不是等拼完再回头。
REPAIR_SOURCES: Final[frozenset[StoryStage]] = frozenset(
    {StoryStage.RENDERING, StoryStage.QA, StoryStage.POST, StoryStage.FINAL_REVIEW}
)


class InvalidStageTransition(RuntimeError):
    """非法阶段迁移。"""


def can_advance(src: StoryStage, dst: StoryStage) -> bool:
    """判断 src → dst 是否为合法的正向阶段迁移。"""
    if dst in (StoryStage.FAILED, StoryStage.CANCELLED):
        return src not in TERMINAL_STAGES
    return dst in _ALLOWED_STAGE[src]


def can_repair(src: StoryStage, dst: StoryStage) -> bool:
    """判断 src → dst 是否为合法的 Repair 打回。"""
    return src in REPAIR_SOURCES and dst in REPAIR_TARGETS


@dataclass
class Story:
    """一部剧在工厂里的完整运行态（cinemadna.story.v1）。"""

    story_id: str
    bundle_id: str
    title: str = ""
    priority: str = "normal"
    stage: StoryStage = StoryStage.CREATED
    status: StoryStatus = StoryStatus.RUNNING
    script_ref: str | None = None
    script_submitted_at: str | None = None
    script_summary: dict[str, Any] = field(default_factory=dict)
    hold: dict[str, Any] | None = None
    blocked_reason: str | None = None
    #: 自修复指令（AUTO_REPAIRING 时非空）：{code, target_stage, attempt, max, escalation, ...}
    repair_directive: dict[str, Any] | None = None
    asset_summary: dict[str, Any] = field(default_factory=dict)
    #: 资产阶段产出的绑定表：scene_id → 场景资产 asset_id 等
    asset_bindings: dict[str, Any] = field(default_factory=dict)
    shot_summary: dict[str, Any] = field(default_factory=dict)
    performance_summary: dict[str, Any] = field(default_factory=dict)
    render_summary: dict[str, Any] = field(default_factory=dict)
    prerender_summary: dict[str, Any] = field(default_factory=dict)
    qa_summary: dict[str, Any] = field(default_factory=dict)
    cut_summary: dict[str, Any] = field(default_factory=dict)
    final_review_summary: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=schemas.utc_now_iso)
    updated_at: str = field(default_factory=schemas.utc_now_iso)
    schema_version: str = schemas.SCHEMA_STORY
    #: 每类任务的 task_id 自增计数（scenedna / identitydna / propdna / script ...）
    _task_counters: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        schemas.require_schema_version(self.schema_version, schemas.SCHEMA_STORY)
        if not isinstance(self.stage, StoryStage):
            self.stage = StoryStage(self.stage)
        if not isinstance(self.status, StoryStatus):
            self.status = StoryStatus(self.status)
        # 借四元组的 ID 规则校验 story_id / bundle_id，保证可直接作路径片段
        Quad(
            story_id=self.story_id,
            bundle_id=self.bundle_id,
            task_id="task_init",
            asset_hash=None,
        ).validate()
        self.log_event("STORY_CREATED", note=self.title)

    # -- 扁平视图 -----------------------------------------------------------

    @property
    def state(self) -> str:
        """给看板/查询用的单值状态：挂起/自修复/阻塞时优先反映运行状况。"""
        if self.status in (StoryStatus.WAITING_HUMAN, StoryStatus.AUTO_REPAIRING,
                           StoryStatus.BLOCKED):
            return self.status.value
        return self.stage.value

    @property
    def is_terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES

    @property
    def occupies_script_slot(self) -> bool:
        """是否正占用一个 ScriptBrain 并发槽位。

        关键设计：只有"正在真跑剧本"才算占位。一旦剧本提交（SCRIPT_DONE）
        或卡在人工闸门（WAITING_HUMAN），槽位立即释放 —— 这正是
        「剧本提交后立刻可开新剧」与「人工审核不阻塞其它故事」两条铁规的落点。
        """
        return self.stage is StoryStage.SCRIPT_RUNNING and self.status is StoryStatus.RUNNING

    # -- 事件日志 -----------------------------------------------------------

    def log_event(self, event: str, **fields: Any) -> None:
        self.updated_at = schemas.utc_now_iso()
        self.events.append({"at": self.updated_at, "event": event, **fields})

    # -- 阶段迁移 -----------------------------------------------------------

    def advance_to(self, dst: StoryStage, *, note: str | None = None) -> "Story":
        """正向推进阶段。非法迁移抛 InvalidStageTransition。"""
        dst = StoryStage(dst)
        # 挂起中不许继续推进生产（但允许直接判负/取消——放弃一部剧不需要先解挂）
        if self.status is StoryStatus.WAITING_HUMAN and dst not in (
            StoryStage.FAILED,
            StoryStage.CANCELLED,
        ):
            raise InvalidStageTransition(
                f"故事 {self.story_id} 正卡在人工闸门（{self.hold}），"
                f"必须先 release_hold 才能推进阶段"
            )
        if not can_advance(self.stage, dst):
            raise InvalidStageTransition(
                f"非法阶段迁移: {self.stage.value} → {dst.value}（故事 {self.story_id}）"
            )
        src = self.stage
        self.stage = dst
        if dst in TERMINAL_STAGES:
            self.status = StoryStatus.CLOSED
        elif self.status in (StoryStatus.BLOCKED, StoryStatus.AUTO_REPAIRING):
            # 阶段推进意味着此前的阻塞/自修复已解除
            self.status = StoryStatus.RUNNING
            self.blocked_reason = None
            self.repair_directive = None
        self.log_event("STAGE_ADVANCED", **{"from": src.value, "to": dst.value, "note": note})
        return self

    def repair_to(self, dst: StoryStage, *, reason: str) -> "Story":
        """Repair Planner 精准打回：只回退到必要的模块，不重跑全剧。"""
        dst = StoryStage(dst)
        if not can_repair(self.stage, dst):
            raise InvalidStageTransition(
                f"非法打回: {self.stage.value} → {dst.value}；"
                f"仅允许从 {sorted(s.value for s in REPAIR_SOURCES)} "
                f"打回到 {sorted(s.value for s in REPAIR_TARGETS)}"
            )
        src = self.stage
        self.stage = dst
        self.status = StoryStatus.RUNNING
        self.blocked_reason = None
        self.repair_directive = None
        self.log_event("REPAIR_ROLLBACK", **{"from": src.value, "to": dst.value, "note": reason})
        return self

    # -- 自修复（AUTO_REPAIRING：机器打回重做，不停厂、不等人）----------------

    def enter_auto_repair(self, directive: dict[str, Any]) -> "Story":
        """进入系统自修复态。stage 不变，携带修复指令供驱动器执行。"""
        if self.is_terminal:
            raise InvalidStageTransition(f"故事 {self.story_id} 已终态，不能自修复")
        self.status = StoryStatus.AUTO_REPAIRING
        self.repair_directive = dict(directive)
        self.blocked_reason = None
        self.log_event("AUTO_REPAIR_ENTER", code=directive.get("code"),
                       target=directive.get("target_stage"),
                       attempt=directive.get("attempt"))
        return self

    def clear_auto_repair(self, note: str = "") -> "Story":
        """自修复成功，回到 RUNNING（stage 不变，续跑）。"""
        self.status = StoryStatus.RUNNING
        self.repair_directive = None
        self.log_event("AUTO_REPAIR_CLEARED", note=note)
        return self

    def fail(self, error: str) -> "Story":
        self.error = error
        return self.advance_to(StoryStage.FAILED, note=error)

    def cancel(self, reason: str = "") -> "Story":
        return self.advance_to(StoryStage.CANCELLED, note=reason)

    # -- Human Hold（stage 不变，只动 status）-------------------------------

    def hold_for_human(
        self, reason: str, *, gate_ids: list[str] | None = None, note: str = ""
    ) -> "Story":
        """挂起等待人工审核。stage 保持不动，以便通过后原地续跑。"""
        if self.is_terminal:
            raise InvalidStageTransition(f"故事 {self.story_id} 已终态，不能挂起")
        self.status = StoryStatus.WAITING_HUMAN
        self.hold = {
            "reason": reason,
            "gate_ids": list(gate_ids or []),
            "stage": self.stage.value,
            "since": schemas.utc_now_iso(),
            "note": note,
        }
        self.log_event("HUMAN_HOLD", reason=reason, stage=self.stage.value)
        return self

    def release_hold(self, *, decided_by: str, note: str = "") -> "Story":
        """人工放行后自动续跑：回到 RUNNING，stage 仍是挂起时那一步。"""
        if self.status is not StoryStatus.WAITING_HUMAN:
            raise InvalidStageTransition(
                f"故事 {self.story_id} 当前不处于 WAITING_HUMAN，无需释放"
            )
        self.hold = None
        self.status = StoryStatus.RUNNING
        self.log_event("HOLD_RELEASED", decided_by=decided_by, note=note)
        return self

    # -- 机器阻塞 -----------------------------------------------------------

    def block(self, reason: str) -> "Story":
        if self.is_terminal:
            raise InvalidStageTransition(f"故事 {self.story_id} 已终态，不能阻塞")
        self.status = StoryStatus.BLOCKED
        self.blocked_reason = reason
        self.log_event("BLOCKED", reason=reason)
        return self

    def unblock(self, note: str = "") -> "Story":
        if self.status is not StoryStatus.BLOCKED:
            raise InvalidStageTransition(f"故事 {self.story_id} 当前不处于 BLOCKED")
        self.status = StoryStatus.RUNNING
        self.blocked_reason = None
        self.log_event("UNBLOCKED", note=note)
        return self

    # -- 四元组对接 ---------------------------------------------------------

    def new_task_id(self, kind: str) -> str:
        """为该故事分配一个新的 task_id，如 task_scenedna_0001。"""
        n = self._task_counters.get(kind, 0) + 1
        self._task_counters[kind] = n
        return f"task_{kind}_{n:04d}"

    def quad(self, task_id: str | None = None, *, kind: str = "generic") -> Quad:
        """产出绑定本故事的四元组上下文（asset_hash 待资产产出后再绑）。"""
        return Quad(
            story_id=self.story_id,
            bundle_id=self.bundle_id,
            task_id=task_id or self.new_task_id(kind),
            asset_hash=None,
        ).validate()

    # -- 序列化 -------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "story_id": self.story_id,
            "bundle_id": self.bundle_id,
            "title": self.title,
            "priority": self.priority,
            "stage": self.stage.value,
            "status": self.status.value,
            "state": self.state,
            "occupies_script_slot": self.occupies_script_slot,
            "script_ref": self.script_ref,
            "script_submitted_at": self.script_submitted_at,
            "script_summary": self.script_summary,
            "hold": self.hold,
            "blocked_reason": self.blocked_reason,
            "repair_directive": self.repair_directive,
            "asset_summary": self.asset_summary,
            "asset_bindings": self.asset_bindings,
            "shot_summary": self.shot_summary,
            "performance_summary": self.performance_summary,
            "render_summary": self.render_summary,
            "prerender_summary": self.prerender_summary,
            "qa_summary": self.qa_summary,
            "cut_summary": self.cut_summary,
            "final_review_summary": self.final_review_summary,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": list(self.events),
        }
