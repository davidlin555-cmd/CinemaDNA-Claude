"""FactoryOptimizer (Phase G) —— 自我优化闭环 + 自我验收。

闭环：analyze(遥测) → 自动应用低风险可逆调参 → 记基线 → 下一轮再看指标 →
不劣于基线则保留，变差则**整档回滚**。这就是"优化本身也要被验收，不能越优化越差"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .analyzer import (
    Recommendation,
    analyze,
    hotspot_report,
    is_better,
    target_metrics,
)
from .profile import TuningProfile


@dataclass
class OptimizationReport:
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    applied: dict[str, int] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    action: str = "none"            # analyzed | applied | kept | rolled_back
    profile_version: int = 0
    hotspots: dict[str, Any] = field(default_factory=dict)   # 修复热点报告
    before: dict[str, float] = field(default_factory=dict)   # 上版基线
    after: dict[str, float] = field(default_factory=dict)    # 本轮指标

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "profile_version": self.profile_version,
            "metrics": self.metrics,
            "applied_caps": self.applied,
            "recommendations": self.recommendations,
            "hotspots": self.hotspots,
            "before": self.before, "after": self.after,
        }


class FactoryOptimizer:
    """把复盘分析变成对 TuningProfile 的可逆调参，并对结果自我验收。"""

    def __init__(self, profile: TuningProfile | None = None) -> None:
        self.profile = profile or TuningProfile()

    def analyze(self, telemetry: dict[str, Any]) -> list[Recommendation]:
        return analyze(telemetry)

    def run(self, telemetry: dict[str, Any], *, auto_apply: bool = True
            ) -> OptimizationReport:
        """一轮优化。

        1) 先对**上一次**应用做自我验收：现指标不劣于基线 → 保留；变差 → 回滚。
        2) 再分析当前遥测，自动应用低风险可逆建议（重试上限），记新基线。
        """
        recs = analyze(telemetry)
        metrics = target_metrics(telemetry)
        before = dict(self.profile.baseline)   # 上版应用时记录的基线
        report = OptimizationReport(
            recommendations=[r.to_dict() for r in recs],
            metrics=metrics, action="analyzed",
            profile_version=self.profile.version,
            hotspots=hotspot_report(telemetry),
            before=before, after=metrics)

        # 1) 自我验收上一版：前后对比
        if self.profile.baseline:
            if is_better(self.profile.baseline, metrics):
                report.action = "kept"
            else:
                if self.profile.rollback():
                    report.action = "rolled_back"
                    report.profile_version = self.profile.version
                    return report      # 回滚后本轮不再叠加新调参，先稳住

        if not auto_apply:
            report.applied = dict(self.profile.repair_cap_override)
            return report

        # 2) 应用低风险可逆建议
        cap_override = dict(self.profile.repair_cap_override)
        watch: list[str] = []
        for r in recs:
            if r.auto and r.cap_delta:
                cap_override.update(r.cap_delta)
            elif r.kind == "hotspot":
                watch.append(r.target)
        if cap_override != self.profile.repair_cap_override or watch != self.profile.watch:
            self.profile.apply(cap_override=cap_override, watch=watch, baseline=metrics)
            report.action = "applied"
        report.applied = dict(self.profile.repair_cap_override)
        report.profile_version = self.profile.version
        return report
