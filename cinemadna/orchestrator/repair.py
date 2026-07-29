"""自动修复策略 (Phase D) —— 把「BLOCKED 原因 → 负责智能体 → 修复动作 → 重试上限」
编码成一张表，让工厂自愈：除 3 个人工闸门外，其余 Gate 失败都是自修复触发器。

理念（用户明确要求）：
  - 人工审核只保留 3 个点：剧本审核 / 人脸·角色审核 / 最终成片审核
  - 其它所有 Gate/BLOCKED 都不是终态，而是**自动打回对应模块重做**
  - 硬门标准一律不降；红线（单一真人克隆）永不自动绕过，直接升级人脸审核
  - 修不动（超过重试上限）才升级到最近的人工点，或按类别降级/判失败

驱动器（PipelineOrchestrator.run_until_quiescent）读这张表执行修复。
"""

from __future__ import annotations

from dataclasses import dataclass

from .story import StoryStage

# ── 3 个人工闸门（白名单）。pipeline 只允许用这三个理由挂人工 ──────────────
SCRIPT_REVIEW = "script_gate3"          # 剧本审核（沿用既有 reason）
IDENTITY_REVIEW = "identity_review"     # 人脸/角色审核
FINAL_CUT_REVIEW = "final_review_critical"  # 最终成片审核（沿用既有 reason）
HUMAN_GATES = frozenset({SCRIPT_REVIEW, IDENTITY_REVIEW, FINAL_CUT_REVIEW})

# ── 升级目标（重试耗尽后的去向）────────────────────────────────────────────
ESC_IDENTITY = IDENTITY_REVIEW          # 升级到人脸审核
ESC_FINAL = FINAL_CUT_REVIEW            # 升级到成片审核
ESC_FAIL = "FAILED"                     # 判技术失败（纯资产缺口，无对应人工点）
ESC_DEGRADE = "DEGRADE"                 # 降级占位继续（场景/道具真实图出不来，不停厂）


@dataclass(frozen=True)
class RepairAction:
    """一条修复动作：打回到哪个阶段、最多重试几次、耗尽后去哪。"""

    code: str
    target_stage: str        # repair_to 目标（StoryStage 值）；"" = 用 plan 里的 target
    max_attempts: int
    escalation: str          # ESC_*
    agent: str
    note: str = ""


_AM = StoryStage.ASSET_MATCHING.value
_DIR = StoryStage.DIRECTING.value
_PERF = StoryStage.PERFORMANCE.value
_REN = StoryStage.RENDERING.value

# ── 修复码 → 动作（对应设计文档 R1–R15）───────────────────────────────────
POLICY: dict[str, RepairAction] = {
    # R1/R4/R5：缺场景/道具/软判负 → 重生成对应资产。
    # 重试仍不过（如需求本身不可满足）→ 判该故事失败（不拖垮其它并行故事）。
    "ASSET_REGEN": RepairAction("ASSET_REGEN", _AM, 2, ESC_FAIL,
                                "SceneDNA/PropDNA", "重生成缺失或判负的资产并回流"),
    "SCENE_FIX": RepairAction("SCENE_FIX", _AM, 2, ESC_FAIL,
                              "SceneDNA", "补真实场景图并回流"),
    "PROP_FIX": RepairAction("PROP_FIX", _AM, 2, ESC_FAIL,
                             "PropDNA", "补真实道具图（屏幕→Chrome 模板 / 物理→FLUX）"),
    # R2：身份红线 —— 禁止自动绕过，直接升级人脸审核
    "IDENTITY_REDLINE": RepairAction("IDENTITY_REDLINE", "", 0, ESC_IDENTITY,
                                     "IdentityDNA", "红线命中（单一真人克隆），禁止自动绕过"),
    # R6：缺真实人脸 → 生成后仍需进人脸审核
    "IDENTITY_MISSING": RepairAction("IDENTITY_MISSING", _AM, 1, ESC_IDENTITY,
                                     "IdentityDNA", "生成真实人脸；生成后进人脸审核"),
    # R3：跨镜身份漂移 → 重绑同一 Master Pack
    "CONTINUITY": RepairAction("CONTINUITY", _DIR, 2, ESC_IDENTITY,
                               "DirectorDNA", "重绑同一 Master Pack，重签合约"),
    # R7：表演节拍缺失
    "PERFORMANCE_FIX": RepairAction("PERFORMANCE_FIX", _PERF, 2, ESC_FAIL,
                                    "PerformanceDNA", "重跑表演节拍"),
    # R8：声音/口型（AudioDNA 未建 → 桩：结构可修，媒体合成待 Audio）
    "AUDIO_FIX": RepairAction("AUDIO_FIX", _PERF, 2, ESC_FAIL,
                              "AudioDNA(stub)", "结构性修复声音性别/口型；媒体合成待 AudioDNA"),
    # R9：机位/路由/qa 子合约不全
    "DIRECTOR_FIX": RepairAction("DIRECTOR_FIX", _DIR, 2, ESC_FAIL,
                                 "DirectorDNA", "重新 enrich 机位/路由/qa 子合约"),
    # R10：渲染后端失败 → 重试/降级
    "RENDER_RETRY": RepairAction("RENDER_RETRY", _REN, 3, ESC_FINAL,
                                 "RenderBrain", "按 RetryPolicy 重试/云端降级本地"),
    # R11：合约非法/被篡改 → 重签
    "RENDER_RESIGN": RepairAction("RENDER_RESIGN", _DIR, 1, ESC_FAIL,
                                  "DirectorDNA", "合约非法，重签后重跑 PreRender"),
    # R12/R13：一致性/镜头级 QA → 精准打回（target 用 QA plan）
    "QA_REPAIR": RepairAction("QA_REPAIR", "", 2, ESC_FINAL,
                              "RepairPlanner", "镜头级 QA 精准打回最小模块"),
    # R15：成片总审驳回 → 按 plan 打回
    "FINAL_REPAIR": RepairAction("FINAL_REPAIR", "", 2, ESC_FINAL,
                                 "RepairPlanner", "成片总审驳回，按维度精准打回"),
    # 结构达标但商业清单未过（PASS_STRUCTURAL）→ 精准打回清单失败项；修不动转成片人工终确认
    "COMMERCIAL_FIX": RepairAction("COMMERCIAL_FIX", "", 2, ESC_FINAL,
                                   "FinalReview", "商业可发布清单未过，精准打回对应模块"),
    # 叙事连贯硬门未过（渲染前）→ 重排分镜/重挂叙事；修不动转成片人工终确认
    "NARRATIVE_FIX": RepairAction("NARRATIVE_FIX", _DIR, 2, ESC_FINAL,
                                  "DirectorDNA", "叙事连贯未过，重排分镜/重挂 story_function"),
}

