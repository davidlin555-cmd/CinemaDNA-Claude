"""成片级 Final Review · QA Council 裁决器 (Phase 6)

Layer 3：不是新 Agent，是 Final Review 的裁决层。汇总 5 维发现后：

  ① 任一"硬确定"指标失败 → 直接否决，按该指标 target 精准打回（复用 Repair Planner）
  ② "软判断"指标 → 未接入判断层的记 PENDING
  ③ commercial_ready = true 需：五维硬门全过 + 无 PENDING 阻断项 + 人工终确认

铁律：**判断层没检查的维度绝不当 PASS**。所以裁决分三档：
  - REJECTED   有致命/高危硬门失败 → 阻断，精准打回
  - HUMAN      有 critical（机器判不了）→ 转人工
  - PASS_STRUCTURAL  结构硬门全过，但判断层仍 PENDING → 结构达标，**尚未达到可发布**
  - PASS_FULL  结构 + 判断层全过（判断层接入后才可能）→ 可发布（仍建议人工终确认）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.bundle import Bundle
from qa.agents import BLOCKING_SEVERITIES, SEVERITY_CRITICAL, SEVERITY_ORDER, TARGET_ORDER
from qa.repair import plan_repair

from .checklist import checklist_passed, evaluate_checklist
from .dimensions import ALL_DIMENSIONS

VERDICT_REJECTED = "REJECTED"
VERDICT_HUMAN = "HUMAN"
VERDICT_PASS_STRUCTURAL = "PASS_STRUCTURAL"
VERDICT_PASS_FULL = "PASS_FULL"


@dataclass
class FinalReviewResult:
    story_id: str
    verdict: str
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    blocking_issues: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    repair_plan: dict[str, Any] = field(default_factory=dict)
    #: 商业可发布清单逐条结论（PASS_FULL 硬条件）
    checklist: list[dict[str, Any]] = field(default_factory=list)
    #: 仍需多模态判断的软项（仅信息，不阻断 PASS_FULL）
    advisory: list[dict[str, Any]] = field(default_factory=list)

    @property
    def commercial_ready(self) -> bool:
        return self.verdict == VERDICT_PASS_FULL

    @property
    def structural_passed(self) -> bool:
        return self.verdict in (VERDICT_PASS_STRUCTURAL, VERDICT_PASS_FULL)

    @property
    def failed_checklist(self) -> list[dict[str, Any]]:
        return [c for c in self.checklist if not c["ok"]]

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": schemas.SCHEMA_FINAL_REVIEW,
            "story_id": self.story_id,
            "verdict": self.verdict,
            "commercial_ready": self.commercial_ready,
            "structural_passed": self.structural_passed,
            "dimensions": self.dimensions,
            "blocking_issues": self.blocking_issues,
            "checklist": self.checklist,
            "checklist_failed": self.failed_checklist,
            "advisory": self.advisory,
            "pending_count": len(self.pending),
            "pending": self.pending,
            "repair_plan": self.repair_plan,
            "note": (
                "PASS_STRUCTURAL = 结构硬门全过但商业可发布清单有项未过（结构完整、"
                "尚未可发布）；清单全过才 PASS_FULL（商业可发布）。"
            ),
            "checked_at": schemas.utc_now_iso(),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "commercial_ready": self.commercial_ready,
            "structural_passed": self.structural_passed,
            "blocking_issues": len(self.blocking_issues),
            "pending": len(self.pending),
            "target_stage": self.repair_plan.get("target_stage"),
            "by_dimension": {d["dimension"]: len(d["issues"]) for d in self.dimensions},
        }


class FinalReviewService:
    """成片级总审（QA Council）。"""

    def __init__(self, bundle: Bundle | None = None) -> None:
        self.bundle = bundle

    def run(self, ctx: dict[str, Any], *, story_id: str) -> FinalReviewResult:
        dims = [fn(ctx) for fn in ALL_DIMENSIONS]

        all_issues = [i for d in dims for i in d["issues"]]
        # 各维度的多模态软项 → 咨询项（记录但不阻断 PASS_FULL；硬条件看清单）
        advisory = [{**p, "dimension": d["dimension"]}
                    for d in dims for p in d["pending"]]

        blocking = [i for i in all_issues if i["severity"] in BLOCKING_SEVERITIES]
        critical = [i for i in blocking if i["severity"] == SEVERITY_CRITICAL]

        # 商业可发布清单（PASS_FULL 硬条件）
        checklist = evaluate_checklist(ctx)
        failed = [c for c in checklist if not c["ok"]]

        total_shots = len(ctx.get("contracts") or [])

        if critical:
            verdict = VERDICT_HUMAN
            plan = plan_repair(all_issues, story_id=story_id, total_shots=total_shots)
        elif blocking:
            verdict = VERDICT_REJECTED
            plan = plan_repair(all_issues, story_id=story_id, total_shots=total_shots)
        elif failed:
            # 结构硬门全过，但商业清单有项未过 → 结构达标、尚未可发布，精准打回
            verdict = VERDICT_PASS_STRUCTURAL
            target = min((c["target"] for c in failed),
                         key=lambda t: TARGET_ORDER.get(t, 9))
            plan = {"schema_version": schemas.SCHEMA_REPAIR_PLAN,
                    "story_id": story_id, "verdict": "REPAIR",
                    "target_stage": target,
                    "reason": "商业清单未过：" + "; ".join(
                        f"{c['name']}({c['detail']})" for c in failed),
                    "failed_checks": [c["name"] for c in failed],
                    "affected_shots": [], "shots_to_redo": 0}
        else:
            verdict = VERDICT_PASS_FULL
            plan = {"target_stage": None, "reason": "商业可发布清单全过"}

        result = FinalReviewResult(
            story_id=story_id, verdict=verdict, dimensions=dims,
            blocking_issues=sorted(blocking,
                                   key=lambda i: SEVERITY_ORDER[i["severity"]]),
            pending=advisory, repair_plan=plan,
            checklist=checklist, advisory=advisory,
        )
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/final_review/{story_id}.json", result.report())
        return result
