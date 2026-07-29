"""镜头级 QA · Phase 4 Service —— 汇总三个质检 Agent 并给出修复计划

对应多智能体文档 §2.10 的 Global QA Aggregator + Repair Planner。

诚实边界：Phase 4 的质检**不看画面**。它检查的是结构（产物在不在、
时长对不对、参考有没有被掉包、台词有没有人演、道具状态有没有分裂），
外加 mock 分数。真正的多模态画面质检（人脸崩坏、动作畸变、光影跳变）
需要接视觉模型，接入点就是往 `vision_qa` 的 issues 里追加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asset_brain.common import schemas
from asset_brain.common.bundle import Bundle

from .agents import (
    BLOCKING_SEVERITIES,
    SEVERITY_ORDER,
    continuity_qa,
    performance_qa,
    vision_qa,
)
from .repair import VERDICT_PASS, plan_repair


@dataclass
class QAResult:
    vision: dict[str, Any]
    performance: dict[str, Any]
    continuity: dict[str, Any]
    global_report: dict[str, Any]
    repair_plan: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.repair_plan["verdict"] == VERDICT_PASS

    @property
    def verdict(self) -> str:
        return self.repair_plan["verdict"]

    def artifacts(self) -> dict[str, Any]:
        sid = self.global_report["story_id"]
        return {
            f"11_review/qa/{sid}_vision.json": self.vision,
            f"11_review/qa/{sid}_performance.json": self.performance,
            f"11_review/qa/{sid}_continuity.json": self.continuity,
            f"11_review/qa/{sid}_global.json": self.global_report,
            f"11_review/qa/{sid}_repair_plan.json": self.repair_plan,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "blocking_issues": self.global_report["blocking_issues"],
            "info_notes": self.global_report["info_count"],
            "target_stage": self.repair_plan["target_stage"],
            "shots_to_redo": self.repair_plan["shots_to_redo"],
            "by_agent": self.global_report["by_agent"],
        }


class ShotQAService:
    """镜头级质检：三个 Agent → 汇总 → 修复计划。"""

    def __init__(self, bundle: Bundle | None = None) -> None:
        self.bundle = bundle

    def run(
        self,
        contracts: list[dict[str, Any]],
        *,
        story_id: str,
        continuity_report: dict[str, Any] | None = None,
    ) -> QAResult:
        v = vision_qa(contracts, bundle=self.bundle)
        p = performance_qa(contracts)
        c = continuity_qa(contracts, continuity_report=continuity_report)

        all_issues = [*v["issues"], *p["issues"], *c["issues"]]
        blocking = [i for i in all_issues if i["severity"] in BLOCKING_SEVERITIES]
        info = [i for i in all_issues if i["severity"] not in BLOCKING_SEVERITIES]

        plan = plan_repair(all_issues, story_id=story_id, total_shots=len(contracts))

        global_report = {
            "schema_version": schemas.SCHEMA_GLOBAL_QA,
            "story_id": story_id,
            "shots_checked": len(contracts),
            "verdict": plan["verdict"],
            "passed": plan["verdict"] == VERDICT_PASS,
            "blocking_issues": len(blocking),
            "info_count": len(info),
            "issues": sorted(
                all_issues, key=lambda i: SEVERITY_ORDER[i["severity"]]
            ),
            "by_agent": {
                "vision": v["blocking_count"],
                "performance": p["blocking_count"],
                "continuity": c["blocking_count"],
            },
            # 说清楚这轮质检有多少是真判定
            "structural_checks_only": all(i["structural"] for i in all_issues),
            "notes": (
                "Phase 4 质检不看画面：只做结构判定 + mock 分数。"
                "真实多模态质检需接视觉模型。"
            ),
            "checked_at": schemas.utc_now_iso(),
        }

        warnings = [i["message"] for i in info]
        return QAResult(
            vision=v, performance=p, continuity=c,
            global_report=global_report, repair_plan=plan, warnings=warnings,
        )
