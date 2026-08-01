"""ActionPerformanceIntentAgent —— 每镜可执行动作节拍（禁止只给 emotion 词）。"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan

#: 纯情绪词（不可执行），出现在 body_action 里即判不合格
_EMOTION_ONLY = ("绝望", "愤怒", "紧张", "恐惧", "焦虑", "压抑", "悲伤", "冷漠",
                 "决绝", "屈辱", "无助", "平静", "开心", "轻松", "威胁", "傲慢")
_PLACEHOLDER = ("待导演", "自然站姿", "沉默中的动作", "（无", "缓慢转身")


class ActionPerformanceIntentAgent:
    name = "action"

    def refine(self, plan: DirectorMasterPlan) -> None:
        # 支持镜内多状态：把 internal_shift 落成 micro_beats（看→反应→决定）
        for s in plan.shots:
            for ab in s.action_beats:
                if not ab.get("micro_beats") and s.internal_shift:
                    ab["micro_beats"] = [x for x in s.internal_shift.split("→") if x][:3]

    def review(self, plan: DirectorMasterPlan) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for s in plan.shots:
            abs_ = s.action_beats or []
            if not abs_:
                issues.append(_i(s.shot_id, "无 action_beats（动作不可执行）"))
                continue
            for ab in abs_:
                body = (ab.get("body_action") or "").strip()
                if not body:
                    issues.append(_i(s.shot_id, "action_beat 缺 body_action"))
                elif any(ph in body for ph in _PLACEHOLDER):
                    issues.append(_i(s.shot_id, f"动作是占位/通用（{body[:16]}），需可执行具体动作"))
                elif body in _EMOTION_ONLY or (len(body) <= 4 and body in _EMOTION_ONLY):
                    issues.append(_i(s.shot_id, f"body_action 只有情绪词（{body}），需具体动作"))
        return issues


def _i(sid, msg):
    return {"agent": "action", "category": "action", "shot_id": sid, "message": msg}


__all__ = ["ActionPerformanceIntentAgent"]
