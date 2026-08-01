"""成片级 Final Review · 五维检查 (Phase 6)

对应架构复盘 §9（QA Council 收敛）。把 ChatGPT 的 14 个 Agent 收敛成 5 个维度，
**不新建 14 个模块**。每个维度分两 tier：

- **确定性 tier（结构性）**：读现成报告即可判定，硬门，**现在就能做，零新模型**。
- **判断 tier（多模态/LLM）**：真正"像不像商业短剧"，需 Audio + 多模态裁判，
  未接入时标 `PENDING`——**绝不因为没检查就当 PASS**。

每条发现复用 `qa.agents.QAIssue`（带 severity + target），直接喂 Repair Planner。
"""

from __future__ import annotations

from typing import Any

from director.shot_contract import (
    SHOT_CLOSEUP,
    SHOT_REACTION,
    check_mouth_policy,
)
from qa.agents import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    SEVERITY_MEDIUM,
    TARGET_ASSET_MATCHING,
    TARGET_DIRECTING,
    TARGET_PERFORMANCE,
    TARGET_RENDERING,
    QAIssue,
)
from render.assembly import TARGET_MAX_SEC, TARGET_MIN_SEC
from scriptbrain.agents import market_hook_critic, script_critic
from scriptbrain.library import BEAT_CLIFFHANGER, BEAT_HOOK, REQUIRED_BEATS

# 打回目标里没有"POST/ScriptBrain"这些阶段名，用最接近的可执行 target。
# 叙事/剧本类问题打回 DIRECTING（重排镜头）或标人工（改剧本超出自动能力）。
TARGET_SCRIPT = TARGET_DIRECTING

MIN_RETENTION = 0.60
MIN_IDENTITY_CONSISTENCY_AVG = 0.80
MIN_IDENTITY_CONSISTENCY_ANY = 0.75
#: 景别多样性：一场戏不能全是同一景别（"素材堆叠"）
MIN_DISTINCT_SHOT_TYPES = 2


def _finding(dim: str, tier: str) -> dict[str, Any]:
    return {"dimension": dim, "tier": tier, "issues": [], "pending": []}


def _pending(dim: str, what: str) -> dict[str, Any]:
    """判断 tier 未接入时的占位项——记录"还没检查什么"，绝不算通过。"""
    return {"code": "PENDING", "message": f"{what}（需判断层模型，尚未接入）",
            "needs": what}


# ---------------------------------------------------------------------------
# 维度 A · 叙事与剧本
# ---------------------------------------------------------------------------


def dim_narrative(ctx: dict[str, Any]) -> dict[str, Any]:
    out = _finding("A_narrative", "mixed")
    issues: list[QAIssue] = []
    script = ctx.get("shooting_script") or {}

    # 【现成】每集齐 钩子/冲突/悬念
    for ep in script.get("episodes") or []:
        beats = {s.get("beat_type") for s in ep.get("scenes") or []}
        missing = REQUIRED_BEATS - beats
        if missing:
            issues.append(QAIssue(
                "*", "MISSING_BEATS",
                f"{ep.get('episode_id')} 缺节拍 {sorted(missing)}",
                SEVERITY_HIGH, TARGET_SCRIPT, "final_review.narrative"))

    # 【现成】剧本逻辑/连续性/可拍摄性（复用 script_critic 纯函数）
    if script:
        review = script_critic(script)
        for msg in review.get("logic_issues", []):
            issues.append(QAIssue("*", "SCRIPT_LOGIC", msg, SEVERITY_HIGH,
                                  TARGET_SCRIPT, "final_review.narrative"))
        if review.get("production_difficulty", {}).get("level") == "hard":
            for msg in review["production_difficulty"]["hard_scenes"]:
                issues.append(QAIssue("*", "PRODUCTION_TOO_HARD", msg,
                                      SEVERITY_MEDIUM, TARGET_SCRIPT,
                                      "final_review.narrative"))

    # 【需多模态/LLM】对白自然度、每句对白对应画面
    out["pending"] = [_pending("A", "对白自然度（像真人不像旁白）"),
                      _pending("A", "每句对白与画面对齐")]
    out["issues"] = [i.to_dict() for i in issues]
    return out


# ---------------------------------------------------------------------------
# 维度 B · 导演与节奏
# ---------------------------------------------------------------------------


