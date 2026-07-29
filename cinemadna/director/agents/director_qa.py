"""DirectorQAOfficer（验收官）—— 总谱自检；不过打回总导演重做，过了才准 broadcast。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from director.master_plan import DirectorMasterPlan, STORY_FUNCTIONS

from .relationship import RelationshipContinuityAgent
from .staging import StagingBlockingAgent
from .action import ActionPerformanceIntentAgent
from .camera import CameraLanguageAgent
from .prop_info import PropInformationAgent
from .density import CommercialDensityAgent

VERDICT_PASS = "PASS"
VERDICT_REPAIR = "REPAIR"

#: 有台词却被标成无声景别的镜（硬门 3）
_SILENT_TYPES = ("REACTION", "INSERT", "ESTABLISHING")


@dataclass
class DirectorQAResult:
    verdict: str
    hard_issues: list[dict[str, Any]] = field(default_factory=list)   # 硬门：不过不得下行
    soft_issues: list[dict[str, Any]] = field(default_factory=list)   # 专业建议：记录不阻塞

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    @property
    def issues(self) -> list[dict[str, Any]]:
        return self.hard_issues + self.soft_issues

    def report(self) -> dict[str, Any]:
        by = {}
        for i in self.issues:
            by[i.get("agent", "?")] = by.get(i.get("agent", "?"), 0) + 1
        return {"verdict": self.verdict, "hard": len(self.hard_issues),
                "soft": len(self.soft_issues), "by_agent": by,
                "hard_issues": self.hard_issues, "soft_issues": self.soft_issues}


class DirectorQAOfficer:
    """汇总 6 专业智能体 + 硬门自检。"""

    def __init__(self) -> None:
        self.specialists = [
            RelationshipContinuityAgent(), StagingBlockingAgent(),
            ActionPerformanceIntentAgent(), CameraLanguageAgent(),
            PropInformationAgent(), CommercialDensityAgent()]

    def review(self, plan: DirectorMasterPlan) -> DirectorQAResult:
        soft: list[dict[str, Any]] = []
        # 专业智能体各自复核（建议层，记录不阻塞）
        for ag in self.specialists:
            soft.extend(ag.review(plan))
        # 导演层**硬门**（任务书五·C）：任一不过则不得下行
        hard: list[dict[str, Any]] = []
        if not plan.shots:
            hard.append(_hard("", "总谱无镜头"))
        for s in plan.shots:
            if s.story_function not in STORY_FUNCTIONS:                       # 硬门2
                hard.append(_hard(s.shot_id, f"story_function 缺失/非法：{s.story_function!r}"))
            if not s.action_beats or not (s.action_beats[0].get("body_action") or "").strip():
                hard.append(_hard(s.shot_id, "缺可执行动作（硬门4）"))          # 硬门4
        hard.extend(_composition_monotony(plan))                              # 硬门5:构图单调
        verdict = VERDICT_REPAIR if hard else VERDICT_PASS
        return DirectorQAResult(verdict=verdict, hard_issues=hard, soft_issues=soft)


def _composition_monotony(plan: DirectorMasterPlan) -> list[dict[str, Any]]:
    """硬门5:构图单调 —— 景别分布过于集中/长串同景别 → 打回重排(治"全片正面站街中景")。

    只对 ≥6 镜的片生效(短片天然景别少不算病)。三条任一不过即打回:
    ①景别种类 <3 ②连续同景别 ≥4 镜 ③单一景别占比 >70%。CameraLanguageAgent.refine
    已在锁定前主动打散,正常应过;过不了说明分镜真的呆板,精准打回导演重排。
    """
    sizes = [(s.camera.get("shot_size") or "").strip() for s in plan.shots]
    sizes = [x for x in sizes if x]
    n = len(sizes)
    if n < 6:
        return []
    out: list[dict[str, Any]] = []
    distinct = len(set(sizes))
    if distinct < 3:
        out.append(_hard("", f"构图单调:全片仅 {distinct} 种景别(需≥3)"))
    max_run = cur = 1
    for a, b in zip(sizes, sizes[1:]):
        cur = cur + 1 if a == b else 1
        max_run = max(max_run, cur)
    if max_run >= 4:
        out.append(_hard("", f"构图单调:连续 {max_run} 镜同景别(需<4)"))
    top_share = max(sizes.count(x) for x in set(sizes)) / n
    if top_share > 0.70:
        out.append(_hard("", f"构图单调:单一景别占比 {top_share:.0%}(需≤70%)"))
    return out


def _hard(sid, msg):
    return {"agent": "director_qa", "category": "hard_gate", "shot_id": sid, "message": msg}


__all__ = ["DirectorQAOfficer", "DirectorQAResult", "VERDICT_PASS", "VERDICT_REPAIR"]
