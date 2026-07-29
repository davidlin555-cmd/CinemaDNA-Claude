"""PropInformationAgent —— 关键道具清单/状态/接触方式；信息镜必须绑反应/决策。"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan

#: 信息镜之后（含本镜）必须出现的功能，代表"看到信息 → 有人反应/决策"
_REACT_FUNCS = ("反应", "决策", "反转", "施压")


class PropInformationAgent:
    name = "prop_info"

    def refine(self, plan: DirectorMasterPlan) -> None:
        # 把道具计划里的状态/接触铺到相关信息镜
        prop_ids = [p["prop_id"] for p in plan.prop_plan if p.get("prop_id")]
        state_of = {p["prop_id"]: p.get("state", "") for p in plan.prop_plan}
        if not prop_ids:
            return
        pi = 0
        for s in plan.shots:
            if s.story_function == "揭示" and pi < len(prop_ids) and not s.prop_usage:
                pid = prop_ids[pi]; pi += 1
                s.prop_usage = {"prop_id": pid, "state": state_of.get(pid, ""),
                                "contact": "人物手持凑近镜头查看"}

    def review(self, plan: DirectorMasterPlan) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        shots = plan.shots
        for i, s in enumerate(shots):
            if s.prop_usage.get("prop_id"):
                if not s.prop_usage.get("state"):
                    issues.append(_i(s.shot_id, "道具镜缺状态锁定"))
                # 信息镜必须绑定反应/决策：本镜或紧邻后一镜要是反应/决策/反转类
                window = shots[i:i + 2]
                if not any(w.story_function in _REACT_FUNCS for w in window):
                    issues.append(_i(s.shot_id,
                                     "信息镜未绑定人物反应/决策（信息镜不能空转）"))
        return issues


def _i(sid, msg):
    return {"agent": "prop_info", "category": "prop_info", "shot_id": sid, "message": msg}


__all__ = ["PropInformationAgent"]