def dim_direction(ctx: dict[str, Any]) -> dict[str, Any]:
    out = _finding("B_direction", "mixed")
    issues: list[QAIssue] = []
    contracts = sorted(ctx.get("contracts") or [], key=lambda c: c["order"])
    cut = ctx.get("rough_cut") or {}

    if contracts:
        # 【现成】首镜是钩子、末镜是悬念
        if contracts[0]["emotion"].get("beat") != BEAT_HOOK:
            issues.append(QAIssue(contracts[0]["shot_id"], "OPENING_NOT_HOOK",
                "开场镜头不是钩子", SEVERITY_MEDIUM, TARGET_DIRECTING,
                "final_review.direction"))
        if contracts[-1]["emotion"].get("beat") != BEAT_CLIFFHANGER:
            issues.append(QAIssue(contracts[-1]["shot_id"], "ENDING_NOT_CLIFF",
                "结尾镜头不是悬念", SEVERITY_MEDIUM, TARGET_DIRECTING,
                "final_review.direction"))
        # 【现成】景别有搭配（防"素材堆叠"）
        shot_types = {c["shot_type"] for c in contracts}
        if len(contracts) >= 3 and len(shot_types) < MIN_DISTINCT_SHOT_TYPES:
            issues.append(QAIssue("*", "MONOTONE_SHOTS",
                f"全片只有 {len(shot_types)} 种景别，像素材堆叠",
                SEVERITY_MEDIUM, TARGET_DIRECTING, "final_review.direction"))

    # 【现成】无缺失镜头
    for sid in cut.get("missing_shots") or []:
        issues.append(QAIssue(sid, "SHOT_MISSING", f"{sid} 未渲染，成片缺镜",
            SEVERITY_HIGH, TARGET_RENDERING, "final_review.direction"))

    # 【现成】总时长达标
    total = cut.get("total_duration_sec", 0)
    if total and not (TARGET_MIN_SEC <= total <= TARGET_MAX_SEC):
        issues.append(QAIssue("*", "DURATION_OUT_OF_RANGE",
            f"总时长 {total}s 不在 {TARGET_MIN_SEC}–{TARGET_MAX_SEC}s",
            SEVERITY_MEDIUM, TARGET_DIRECTING, "final_review.direction"))

    out["pending"] = [_pending("B", "剪辑节奏是否拖沓、情绪递进")]
    out["issues"] = [i.to_dict() for i in issues]
    return out


# ---------------------------------------------------------------------------
# 维度 C · 表演与角色一致性
# ---------------------------------------------------------------------------


def dim_performance_consistency(ctx: dict[str, Any]) -> dict[str, Any]:
    out = _finding("C_performance_consistency", "mixed")
    issues: list[QAIssue] = []

    # 【现成】直接吃 Director 连续性报告 + Render 一致性报告 + 镜头 QA
    cont = ctx.get("continuity_report") or {}
    for msg in cont.get("identity_issues", []):
        issues.append(QAIssue("*", "IDENTITY_DRIFT", msg, SEVERITY_CRITICAL,
                              TARGET_ASSET_MATCHING, "final_review.consistency"))
    for msg in cont.get("scene_issues", []):
        issues.append(QAIssue("*", "SCENE_DRIFT", msg, SEVERITY_HIGH,
                              TARGET_DIRECTING, "final_review.consistency"))
    for msg in cont.get("prop_issues", []):
        issues.append(QAIssue("*", "PROP_CONTINUITY", msg, SEVERITY_MEDIUM,
                              TARGET_DIRECTING, "final_review.consistency"))

    consist = ctx.get("consistency_report") or {}
    for msg in consist.get("structural_issues", []):
        issues.append(QAIssue("*", "RENDERED_IDENTITY_DRIFT", msg,
                              SEVERITY_CRITICAL, TARGET_ASSET_MATCHING,
                              "final_review.consistency"))

    # 【现成】身份一致性分（全片均值 + 单镜下限）
    scores = [((c.get("render_result") or {}).get("metrics") or {}).get("identity_consistency")
              for c in ctx.get("contracts") or []]
    scores = [s for s in scores if isinstance(s, (int, float))]
    if scores:
        avg = sum(scores) / len(scores)
        if avg < MIN_IDENTITY_CONSISTENCY_AVG:
            issues.append(QAIssue("*", "LOW_IDENTITY_AVG",
                f"身份一致性均值 {avg:.3f} < {MIN_IDENTITY_CONSISTENCY_AVG}",
                SEVERITY_HIGH, TARGET_RENDERING, "final_review.consistency",
                structural=False))
        if min(scores) < MIN_IDENTITY_CONSISTENCY_ANY:
            issues.append(QAIssue("*", "LOW_IDENTITY_SHOT",
                f"存在单镜身份一致性 {min(scores):.3f} < {MIN_IDENTITY_CONSISTENCY_ANY}",
                SEVERITY_MEDIUM, TARGET_RENDERING, "final_review.consistency",
                structural=False))

    # 【现成】表演节拍（复用镜头 QA 的 performance 结论）
    shot_qa = ctx.get("shot_qa") or {}
    for row in (shot_qa.get("performance") or {}).get("issues", []):
        if row.get("severity") in (SEVERITY_HIGH, SEVERITY_MEDIUM):
            issues.append(QAIssue(row["shot_id"], row["code"], row["message"],
                row["severity"], TARGET_PERFORMANCE, "final_review.consistency"))

    out["pending"] = [_pending("C", "表演真实度、眼神情绪（多模态）")]
    out["issues"] = [i.to_dict() for i in issues]
    return out


