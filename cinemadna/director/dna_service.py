"""DirectorDNAService —— 总导演模块编排：理解剧本 → 总谱 → 验收 → 锁定。

流程（任务书四·上游串行）：
  Step2 DIRECTOR_PLANNING: Showrunner 出总谱骨架 → 6 专业智能体 refine 各自领域
  Step3 DIRECTOR_QA:       验收官自检；不过 → 返回 REPAIR（只回导演，不进下游）
                           过 → plan.lock() 锁定 hash，成为唯一真源
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from director.master_plan import DirectorMasterPlan

from .agents.showrunner import ShowrunnerDirector
from .agents.director_qa import DirectorQAOfficer, DirectorQAResult


@dataclass
class DirectorPlanResult:
    plan: DirectorMasterPlan
    qa: DirectorQAResult

    @property
    def locked(self) -> bool:
        return self.plan.locked


class DirectorPlanningService:
    """1 总导演 + 6 专业智能体 + 1 验收官。"""

    def __init__(self) -> None:
        self.showrunner = ShowrunnerDirector()
        self.qa = DirectorQAOfficer()

    def plan(self, shooting_script: dict[str, Any], *, story_id: str
             ) -> DirectorPlanResult:
        # Step2: 总导演出骨架，专业智能体细化各自领域（仲裁由总导演的结构落定）
        master = self.showrunner.build_skeleton(shooting_script, story_id=story_id)
        for ag in self.qa.specialists:
            if hasattr(ag, "refine"):
                ag.refine(master)
        # Step3: 验收官自检
        result = self.qa.review(master)
        if result.passed:
            master.lock()                     # 通过才锁定 → 唯一真源
        return DirectorPlanResult(plan=master, qa=result)


__all__ = ["DirectorPlanningService", "DirectorPlanResult"]
