"""商业可发布清单 (Phase I) —— PASS_FULL 的硬条件。

把"多模态判断项"从 PENDING 推进到真实检查。PASS_FULL = 本清单**全部通过**。
每条不通过都带 target（精准打回）与可执行说明。

覆盖（用户要求的至少 5 项，全为真实检查，非占位）：
  1. character_consistency  角色一致性：无身份漂移 + 一致性分达标
  2. no_black_or_frame_drop 黑屏/缺帧：ffmpeg 真实探测成片视频
  3. no_obvious_fake        明显假感：真实生成画面（非占位）+ 可选视觉判官
  4. lip_sync_basic         口型基本合规：接了真实音频且有声才张嘴/无声必闭嘴
  5. opening_hook           前 5 秒钩子结构：开场是钩子/强情绪/台词

"结构通过(PASS_STRUCTURAL)" = 结构硬门过但本清单有项未过；
"商业可发布(PASS_FULL)" = 结构 + 本清单全过。
"""

from __future__ import annotations

from typing import Any

from director.shot_contract import check_mouth_policy
from qa.agents import (
    TARGET_ASSET_MATCHING,
    TARGET_DIRECTING,
    TARGET_PERFORMANCE,
    TARGET_RENDERING,
)

MIN_IDENTITY_AVG = 0.80
_HOOK_BEATS = ("HOOK", "CONFLICT", "CRISIS", "TWIST")


def _check(name: str, ok: bool, target: str, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "target": target, "detail": detail}


def _subtitle_shot_alignment(contracts: list[dict[str, Any]]) -> tuple[bool, str]:
    """校验每句对白字幕/配音的起点落在其**镜头时间段**内(视频时轴=Σ镜头 duration_sec)。

    build_dialogue_vtt 把 cue 锚到镜头起点;此闸独立按 duration_sec 累加算各镜时段,再验
    每个 cue.start∈[镜头起, 镜头止]。若时轴回归成"只累加台词时长"(本轮 bug),cue 会落到
    错误镜头 → 此闸拦下。无对白 → 视为通过。
    """
    from audio.service import build_dialogue_vtt
    _, cues = build_dialogue_vtt(contracts)
    if not cues:
        return True, "无对白字幕，跳过对齐检查"
    windows: dict[str, tuple[float, float]] = {}
    t = 0.0
    for c in sorted(contracts, key=lambda c: c.get("order", 0)):
        d = float(c.get("duration_sec") or 0.0)
        windows[c["shot_id"]] = (t, t + d)
        t += d
    bad: list[str] = []
    for cue in cues:
        win = windows.get(cue.get("shot_id"))
        if win is None:
            continue
        if not (win[0] - 0.5 <= float(cue["start"]) <= win[1] + 0.5):
            bad.append(cue.get("shot_id"))
    if bad:
        return False, f"{len(bad)} 句字幕/配音偏离其镜头时段（跑在画面前/后）：{bad[:3]}"
    return True, "每句字幕/配音都落在其镜头时间段内"


