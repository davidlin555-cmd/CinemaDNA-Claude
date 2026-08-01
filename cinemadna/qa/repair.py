"""Repair Planner —— 根因分析与**精准打回** (Phase 4)

对应多智能体文档 §2.10 的 Repair Planner：

    「女主表情呆滞 → 打回 PerformanceDNA 重写 Acting Beat」，
    而不是重跑全剧。

核心是两条规则：

1. **打回最小必要模块**。每条 QA 问题自带 `target`；一批问题里取
   **代价最大的那个**（阶段最靠前的），因为重跑靠前的阶段会顺带重跑后面的，
   反过来不成立。例如同时有"表演不到位"和"身份漂移"，只能从
   ASSET_MATCHING 重来 —— 光重写表演救不了漂移。
2. **范围最小化**。只列出真正出问题的镜头，其余镜头的产物应当保留复用。

`critical` 级问题不自动打回，转人工 —— 机器已经判断不了了。
"""

from __future__ import annotations

from typing import Any

from asset_brain.common import schemas

from .agents import (
    BLOCKING_SEVERITIES,
    SEVERITY_CRITICAL,
    SEVERITY_ORDER,
    TARGET_ORDER,
)

VERDICT_PASS = "PASS"
VERDICT_REPAIR = "REPAIR"
VERDICT_HUMAN = "HUMAN"


def plan_repair(
    issues: list[dict[str, Any]], *, story_id: str, total_shots: int
) -> dict[str, Any]:
    """由 QA 问题列表推导修复计划。"""
    blocking = [i for i in issues if i["severity"] in BLOCKING_SEVERITIES]
    critical = [i for i in blocking if i["severity"] == SEVERITY_CRITICAL]

    if not blocking:
        verdict = VERDICT_PASS
    elif critical:
        verdict = VERDICT_HUMAN
    else:
        verdict = VERDICT_REPAIR

    # 打回目标：取阶段最靠前的（代价最大的那个决定了从哪重来）
    target = None
    if blocking:
        target = min(
            (i["target"] for i in blocking), key=lambda t: TARGET_ORDER.get(t, 99)
        )

    affected = sorted({i["shot_id"] for i in blocking if i["shot_id"] != "*"})
    story_wide = any(i["shot_id"] == "*" for i in blocking)

    # 根因分组：同一 code 的问题归成一条，便于人看
    by_code: dict[str, dict[str, Any]] = {}
    for i in blocking:
        row = by_code.setdefault(
            i["code"],
            {"code": i["code"], "target": i["target"], "severity": i["severity"],
             "count": 0, "shots": [], "example": i["message"]},
        )
        row["count"] += 1
        if i["shot_id"] not in row["shots"]:
            row["shots"].append(i["shot_id"])
        # 保留最严重的那档
        if SEVERITY_ORDER[i["severity"]] < SEVERITY_ORDER[row["severity"]]:
            row["severity"] = i["severity"]

    return {
        "schema_version": schemas.SCHEMA_REPAIR_PLAN,
        "story_id": story_id,
        "verdict": verdict,
        "target_stage": target,
        "reason": _reason(verdict, target, by_code),
        "affected_shots": affected,
        "story_wide": story_wide,
        "scope": "STORY" if story_wide else "SHOTS",
        "shots_total": total_shots,
        "shots_to_redo": total_shots if story_wide else len(affected),
        "root_causes": sorted(
            by_code.values(), key=lambda r: SEVERITY_ORDER[r["severity"]]
        ),
        "blocking_issues": len(blocking),
        "planned_at": schemas.utc_now_iso(),
    }


def _reason(verdict: str, target: str | None, by_code: dict[str, Any]) -> str:
    if verdict == VERDICT_PASS:
        return "镜头级质检全部通过"
    codes = "、".join(sorted(by_code)) or "未知"
    if verdict == VERDICT_HUMAN:
        return f"存在 critical 级问题（{codes}），机器无法自动修复，需人工介入"
    return f"打回 {target} 重跑：{codes}"
