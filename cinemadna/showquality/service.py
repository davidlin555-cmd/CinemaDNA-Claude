"""ShowQualityDNA (Phase H) —— 轻量成片观感审核 + 精准打回。

从"结构合格"到"更像短剧"。规则驱动（不接多模态），只堵**明显**的观感问题：

1. 素材拼接感：景别单一 / 时长齐平 / 情绪拉平 / 对白全程无反应 → 像素材堆叠
2. 前 3–5 秒吸引力：开场没有钩子（冲突/强情绪/台词）
3. 节奏拖沓：连续长镜头 / 平均镜头过长
4. 表演基本情绪：强情绪镜头缺生理反应；对白缺语气

每条问题带 `target`（DIRECTING/PERFORMANCE/RENDERING）→ 精准打回对应模块。
本审核**只对明显问题判负**，正常生成的剧应当通过（阈值刻意宽松）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinemadna.asset_brain.common import schemas
from director.shot_contract import SHOT_REACTION

TARGET_DIRECTING = "DIRECTING"
TARGET_PERFORMANCE = "PERFORMANCE"
TARGET_RENDERING = "RENDERING"
_TARGET_ORDER = {TARGET_DIRECTING: 0, TARGET_PERFORMANCE: 1, TARGET_RENDERING: 2}

VERDICT_PASS = "PASS"
VERDICT_REPAIR = "REPAIR"

SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_INFO = "info"
_BLOCKING = frozenset({SEV_HIGH, SEV_MEDIUM})

#: 短剧节奏：单镜头超过它算偏长
_LONG_SHOT_SEC = 9.0
_HOOK_WINDOW_SEC = 5.0


def _issue(code: str, msg: str, sev: str, target: str,
           fix: str) -> dict[str, Any]:
    return {"code": code, "message": msg, "severity": sev, "target": target,
            "fix": fix, "agent": "showquality"}


@dataclass
class ShowQualityResult:
    story_id: str
    verdict: str
    score: float
    issues: list[dict[str, Any]] = field(default_factory=list)
    target_stage: str | None = None      # 打回目标（最上游的阻塞项）
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": "cinemadna.show_quality.v1",
            "story_id": self.story_id, "verdict": self.verdict,
            "passed": self.passed, "score": self.score,
            "issue_count": len(self.issues), "issues": self.issues,
            "target_stage": self.target_stage, "metrics": self.metrics,
            "checked_at": schemas.utc_now_iso(),
        }


def review_show_quality(
    contracts: list[dict[str, Any]], *, story_id: str,
    cut: dict[str, Any] | None = None,
) -> ShowQualityResult:
    """跑成片观感审核。返回带精准打回目标的报告。"""
    cs = sorted(contracts, key=lambda c: c.get("order", 0))
    issues: list[dict[str, Any]] = []
    n = len(cs)
    shot_types = [c.get("shot_type") for c in cs]
    durations = [float(c.get("duration_sec", 0)) for c in cs]
    intensities = [float((c.get("emotion") or {}).get("intensity", 0.5) or 0.5)
                   for c in cs]

    def all_beats(c):
        return (c.get("performance") or {}).get("acting_beats") or []

    has_dialogue = any(c.get("dialogue") for c in cs)
    reaction_beats = sum(
        1 for c in cs for b in all_beats(c) if b.get("type") == SHOT_REACTION.title()
        or b.get("type") == "REACTION")
    reaction_shots = sum(1 for t in shot_types if t == SHOT_REACTION)

    # --- 1. 素材拼接感 -----------------------------------------------------
    if n >= 4 and len(set(shot_types)) <= 1:
        issues.append(_issue("NO_SHOT_VARIETY",
            f"全片 {n} 个镜头景别单一（都是 {shot_types[0]}），像素材堆叠",
            SEV_HIGH, TARGET_DIRECTING, "混搭景别：特写/中景/反应/插入交替"))
    if n >= 4 and len({round(d, 1) for d in durations}) <= 1:
        issues.append(_issue("MONOTONE_DURATION",
            f"全片镜头时长齐平（都 ~{durations[0]:.1f}s），节奏呆板",
            SEV_MEDIUM, TARGET_DIRECTING, "长短镜错落：关键台词短切、情绪镜给长"))
    if n >= 4 and (max(intensities) - min(intensities)) < 0.12:
        issues.append(_issue("FLAT_EMOTION",
            "全片情绪几乎无起伏，缺戏剧张力",
            SEV_MEDIUM, TARGET_PERFORMANCE, "按情绪曲线做起伏：铺垫→冲突→爆发"))
    if has_dialogue and reaction_beats == 0 and reaction_shots == 0 and n >= 3:
        issues.append(_issue("NO_REACTION",
            "全程对白却无任何反应镜/反应节拍，像念稿",
            SEV_MEDIUM, TARGET_PERFORMANCE, "给听者补反应镜与反应节拍"))

    # --- 2. 前 3–5 秒吸引力 -----------------------------------------------
    opening, acc = [], 0.0
    for c, d in zip(cs, durations):
        if acc >= _HOOK_WINDOW_SEC:
            break
        opening.append(c)
        acc += d
    if opening:
        hook = any(
            float((c.get("emotion") or {}).get("intensity", 0.5) or 0.5) >= 0.5
            or c.get("dialogue")
            or str((c.get("emotion") or {}).get("beat", "")).upper()
            in ("CONFLICT", "CRISIS", "TWIST", "HOOK")
            for c in opening)
        if not hook:
            issues.append(_issue("WEAK_HOOK",
                "开场 3–5 秒没有钩子（无冲突/强情绪/台词），留不住人",
                SEV_HIGH, TARGET_DIRECTING, "把最有张力的一刻提到开场或加一句钩子台词"))

    # --- 3. 节奏拖沓 ------------------------------------------------------
    run_long = 0
    max_run_long = 0
    for d in durations:
        run_long = run_long + 1 if d > _LONG_SHOT_SEC else 0
        max_run_long = max(max_run_long, run_long)
    avg = sum(durations) / n if n else 0.0
    if max_run_long >= 3:
        issues.append(_issue("DRAGGING",
            f"连续 {max_run_long} 个长镜头（>{_LONG_SHOT_SEC:.0f}s），节奏拖沓",
            SEV_MEDIUM, TARGET_DIRECTING, "切碎长镜、插入反应/空镜提速"))
    elif n >= 4 and avg > _LONG_SHOT_SEC + 1:
        issues.append(_issue("SLOW_PACE",
            f"平均镜头 {avg:.1f}s 偏长，短剧应更快切",
            SEV_MEDIUM, TARGET_DIRECTING, "整体缩短镜头、加快剪切"))

    # --- 4. 表演基本情绪 -------------------------------------------------
    for c in cs:
        inten = float((c.get("emotion") or {}).get("intensity", 0.5) or 0.5)
        beats = all_beats(c)
        if inten >= 0.8 and beats:
            has_phys = any((b.get("micro_expression") or {}).get("physiological")
                           for b in beats)
            if not has_phys:
                issues.append(_issue("EMOTION_MISSING",
                    f"{c['shot_id']} 情绪强({inten:.2f})却无生理反应，表演空",
                    SEV_MEDIUM, TARGET_PERFORMANCE, "强情绪镜补生理反应（呼吸/吞咽/瞳孔）"))
                break
        # 对白缺语气（表演导演应已挂 tone）
        if c.get("dialogue"):
            line_beats = [b for b in beats if b.get("type") == "LINE"]
            if line_beats and not any(b.get("tone") for b in line_beats):
                issues.append(_issue("NO_TONE",
                    f"{c['shot_id']} 对白没有语气设计，念稿感",
                    SEV_INFO, TARGET_PERFORMANCE, "对白语气 Agent 给每句定语气"))
                break

    # --- 汇总 ------------------------------------------------------------
    blocking = [i for i in issues if i["severity"] in _BLOCKING]
    score = round(max(0.0, 1.0 - 0.18 * len(blocking) - 0.04 *
                      (len(issues) - len(blocking))), 3)
    verdict = VERDICT_REPAIR if blocking else VERDICT_PASS
    target = None
    if blocking:
        target = min((i["target"] for i in blocking),
                     key=lambda t: _TARGET_ORDER.get(t, 9))
    return ShowQualityResult(
        story_id=story_id, verdict=verdict, score=score, issues=issues,
        target_stage=target,
        metrics={"shots": n, "shot_types": len(set(shot_types)),
                 "avg_duration": round(avg, 2),
                 "emotion_range": round(max(intensities) - min(intensities), 3)
                 if intensities else 0.0,
                 "reaction_beats": reaction_beats, "blocking": len(blocking)})


class ShowQualityDNAService:
    def __init__(self, bundle=None) -> None:
        self.bundle = bundle

    def review(self, contracts, *, story_id, cut=None) -> ShowQualityResult:
        result = review_show_quality(contracts, story_id=story_id, cut=cut)
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/show_quality/{story_id}.json", result.report())
        return result