def evaluate_checklist(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """跑商业可发布清单，返回逐条 {name, ok, target, detail}。"""
    contracts = sorted(ctx.get("contracts") or [], key=lambda c: c.get("order", 0))
    probe = ctx.get("media_probe") or None
    checks: list[dict[str, Any]] = []

    # 1) 角色一致性：结构(asset_hash) + **真实视觉跨镜漂移**(IdentityConsistencyQA)
    cont = ctx.get("continuity_report") or {}
    consist = ctx.get("consistency_report") or {}
    idc = ctx.get("identity_consistency") or {}
    visual_drift = list(idc.get("drifted_shots") or [])
    drift = (bool(cont.get("identity_issues")) or bool(consist.get("structural_issues"))
             or bool(visual_drift))
    scores = [((c.get("render_result") or {}).get("metrics") or {}).get("identity_consistency")
              for c in contracts]
    scores = [s for s in scores if isinstance(s, (int, float))]
    avg = sum(scores) / len(scores) if scores else 1.0
    ok = (not drift) and avg >= MIN_IDENTITY_AVG
    detail = "无身份漂移且一致性均值≥0.80"
    if visual_drift:
        detail = (f"跨镜视觉身份漂移 {len(visual_drift)} 镜：{visual_drift[:4]}"
                  f"（比对器{'真实' if idc.get('embedder_is_real') else '代理'}）")
    elif not ok:
        detail = f"身份漂移或一致性均值 {avg:.2f}<0.80"
    checks.append(_check("character_consistency", ok, TARGET_RENDERING, detail))

    # 2) 黑屏/缺帧（ffmpeg 真实探测）
    if probe is not None:
        pok = probe.get("ok", False)
        checks.append(_check("no_black_or_frame_drop", pok, TARGET_RENDERING,
            "成片无黑屏/冻帧/缺帧" if pok else
            "、".join(probe.get("issues") or ["视频异常"])))
    else:
        checks.append(_check("no_black_or_frame_drop", False, TARGET_RENDERING,
            "无成片视频可探测（未导出真实 MP4）"))

    # 3) 明显假感（真实生成画面为硬底；可选视觉判官进一步判）
    real_shots = [c for c in contracts if (c.get("render_result") or {}).get("is_real_media")]
    generated = [c for c in real_shots if (c.get("render_result") or {}).get("is_generated_footage")]
    judge = ctx.get("vision_judge") or {}   # 可选：外部视觉判官 {fake: bool, reason}
    if contracts and len(generated) < len(contracts):
        checks.append(_check("no_obvious_fake", False, TARGET_RENDERING,
            f"仅 {len(generated)}/{len(contracts)} 为真实生成画面（占位/mock 视为假感）"))
    elif judge.get("fake"):
        checks.append(_check("no_obvious_fake", False, TARGET_RENDERING,
            f"视觉判官判为假感：{judge.get('reason', '')}"))
    else:
        checks.append(_check("no_obvious_fake", True, TARGET_RENDERING,
            "全片真实生成画面" + ("（视觉判官通过）" if judge else "（未接视觉判官，仅结构底线）")))

    # 4) 口型基本合规
    audio_ran = any(c.get("has_real_audio") for c in contracts)
    mouth_bad = []
    if audio_ran:
        for c in contracts:
            bindings = (c.get("voice_contract") or {}).get("bindings") or []
            has_audio = any(b.get("audio_file") for b in bindings)
            policy = c.get("mouth_policy")
            if policy == "SPEAKING_LIPSYNC" and (c.get("dialogue") or []) and not has_audio:
                mouth_bad.append(c["shot_id"])
            if policy in ("SILENT_CLOSED", "REACTION") and has_audio:
                mouth_bad.append(c["shot_id"])
            mouth_bad.extend([c["shot_id"]] if check_mouth_policy(c) else [])
    ok = audio_ran and not mouth_bad
    checks.append(_check("lip_sync_basic", ok, TARGET_PERFORMANCE,
        "有声才张嘴/无声必闭嘴，口型合规" if ok else
        ("未接真实音频，口型无法实测" if not audio_ran else
         f"口型不合规：{sorted(set(mouth_bad))[:3]}")))

    # 5) 前 5 秒钩子结构
    hook = False
    if contracts:
        head = contracts[0]
        beat = str((head.get("emotion") or {}).get("beat", "")).upper()
        inten = float((head.get("emotion") or {}).get("intensity", 0.5) or 0.5)
        hook = beat in _HOOK_BEATS or inten >= 0.6 or bool(head.get("dialogue"))
    checks.append(_check("opening_hook", hook, TARGET_DIRECTING,
        "开场是钩子/强情绪/台词" if hook else "前 5 秒无钩子，留存有风险"))

    # 7) 字幕/音频-镜头对齐：每句字幕/配音必须落在其镜头的时间段内
    #    （治本轮致命 bug：字幕只累加台词时长→整体提前、中段就放完、片尾无字幕；音频同错）。
    sub_ok, sub_detail = _subtitle_shot_alignment(contracts)
    checks.append(_check("subtitle_shot_alignment", sub_ok, TARGET_PERFORMANCE, sub_detail))

    # 6) 镜头功能表达：动作类功能镜不得近乎静止（技术过但没演出功能=失败）
    fe = ctx.get("function_expression")
    if fe is not None:
        flagged = list(fe.get("flagged") or [])
        feok = not flagged
        checks.append(_check("story_function_expressed", feok, TARGET_RENDERING,
            f"动作类功能镜均演出（查 {fe.get('checked', 0)} 镜）" if feok else
            f"{len(flagged)} 个动作镜近乎静止未表达功能：{[x['shot_id'] for x in flagged][:3]}"))

    return checks


#: PASS_FULL 硬条件清单（对外展示用）
PASS_FULL_CHECKLIST = [
    ("character_consistency", "角色一致性：无身份漂移 + 一致性分达标"),
    ("no_black_or_frame_drop", "黑屏/缺帧：ffmpeg 真实探测成片无黑屏/冻帧/丢帧"),
    ("no_obvious_fake", "明显假感：全片真实生成画面（非占位）"),
    ("lip_sync_basic", "口型基本合规：有声才张嘴、无声必闭嘴"),
    ("subtitle_shot_alignment", "字幕/音频-镜头对齐：每句落在其镜头时间段内"),
    ("opening_hook", "前 5 秒钩子结构：开场是钩子/强情绪/台词"),
]


def checklist_passed(checks: list[dict[str, Any]]) -> bool:
    return all(c["ok"] for c in checks)


__all__ = ["evaluate_checklist", "checklist_passed", "PASS_FULL_CHECKLIST"]