# ---------------------------------------------------------------------------
# 维度 D · 视听技术（含口型铁律）
# ---------------------------------------------------------------------------


def dim_audiovisual(ctx: dict[str, Any]) -> dict[str, Any]:
    out = _finding("D_audiovisual", "mixed")
    issues: list[QAIssue] = []
    contracts = ctx.get("contracts") or []

    # 【现成/规则】口型铁律（确定性，现在就能做）
    for c in contracts:
        for msg in check_mouth_policy(c):
            issues.append(QAIssue(c["shot_id"], "MOUTH_POLICY_VIOLATION", msg,
                SEVERITY_HIGH, TARGET_DIRECTING, "final_review.audiovisual"))

    # 【现成】分辨率/帧率全片统一（读 rendered_shot）
    specs = {(r.get("resolution"), r.get("fps"))
             for c in contracts if (r := c.get("render_result"))}
    if len(specs) > 1:
        issues.append(QAIssue("*", "SPEC_MISMATCH",
            f"全片分辨率/帧率不统一：{sorted(map(str, specs))}",
            SEVERITY_HIGH, TARGET_RENDERING, "final_review.audiovisual"))

    # 【现成】非占位素材（占位色板不能当成片）
    for c in contracts:
        r = c.get("render_result") or {}
        if r and not r.get("is_generated_footage"):
            issues.append(QAIssue(c["shot_id"], "PLACEHOLDER_FOOTAGE",
                "产物是占位素材，不是模型生成画面",
                SEVERITY_INFO, TARGET_RENDERING, "final_review.audiovisual"))

    # 【Audio 已接】真实音频一致性：有声才张嘴 / 无声必闭嘴 / 有对白必有音量关系
    audio_ran = any(c.get("has_real_audio") for c in contracts)
    if audio_ran:
        for c in contracts:
            bindings = (c.get("voice_contract") or {}).get("bindings") or []
            has_audio = any(b.get("audio_file") for b in bindings)
            policy = c.get("mouth_policy")
            if policy == "SPEAKING_LIPSYNC" and (c.get("dialogue") or []) and not has_audio:
                issues.append(QAIssue(c["shot_id"], "VOICED_NO_AUDIO",
                    "张嘴说话镜头缺真实音频（会无声张嘴）",
                    SEVERITY_HIGH, TARGET_PERFORMANCE, "final_review.audiovisual"))
            if policy in ("SILENT_CLOSED", "REACTION") and has_audio:
                issues.append(QAIssue(c["shot_id"], "SILENT_HAS_AUDIO",
                    "无声镜头却挂了说话人音频（会闭嘴出声）",
                    SEVERITY_HIGH, TARGET_PERFORMANCE, "final_review.audiovisual"))
            if (c.get("dialogue") or []) and not c.get("audio_mix"):
                issues.append(QAIssue(c["shot_id"], "AUDIO_MIX_MISSING",
                    "有对白但缺对白/BGM 音量关系",
                    SEVERITY_MEDIUM, TARGET_PERFORMANCE, "final_review.audiovisual"))

    # 【视觉判断已接】明显假感/崩坏：ffmpeg 像素层真判（静帧/空白=假感）
    vj = ctx.get("vision_judge") or {}
    if vj.get("judged") and vj.get("fake"):
        issues.append(QAIssue("*", "VISION_FAKE",
            "视觉判断：" + "；".join(vj.get("reasons") or ["明显假感/崩坏"]),
            SEVERITY_HIGH, TARGET_RENDERING, "final_review.audiovisual"))

    # 唇形状态**诚实可见**：未接真实模型则如实记 info（不阻断，但不假装完成）
    lip = ctx.get("lip_sync") or {}
    if audio_ran and not lip.get("lip_synced", False):
        issues.append(QAIssue("*", "LIP_SYNC_NOT_APPLIED",
            "唇形未真正对齐（" + str(lip.get("note", "未接真实唇形模型")) + "）",
            SEVERITY_INFO, TARGET_RENDERING, "final_review.audiovisual"))

    # 剩余仍需 LLM 视觉深判的项（ffmpeg 层已覆盖明显假感/崩坏；细粒度手脸/僵硬待接）
    pending = []
    if not (vj.get("judged") and vj.get("deep_judged")):
        pending.append(_pending("D", "细粒度手部/面部崩坏、表演僵硬（LLM 视觉深判，插槽待接）"))
    if not audio_ran:
        pending = [
            _pending("D", "口型实测（嘴动是否真有声音）"),
            _pending("D", "女声/男声匹配、非全男声"),
            _pending("D", "字幕大小/位置/同步/无乱码"),
            _pending("D", "音画同步、对白不被 BGM 压过"),
        ] + pending
    out["pending"] = pending
    out["issues"] = [i.to_dict() for i in issues]
    return out


