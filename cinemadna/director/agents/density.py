"""CommercialDensityAgent —— 商业密度：5秒钩子/每5秒推进/反凑秒/反空镜/反复读。"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan

_PROGRESS_FUNCS = ("钩子", "施压", "揭示", "反转", "决策", "收束")


class CommercialDensityAgent:
    name = "density"

    def review(self, plan: DirectorMasterPlan) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        shots = plan.shots
        if not shots:
            return [_i("", "无镜头")]
        # 1) 5 秒钩子：开头累计 5s 内必须有钩子功能镜
        cum, hooked = 0.0, False
        for s in shots:
            if s.story_function == "钩子":
                hooked = True
            cum += float(s.camera.get("duration_sec") or 3)
            if cum >= 5.0:
                break
        if not hooked:
            issues.append(_i(shots[0].shot_id, "前5秒无钩子镜"))
        # 2) 每 ≤5 秒必须有一次推进（不能连续空镜/复读）
        run = 0.0
        for s in shots:
            if s.story_function in _PROGRESS_FUNCS or s.internal_shift:
                run = 0.0
            else:
                run += float(s.camera.get("duration_sec") or 3)
                if run > 5.0:
                    issues.append(_i(s.shot_id, "连续 >5s 无推进（空镜/凑秒）"))
                    run = 0.0
        # 3) 反复读：同一 story_function 连续 ≥3 镜 = 功能复读
        streak, prev = 1, None
        for s in shots:
            if s.story_function == prev:
                streak += 1
                if streak >= 3:
                    issues.append(_i(s.shot_id, f"功能复读：连续 {streak} 镜都是「{s.story_function}」"))
            else:
                streak, prev = 1, s.story_function
        # 4) 反凑秒：单镜 >5s
        for s in shots:
            if float(s.camera.get("duration_sec") or 3) > 5.0:
                issues.append(_i(s.shot_id, "单镜 >5s，凑秒"))
        return issues


def _i(sid, msg):
    return {"agent": "density", "category": "density", "shot_id": sid, "message": msg}


__all__ = ["CommercialDensityAgent"]
