"""各模块报告 → 统一 ReviewResult 的适配器 (Phase F)。

不重跑任何审核，只把每个模块**已经产出**的报告翻译成统一结论。缺报告 → PENDING。
"""

from __future__ import annotations

from typing import Any

from .result import (
    VERDICT_CONFIRM,
    VERDICT_PASS,
    VERDICT_PENDING,
    VERDICT_REPAIR,
    ReviewResult,
    _mk,
)


def _issue(code: str, message: str, severity: str = "high") -> dict[str, Any]:
    return {"code": code, "message": message, "severity": severity}


def from_script(script_summary: dict[str, Any] | None) -> ReviewResult:
    """Script：ScriptBrain Gate3（结构/钩子/冲突/可拍摄性）。"""
    if not script_summary:
        return _mk("script", VERDICT_PENDING)
    issues = [_issue("SCRIPT_GATE3", m)
              for m in (script_summary.get("gate3_issues") or [])]
    if script_summary.get("needs_human_review"):
        # 未过 = 打回改剧本（这里由人在终确认处决定；专业审核已标问题）
        return _mk("script", VERDICT_REPAIR, issues)
    # 过了专业审核 → 等剧本终确认（人工点 1）
    return _mk("script", VERDICT_CONFIRM, issues)


def from_asset_gates(
    module: str, gate_reports: list[dict[str, Any]]
) -> ReviewResult:
    """Scene / Identity / Prop：聚合该类资产的所有 Gate 报告。

    - 有硬拦截（红线）→ REPAIR（红线走 IDENTITY_REDLINE 升级，不在此放行）
    - 有软问题未过 → REPAIR
    - 需人工复核（identity 真实人脸）→ CONFIRM
    - 全过 → PASS
    """
    if not gate_reports:
        return _mk(module, VERDICT_PENDING)
    issues: list[dict[str, Any]] = []
    any_hard = any(g.get("hard_blocks") for g in gate_reports)
    any_fail = any(not g.get("passed") for g in gate_reports)
    any_review = any(g.get("human_review_required") for g in gate_reports)
    for g in gate_reports:
        for b in g.get("hard_blocks") or []:
            issues.append(_issue("HARD_BLOCK", str(b), "critical"))
        for m in g.get("issues") or []:
            issues.append(_issue("GATE_ISSUE", str(m)))
    if any_hard or any_fail:
        return _mk(module, VERDICT_REPAIR, issues)
    if any_review and module == "identity":
        return _mk("identity", VERDICT_CONFIRM, issues)  # 人脸终确认
    return _mk(module, VERDICT_PASS, issues)


def from_continuity(continuity_report: dict[str, Any] | None) -> ReviewResult:
    """Director：连续性/分镜功能审核。"""
    if not continuity_report:
        return _mk("director", VERDICT_PENDING)
    issues = []
    for cat in ("identity_issues", "scene_issues", "prop_issues"):
        for m in continuity_report.get(cat) or []:
            issues.append(_issue(cat.upper(), str(m)))
    verdict = VERDICT_PASS if continuity_report.get("passed", not issues) and not issues \
        else VERDICT_REPAIR
    return _mk("director", verdict, issues)


def from_qa_report(
    module: str, qa_report: dict[str, Any] | None
) -> ReviewResult:
    """Performance / Render：复用镜头级 QA 的单维报告（performance / vision）。"""
    if not qa_report:
        return _mk(module, VERDICT_PENDING)
    issues = [_issue(i.get("code", "QA"), i.get("message", ""),
                     i.get("severity", "high"))
              for i in qa_report.get("issues") or []
              if i.get("severity") in ("critical", "high", "medium")]
    verdict = VERDICT_PASS if qa_report.get("passed") else VERDICT_REPAIR
    return _mk(module, verdict, issues)


def from_audio(audio_report: dict[str, Any] | None) -> ReviewResult:
    """Audio：声音性别 / 口型 / 字幕。"""
    if not audio_report:
        return _mk("audio", VERDICT_PENDING)
    issues = [_issue(i.get("code", "AUDIO"), i.get("message", ""))
              for i in audio_report.get("issues") or []]
    verdict = VERDICT_PASS if audio_report.get("passed") else VERDICT_REPAIR
    return _mk("audio", verdict, issues)


def from_final(final_report: dict[str, Any] | None) -> ReviewResult:
    """Final：成片五维总审。通过→等成片终确认（人工点 3）。"""
    if not final_report:
        return _mk("final", VERDICT_PENDING)
    issues = [_issue(i.get("code", "FINAL"), i.get("message", str(i)),
                     i.get("severity", "high"))
              for i in final_report.get("blocking_issues") or []]
    verdict = final_report.get("verdict", "")
    if verdict in ("PASS_FULL", "PASS_STRUCTURAL"):
        return _mk("final", VERDICT_CONFIRM, issues)
    if verdict == "HUMAN":
        return _mk("final", VERDICT_CONFIRM, issues)
    return _mk("final", VERDICT_REPAIR, issues)