# ── Cross-Model Validation code → 修复码 ───────────────────────────────────
CV_CODE_TO_REPAIR: dict[str, str] = {
    "SCENE_NOT_GATED": "SCENE_FIX",
    "REAL_SCENE_MISSING": "SCENE_FIX",
    "NO_REAL_SCENE": "SCENE_FIX",
    "PROP_NOT_GATED": "PROP_FIX",
    "PROP_NO_STATE": "PROP_FIX",
    "REAL_PROP_MISSING": "PROP_FIX",
    "NO_REAL_PROP": "PROP_FIX",
    "IDENTITY_NOT_GATED": "IDENTITY_MISSING",
    "REAL_FACE_MISSING": "IDENTITY_MISSING",
    "NO_REAL_FACE": "IDENTITY_MISSING",
    "REAL_FACE_INVALID": "IDENTITY_MISSING",
    "NO_MASTER_PACK": "CONTINUITY",
    "IDENTITY_DRIFT": "CONTINUITY",
    "MOUTH_POLICY": "AUDIO_FIX",
    "SPEAKER_NOT_IN_FRAME": "AUDIO_FIX",
    "VOICE_GENDER_MISMATCH": "AUDIO_FIX",
    "VOICED_NO_AUDIO": "AUDIO_FIX",
    "SILENT_HAS_AUDIO": "AUDIO_FIX",
    "NO_PERFORMANCE_FOR_PROP": "PERFORMANCE_FIX",
}

# ── PreRender 12 项就绪 flag → 修复码 ──────────────────────────────────────
FLAG_TO_REPAIR: dict[str, str] = {
    "script_contract_ready": SCRIPT_REVIEW,       # 剧本没过 → 人工点 1
    "scene_contract_ready": "SCENE_FIX",
    "prop_contract_ready": "PROP_FIX",
    "identity_contract_ready": "IDENTITY_MISSING",
    "performance_contract_ready": "PERFORMANCE_FIX",
    "dialogue_contract_ready": "AUDIO_FIX",
    "voice_contract_ready": "AUDIO_FIX",
    "mouth_policy_ready": "AUDIO_FIX",
    "camera_contract_ready": "DIRECTOR_FIX",
    "route_contract_ready": "DIRECTOR_FIX",
    "qa_contract_ready": "DIRECTOR_FIX",
    # cross_model_validation_ready 走 CV code 细分，不在此
}

#: 修复码优先级（一次失败可能命中多码，按最上游先修）
_PRIORITY = ["IDENTITY_REDLINE", "IDENTITY_MISSING", "SCENE_FIX", "PROP_FIX",
             "CONTINUITY", "PERFORMANCE_FIX", "AUDIO_FIX", "DIRECTOR_FIX",
             "ASSET_REGEN", "RENDER_RESIGN", "RENDER_RETRY", "QA_REPAIR",
             "FINAL_REPAIR"]


def pick_primary(codes: set[str]) -> str | None:
    """多码并发时挑最上游的一个先修（修完重跑门会重新暴露其余问题）。"""
    for c in _PRIORITY:
        if c in codes:
            return c
    return next(iter(codes), None)


def codes_from_prerender(report: dict) -> set[str]:
    """把一次 PreRender Gate 报告翻译成修复码集合。"""
    codes: set[str] = set()
    # 合约因**渲染失败**被标 FAILED（如预算耗尽/后端失败）→ 是渲染问题，不是场景问题。
    # 否则会被误判成 SCENE_FIX 反复回滚资产、空烧预算（真实故障复盘）。
    render_failed = any(
        "合约状态为 FAILED" in str(b.get("reason", ""))
        for b in (report.get("blockers") or []))
    for flag, status in (report.get("flags") or {}).items():
        if status != "READY" and flag in FLAG_TO_REPAIR:
            if flag == "scene_contract_ready" and render_failed:
                codes.add("RENDER_RESIGN")   # 重签合约(复位 FAILED)再重跑，不打回场景
            else:
                codes.add(FLAG_TO_REPAIR[flag])
    for issue in (report.get("cross_validation") or {}).get("issues") or []:
        c = CV_CODE_TO_REPAIR.get(issue.get("code"))
        if c:
            codes.add(c)
    return codes


__all__ = [
    "SCRIPT_REVIEW", "IDENTITY_REVIEW", "FINAL_CUT_REVIEW", "HUMAN_GATES",
    "ESC_IDENTITY", "ESC_FINAL", "ESC_FAIL", "ESC_DEGRADE",
    "RepairAction", "POLICY", "CV_CODE_TO_REPAIR", "FLAG_TO_REPAIR",
    "pick_primary", "codes_from_prerender",
]