# ---------------------------------------------------------------------------
# 维度 E · 商业完成度
# ---------------------------------------------------------------------------


def dim_commercial(ctx: dict[str, Any]) -> dict[str, Any]:
    out = _finding("E_commercial", "mixed")
    issues: list[QAIssue] = []
    contracts = sorted(ctx.get("contracts") or [], key=lambda c: c["order"])
    script = ctx.get("shooting_script") or {}

    # 【现成】前 5 秒有钩子镜头
    if contracts:
        head = [c for c in contracts if c["order"] == 1]
        if head and head[0]["emotion"].get("beat") != BEAT_HOOK:
            issues.append(QAIssue(head[0]["shot_id"], "NO_OPENING_HOOK",
                "前 5 秒不是钩子镜头，留存有风险", SEVERITY_MEDIUM,
                TARGET_DIRECTING, "final_review.commercial"))

    # 【现成】留存/钩子强度（复用 market_hook_critic）
    if script:
        mk = market_hook_critic(script)
        if mk.get("retention_score", 0) < MIN_RETENTION:
            issues.append(QAIssue("*", "LOW_RETENTION",
                f"留存分 {mk.get('retention_score')} < {MIN_RETENTION}",
                SEVERITY_MEDIUM, TARGET_SCRIPT, "final_review.commercial"))

    # 【现成·硬】有可发布素材。区分两种"不可发布"：
    #   ① 完全没有真实媒体文件（mock）→ 硬拦，成片里根本没东西
    #   ② 有真实文件但是占位色板（非模型生成画面）→ 不硬拦，
    #      记 PENDING「真实生成画面」，使裁决停在 PASS_STRUCTURAL（结构完整、内容待真）
    with_media = [c for c in contracts if (c.get("render_result") or {}).get("is_real_media")]
    generated = [c for c in contracts if (c.get("render_result") or {}).get("is_generated_footage")]
    if contracts and len(with_media) < len(contracts):
        issues.append(QAIssue("*", "NO_MEDIA",
            f"仅 {len(with_media)}/{len(contracts)} 个镜头有真实媒体文件，成片缺素材",
            SEVERITY_HIGH, TARGET_RENDERING, "final_review.commercial"))
    elif contracts and len(generated) < len(contracts):
        out["pending"].append(_pending("E",
            f"真实生成画面（当前 {len(generated)}/{len(contracts)} 为占位素材）"))

    # 【现成·硬】安全合规复查（红线/未授权源/真实品牌）
    for msg in ctx.get("safety_violations") or []:
        issues.append(QAIssue("*", "SAFETY_VIOLATION", msg, SEVERITY_CRITICAL,
                              TARGET_ASSET_MATCHING, "final_review.commercial"))

    out["pending"].append(_pending("E", "整体商业感、平台适配（多模态）"))
    out["issues"] = [i.to_dict() for i in issues]
    return out


ALL_DIMENSIONS = (
    dim_narrative, dim_direction, dim_performance_consistency,
    dim_audiovisual, dim_commercial,
)
