"""统一审核结果模型 (Phase F)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

VERDICT_PASS: Final = "PASS"        # 专业审核通过
VERDICT_REPAIR: Final = "REPAIR"    # 未过 → 自动打回修复
VERDICT_CONFIRM: Final = "CONFIRM"  # 通过、待人工终确认（仅 3 个点）
VERDICT_PENDING: Final = "PENDING"  # 该模块尚未产出

#: 9 个模块的顺序（= 生产链顺序，控制台按此排）
MODULE_ORDER: Final[tuple[str, ...]] = (
    "script", "scene", "identity", "prop", "director",
    "performance", "render", "audio", "final",
)

MODULE_LABELS: Final[dict[str, str]] = {
    "script": "剧本", "scene": "场景", "identity": "人脸/角色", "prop": "道具",
    "director": "分镜", "performance": "表演", "render": "渲染",
    "audio": "声音", "final": "成片总审",
}

#: 各模块未过时的默认修复码（对齐 orchestrator.repair.POLICY）
MODULE_REPAIR_CODE: Final[dict[str, str]] = {
    "scene": "SCENE_FIX", "identity": "IDENTITY_MISSING", "prop": "PROP_FIX",
    "director": "DIRECTOR_FIX", "performance": "PERFORMANCE_FIX",
    "render": "RENDER_RETRY", "audio": "AUDIO_FIX", "final": "FINAL_REPAIR",
}

#: 3 个终确认点：模块 → 人工闸门 reason
MODULE_HUMAN_GATE: Final[dict[str, str]] = {
    "script": "script_gate3",
    "identity": "identity_review",
    "final": "final_review_critical",
}


@dataclass
class ReviewResult:
    """一个模块的统一审核结论。"""

    module: str
    verdict: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    repair_code: str | None = None
    human_gate: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return MODULE_LABELS.get(self.module, self.module)

    @property
    def passed(self) -> bool:
        return self.verdict in (VERDICT_PASS, VERDICT_CONFIRM)

    @property
    def needs_repair(self) -> bool:
        return self.verdict == VERDICT_REPAIR

    def to_dict(self) -> dict[str, Any]:
        return {
            "module": self.module,
            "label": self.label,
            "verdict": self.verdict,
            "passed": self.passed,
            "issue_count": len(self.issues),
            "issues": self.issues[:20],
            "repair_code": self.repair_code,
            "human_gate": self.human_gate,
            "detail": self.detail,
        }


def _mk(module: str, verdict: str, issues=None, **detail) -> ReviewResult:
    """构造 ReviewResult 并自动补 repair_code / human_gate。"""
    rc = MODULE_REPAIR_CODE.get(module) if verdict == VERDICT_REPAIR else None
    hg = MODULE_HUMAN_GATE.get(module) if verdict == VERDICT_CONFIRM else None
    return ReviewResult(module=module, verdict=verdict, issues=list(issues or []),
                        repair_code=rc, human_gate=hg, detail=detail)
