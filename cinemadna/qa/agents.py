"""镜头级 QA · 三个质检 Agent (Phase 4)

对应多智能体文档 §2.10 的 Vision / Performance / Continuity QA。

Phase 4 的质检是**规则驱动**的，不是多模态模型。这样做是刻意的：
先把"发现问题 → 定位模块 → 精准打回"这条通路建起来，
等真接了视觉模型，只要往同一个 `QAIssue` 列表里追加发现即可。

规则分两类，报告里分得很清楚：
- **结构性规则（真判定）**：文件在不在、时长对不对、参考 hash 有没有被换掉、
  台词有没有对应的表演节拍 —— 这些不接模型也能查，而且必须查
- **打分规则（mock）**：identity/scene/motion 分数目前是伪随机，
  只用于验证"低分会被打回"这条链路

每条问题都带 `target`（该打回哪个模块），这是 Repair Planner 的输入。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Final

from asset_brain.common import schemas
from asset_brain.common.bundle import Bundle
from director.shot_contract import SHOT_CLOSEUP, SHOT_REACTION

# ---------------------------------------------------------------------------
# 打回目标（= Pipeline 的阶段名，Repair Planner 直接拿来回退）
# ---------------------------------------------------------------------------

TARGET_ASSET_MATCHING: Final = "ASSET_MATCHING"
TARGET_DIRECTING: Final = "DIRECTING"
TARGET_PERFORMANCE: Final = "PERFORMANCE"
TARGET_RENDERING: Final = "RENDERING"

#: 打回代价排序：越靠前重跑的东西越多，所以要挑"最小必要模块"
TARGET_ORDER: Final[dict[str, int]] = {
    TARGET_ASSET_MATCHING: 0,
    TARGET_DIRECTING: 1,
    TARGET_PERFORMANCE: 2,
    TARGET_RENDERING: 3,
}

SEVERITY_CRITICAL: Final = "critical"   # 必须人工介入
SEVERITY_HIGH: Final = "high"           # 必须重跑
SEVERITY_MEDIUM: Final = "medium"       # 建议重跑
SEVERITY_LOW: Final = "low"             # 记录即可
SEVERITY_INFO: Final = "info"           # 只是说明，不算问题

SEVERITY_ORDER: Final[dict[str, int]] = {
    SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3, SEVERITY_INFO: 4,
}

#: 会触发打回的严重度
BLOCKING_SEVERITIES: Final[frozenset[str]] = frozenset(
    {SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM}
)

# 打分阈值（mock 分数用）
MIN_MOTION_QUALITY: Final = 0.60
EXPECTED_FPS: Final = 24


@dataclass
class QAIssue:
    """一条质检发现。`target` 决定它该打回哪个模块。"""

    shot_id: str
    code: str
    message: str
    severity: str
    target: str
    agent: str
    #: 该问题是结构性判定还是 mock 打分
    structural: bool = True
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _report(schema: str, agent: str, issues: list[QAIssue],
            checked: int) -> dict[str, Any]:
    blocking = [i for i in issues if i.severity in BLOCKING_SEVERITIES]
    return {
        "schema_version": schema,
        "agent": agent,
        "shots_checked": checked,
        "passed": not blocking,
        "issues": [i.to_dict() for i in issues],
        "blocking_count": len(blocking),
        "checked_at": schemas.utc_now_iso(),
    }


# ---------------------------------------------------------------------------
# Vision QA
# ---------------------------------------------------------------------------


def vision_qa(
    contracts: list[dict[str, Any]], *, bundle: Bundle | None = None
) -> dict[str, Any]:
    """画面质检：产物在不在、对不对、参考有没有被掉包、分数够不够。"""
    issues: list[QAIssue] = []

    for c in contracts:
        sid = c["shot_id"]
        r = c.get("render_result")
        if not r:
            issues.append(QAIssue(
                sid, "NO_RENDER_RESULT", "镜头没有渲染产物",
                SEVERITY_HIGH, TARGET_RENDERING, "vision",
            ))
            continue

        # 1) 文件是否真的落地，且真的是媒体
        #    （光查"文件在不在"不够：一份 .mock.json 也"在"）
        if r.get("is_real_media"):
            if bundle is None or not bundle.exists(r["file_relpath"]):
                issues.append(QAIssue(
                    sid, "MEDIA_FILE_MISSING",
                    f"声称已出片但文件不存在: {r['file_relpath']}",
                    SEVERITY_HIGH, TARGET_RENDERING, "vision",
                ))
            elif not str(r.get("media_type", "")).startswith(("video/", "image/")):
                issues.append(QAIssue(
                    sid, "MEDIA_TYPE_INVALID",
                    f"声称已出片但媒体类型是 {r.get('media_type')!r}，不是可用素材",
                    SEVERITY_HIGH, TARGET_RENDERING, "vision",
                ))
        else:
            issues.append(QAIssue(
                sid, "NOT_REAL_FOOTAGE",
                "产物不是真实画面（mock/占位），不能当成片素材",
                SEVERITY_INFO, TARGET_RENDERING, "vision",
                detail={"media_type": r.get("media_type")},
            ))
        if r.get("placeholder"):
            issues.append(QAIssue(
                sid, "PLACEHOLDER_FOOTAGE",
                "产物是占位色板，不是模型生成画面",
                SEVERITY_INFO, TARGET_RENDERING, "vision",
            ))

        # 2) 时长是否与合约一致（剪辑全靠它）
        if abs(float(r.get("duration_sec", 0)) - float(c["duration_sec"])) > 0.05:
            issues.append(QAIssue(
                sid, "DURATION_MISMATCH",
                f"产物时长 {r.get('duration_sec')}s 与合约 {c['duration_sec']}s 不符",
                SEVERITY_HIGH, TARGET_RENDERING, "vision",
            ))

        # 3) 参考资产有没有被掉包（一致性的根）
        expected = {
            "scene": c["assets"]["scene"]["asset_hash"],
            "characters": {x["character_id"]: x["asset_hash"]
                           for x in c["assets"]["characters"]},
            "props": {x["prop_id"]: x["asset_hash"] for x in c["assets"]["props"]},
        }
        if r.get("reference_hashes") != expected:
            issues.append(QAIssue(
                sid, "REFERENCE_MISMATCH",
                "产物记录的参考资产 hash 与合约不一致（参考没注进去或被掉包）",
                SEVERITY_HIGH, TARGET_RENDERING, "vision",
                detail={"expected": expected, "actual": r.get("reference_hashes")},
            ))

        # 4) 规格
        if int(r.get("fps", EXPECTED_FPS)) != EXPECTED_FPS:
            issues.append(QAIssue(
                sid, "FPS_MISMATCH", f"帧率 {r.get('fps')} 非预期 {EXPECTED_FPS}",
                SEVERITY_MEDIUM, TARGET_RENDERING, "vision",
            ))

        # 5) 打分（mock）
        m = r.get("metrics") or {}
        if m.get("motion_quality", 1.0) < MIN_MOTION_QUALITY:
            issues.append(QAIssue(
                sid, "LOW_MOTION_QUALITY",
                f"运动质量 {m.get('motion_quality')} < {MIN_MOTION_QUALITY}",
                SEVERITY_MEDIUM, TARGET_RENDERING, "vision",
                structural=False,
            ))

    return _report(schemas.SCHEMA_VISION_QA, "vision", issues, len(contracts))


# ---------------------------------------------------------------------------
# Performance QA
# ---------------------------------------------------------------------------


def performance_qa(contracts: list[dict[str, Any]]) -> dict[str, Any]:
    """表演质检：台词有没有人演、特写有没有微表情、强情绪有没有生理反应。"""
    issues: list[QAIssue] = []

    for c in contracts:
        sid = c["shot_id"]
        beats = (c.get("performance") or {}).get("acting_beats") or []
        if not beats:
            issues.append(QAIssue(
                sid, "NO_ACTING_BEATS", "镜头没有任何表演节拍，画面会是死的",
                SEVERITY_HIGH, TARGET_PERFORMANCE, "performance",
            ))
            continue

        # 每句台词都必须有对应角色的 LINE 节拍
        spoken = {(b["character_id"], b.get("cue")) for b in beats if b["type"] == "LINE"}
        for line in c.get("dialogue") or []:
            if (line["character_id"], line["line"]) not in spoken:
                issues.append(QAIssue(
                    sid, "DIALOGUE_WITHOUT_BEAT",
                    f"台词「{line['line']}」没有对应的表演节拍",
                    SEVERITY_HIGH, TARGET_PERFORMANCE, "performance",
                ))

        # 特写/反应镜必须有微表情设计，否则特写就白给了
        if c["shot_type"] in (SHOT_CLOSEUP, SHOT_REACTION):
            if not any((b.get("micro_expression") or {}).get("primary") for b in beats):
                issues.append(QAIssue(
                    sid, "CLOSEUP_WITHOUT_EXPRESSION",
                    "特写/反应镜没有微表情设计",
                    SEVERITY_MEDIUM, TARGET_PERFORMANCE, "performance",
                ))

        # 强情绪必须落到生理反应上
        if float(c["emotion"].get("intensity", 0)) >= 0.85:
            if not any((b.get("micro_expression") or {}).get("physiological")
                       for b in beats):
                issues.append(QAIssue(
                    sid, "HIGH_EMOTION_WITHOUT_PHYSIOLOGY",
                    f"情绪强度 {c['emotion']['intensity']} 却没有生理反应设计",
                    SEVERITY_MEDIUM, TARGET_PERFORMANCE, "performance",
                ))

        # 画面里的人必须都有戏
        in_frame = {x["character_id"] for x in c["assets"]["characters"]}
        acted = {b["character_id"] for b in beats}
        for missing in sorted(in_frame - acted):
            issues.append(QAIssue(
                sid, "ACTOR_WITHOUT_BEAT",
                f"人物 {missing} 在画面里却没有任何表演节拍",
                SEVERITY_MEDIUM, TARGET_PERFORMANCE, "performance",
            ))

    return _report(schemas.SCHEMA_PERFORMANCE_QA, "performance", issues, len(contracts))


# ---------------------------------------------------------------------------
# Continuity QA
# ---------------------------------------------------------------------------


def continuity_qa(
    contracts: list[dict[str, Any]],
    *,
    continuity_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """连续性质检：身份漂移、场景漂移、道具状态穿帮，以及产物与合约脱节。"""
    issues: list[QAIssue] = []

    # 1) 直接吃 Director 的连续性检查结论（它已经查过账本）
    for cat, target, sev in (
        ("identity_issues", TARGET_DIRECTING, SEVERITY_HIGH),
        ("scene_issues", TARGET_DIRECTING, SEVERITY_HIGH),
        ("prop_issues", TARGET_DIRECTING, SEVERITY_MEDIUM),
    ):
        for msg in (continuity_report or {}).get(cat, []):
            issues.append(QAIssue(
                "*", f"LEDGER_{cat.upper()}", msg, sev, target, "continuity",
            ))

    # 2) 渲染产物用的参考资产与合约是否一致（跨镜头一致性的实证）
    packs: dict[str, set[str]] = {}
    for c in contracts:
        r = c.get("render_result") or {}
        refs = (r.get("reference_hashes") or {}).get("characters") or {}
        for cid, h in refs.items():
            packs.setdefault(cid, set()).add(str(h))
    for cid, hashes in packs.items():
        if len(hashes) > 1:
            issues.append(QAIssue(
                "*", "RENDERED_IDENTITY_DRIFT",
                f"人物 {cid} 的渲染产物引用了 {len(hashes)} 个不同的身份资产",
                SEVERITY_CRITICAL, TARGET_ASSET_MATCHING, "continuity",
                detail={"hashes": sorted(hashes)},
            ))

    # 3) 道具状态：同一场戏内不得出现两种状态
    by_scene: dict[tuple[str, str], set[str]] = {}
    for c in contracts:
        for p in c["assets"]["props"]:
            by_scene.setdefault((c["scene_id"], p["prop_id"]), set()).add(p["state"])
    for (scene_id, pid), states in by_scene.items():
        if len(states) > 1:
            issues.append(QAIssue(
                "*", "PROP_STATE_SPLIT",
                f"道具 {pid} 在同一场戏 {scene_id} 内出现了多种状态 {sorted(states)}",
                SEVERITY_HIGH, TARGET_DIRECTING, "continuity",
            ))

    return _report(schemas.SCHEMA_CONTINUITY_QA, "continuity", issues, len(contracts))
