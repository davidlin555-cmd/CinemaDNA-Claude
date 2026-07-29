"""RelationshipContinuityAgent —— 锁定人物立场/权力关系 + 跨镜情绪连续。"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan

_POWER_BY_ROLE = (("债主", "高"), ("讨债", "高"), ("放贷", "高"), ("老板", "高"),
                  ("护士", "低"), ("欠", "低"), ("借", "低"))


class RelationshipContinuityAgent:
    name = "relationship"

    def refine(self, plan: DirectorMasterPlan) -> None:
        # 立场/权力：从角色 role/name 推定（缺则中性），锁进 cast_lock
        for cid, c in plan.cast_lock.items():
            blob = f"{c.get('role', '')}{c.get('name', '')}"
            if not c.get("power"):
                c["power"] = next((p for kw, p in _POWER_BY_ROLE if kw in blob), "中")
            if not c.get("stance"):
                c["stance"] = "施压方" if c["power"] == "高" else "承压方"

    def review(self, plan: DirectorMasterPlan) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        shots = plan.shots
        # 跨镜连续：每镜必须有 entry/exit state（防情绪无因果跳变）
        for s in shots:
            if not (s.entry_state or "").strip() or not (s.exit_state or "").strip():
                issues.append(_i("relationship", s.shot_id,
                                 "缺 entry/exit state（情绪易无因果跳变）"))
        # 权力关系必须锁定
        for cid, c in plan.cast_lock.items():
            if not c.get("power") or not c.get("stance"):
                issues.append(_i("relationship", "", f"人物 {cid} 立场/权力未锁定"))
        return issues


def _i(cat, sid, msg):
    return {"agent": "relationship", "category": cat, "shot_id": sid, "message": msg}


__all__ = ["RelationshipContinuityAgent"]
