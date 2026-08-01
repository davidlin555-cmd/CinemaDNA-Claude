"""DirectorDNA 发单门 —— 无总谱签字，下游一律不得开工。

宪法层铁规：SceneDNA / IdentityDNA / PropDNA / PerformanceDNA / Render 在
DirectorMasterPlan 锁定前不得开工；下游只读总谱、不得改叙事字段。
"""

from __future__ import annotations

from director.master_plan import DirectorMasterPlan


class DirectorGateError(RuntimeError):
    """无锁定总谱却试图开工。"""


def require_locked_plan(plan: DirectorMasterPlan | None, *, action: str) -> DirectorMasterPlan:
    """下游任何开工前调用：无锁定/被篡改的总谱直接拒绝。"""
    if plan is None:
        raise DirectorGateError(f"{action} 被拒：还没有 DirectorMasterPlan（导演未签字）")
    if not plan.locked:
        raise DirectorGateError(f"{action} 被拒：DirectorMasterPlan 未锁定（未过导演验收）")
    if not plan.verify_lock():
        raise DirectorGateError(f"{action} 被拒：DirectorMasterPlan 被篡改（hash 不匹配）")
    return plan


__all__ = ["require_locked_plan", "DirectorGateError"]
