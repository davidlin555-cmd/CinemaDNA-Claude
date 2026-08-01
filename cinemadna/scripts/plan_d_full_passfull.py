"""方向2：全量真实出片，冲 PASS_FULL —— 11 镜真实 image2video + 屏内合成 + AudioDNA。

    python scripts/plan_d_full_passfull.py

用户已确认：预算闸上限 25 units（真实预估 ~8-11）、接 AudioDNA 全套、
lip_synced 保持诚实 False、人脸质量已认可。

链路（全自动，只在成片终确认停）：
  剧本 → 真实人脸(FLUX) → 真实镜(Kling image2video) → 屏内道具合成(Chrome) →
  AudioDNA(配音+BGM+环境音+字幕+loudnorm) → 商业闸(PASS_FULL 硬清单)

安全：BudgetGate(max=25) 前置，5±秒白名单，confirm_yes（用户逐项授权本次全量），
不印原始密钥；非成片人工闸(剧本/人脸)凭用户已确认自动放行并如实记录，
成片终确认(final_review_critical)保留人工。进度写 progress.log 可随时查。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assets_real.screen_render import ChromeScreenshotBackend  # noqa: E402
from assets_real.image_backend import TogetherImageBackend  # noqa: E402
from audio.mixer import AudioMixer  # noqa: E402
from audio.tts_edge import EdgeTTSBackend  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.backend import BackendCapabilities  # noqa: E402
from render.budget import BudgetGate, confirm_yes  # noqa: E402
from render.registry import BackendRegistry  # noqa: E402
from render.first_frame import (  # noqa: E402
    FirstFrameBackend, SceneGroundedImageToVideoBackend)
from council.commercial_review import CommercialCouncil  # noqa: E402
from scriptbrain.llm_author import LLMNarrativeAuthor  # noqa: E402
from scriptbrain.service import ScriptBrainService  # noqa: E402

THEME = "县城女护士深夜被高利贷追债，翻出攥皱的缴费单与催债短信，最后逆袭翻身"
OUT = Path(__file__).resolve().parents[1] / "workspace_plan_d"
LOG = OUT / "progress.log"
NONFINAL_GATES = {"script_gate3", "identity_review"}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    import shutil
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    log("=== 全量真实出片启动（叙事+密度+场景内首帧版）===")
    # 预算闸：白名单 2-12 秒（节拍密度镜 2-4s + 语音回写最长 12s），价表补齐 2.0/镜。
    # 记账贴近真实（Kling std 5s ≈ 1 unit，取 1.2 留薄边），上限 26 留足重试余量，
    # 避免"记账2×+首帧+16镜"顶满上限→中途提交被拦→镜头FAILED→SCENE_FIX空烧的复盘故障。
    # 白名单/价表从 1s 起：节拍密度短剧的插入镜/反应镜常在 1.2-2s，Kling 实际仍出
    # ~5s 片再由剪辑裁到目标时长（i2v 提交体不带时长），故短目标时长安全。旧版从 2s
    # 起 + 渲染能力下限 2.0 会把 1.6-1.9s 的插入镜判"超出后端范围"→整片 FAILED（本次复盘）。
    cost_table = {("kling-v1", "std", d): 1.2 for d in range(1, 13)}
    gate = BudgetGate(max_units=26.0, confirm=confirm_yes,
                      allowed_durations_sec=tuple(range(1, 13)), cost_table=cost_table)
    log(f"预算闸 armed={gate.armed} 上限={gate.max_units} 白名单={gate.allowed_durations_sec}")

    screen = ChromeScreenshotBackend()
    img = TogetherImageBackend(budget=gate)            # FLUX（人脸 + 场景内首帧）
    # 场景内首帧 + image2video：每镜锚定场景，根治证件照感
    be = SceneGroundedImageToVideoBackend(
        first_frame_backend=FirstFrameBackend(img),
        budget=gate, bundle_root=OUT, model_name="kling-v1", mode="std",
        sleep_fn=time.sleep)
    be.capabilities = BackendCapabilities(             # 吃下 1-12s 合约（对齐合约下限）
        min_duration_sec=1.0, max_duration_sec=12.5, supports_reference_images=True,
        produces_media=True, is_async=True, accepts_any_resolution=True,
        duration_tolerance_sec=10.0)
    registry = BackendRegistry(default=be)

    # 工厂资产库（跨生产持久化，在 OUT 之外不被 wipe）→ 复用真触发 + 只存好资产
    from asset_brain.common.store import AssetBrainStore
    factory_lib = Path(__file__).resolve().parents[1] / "factory_library"
    orc = PipelineOrchestrator(
        bundle_root=OUT, bundle_date="20260724",
        audio_backend=EdgeTTSBackend(), audio_mixer=AudioMixer(),
        screen_backend=screen if screen.available() else None,
        image_backend=img, store=AssetBrainStore(library_dir=factory_lib))
    log(f"工厂资产库：{factory_lib}（跨生产复用，只存过闸真资产）")
    orc.render_registry = registry
    # ④真口型：LatentSync 本地后端。两段生产：LIPSYNC=off 时第一段只出 Kling 素材、
    # 不跑持续 GPU（扛机器重启）；第二段用本地脚本对已渲染 clip 补口型（可续跑）。
    if os.environ.get("LIPSYNC", "on").lower() == "off":
        from audio.lipsync import PassthroughLipSync
        orc.lipsync_backend = PassthroughLipSync()
        log("口型：本次关闭（第一段只出 Kling 素材，口型放第二段本地补，扛重启）")
    else:
        from audio.lipsync import resolve_lipsync
        orc.lipsync_backend = resolve_lipsync()
        log(f"口型后端：{getattr(orc.lipsync_backend, 'name', '?')} "
            f"real={getattr(orc.lipsync_backend, 'real', False)}")
    # ⑤LLM 视觉深判：抓变脸/手脸崩坏/画内乱码（Claude haiku 视觉，约 $0.05-0.15/片）
    orc.enable_vision_llm()
    log("LLM 视觉深判：已开启（熔断+成片总审都用）")
    # LLM 作者化 + 商业责编 Council 自我改进闭环（剧本层，几分钱文本）
    sb = ScriptBrainService(narrative_author=LLMNarrativeAuthor(),
                            commercial_council=CommercialCouncil(), max_revise_iters=4)
    log("后端：场景内首帧(FLUX)+Kling i2v(真) 屏内=Chrome 声音=EdgeTTS "
        "剧本=LLM作者化+责编闭环")

    s = orc.create_story(title="全量商业样片")
    sid = s.story_id
    orc.run_scriptbrain(sid, {"theme": THEME, "episode_count": 1,
                              "scenes_per_episode": 3}, scriptbrain=sb)
    log(f"剧本完成 story={sid}  叙事模式={sb.narrative_mode}")
    # 角色一致外观锚点（**英文**描述，去中文名字，避免 FLUX 把中文渲成乱码文字）：
    # 同一角色所有镜头共用同一套英文外观 → 身份一致 + 画面无文字。
    script = orc._shooting_scripts.get(sid) or {}
    # 角色外观锁(identity/appearance):每角色一套**具体固定**服装+发型(不再"story-appropriate
    # everyday clothing"模糊串)→ 锚脸/首帧/尾帧同源统一 → 治跨镜服装漂移(女主外套墨绿/卡其浮动)。
    from identity.appearance import character_appearance
    be.char_desc = {c["character_id"]: character_appearance(c)
                    for c in script.get("characters") or []}

    # 身份一致 = **每角色固定种子 + 一致外观描述**（按镜场景内首帧，构图随镜变化）。
    # 不建纯文生图金图包：它非参考条件级同脸(angles_are_reference_consistent=False)，
    # 拿来当每镜锚帧只会重演"零景别变化"，且白烧图像预算。真·跨镜同脸多角度要接
    # FLUX Redux / Kling Elements 参考条件生成（那时金图才作锚帧，见 first_frame 优先级①）。
    be.gold_packs = {}
    be.require_gold = False
    log("身份锚定：每角色固定种子+一致描述；首帧按镜构图（景别/动作/对手/道具随镜变化）")

    # 合成身份铸造（B）：每角色铸 novel 锚脸 → 主角单人镜用 PuLID 锁脸生成首帧
    # （跨镜同脸+无塑料感，实测同脸余弦 0.06-0.20）。FOUNDRY=off 可关。
    if os.environ.get("FOUNDRY", "on").lower() != "off":
        from identity.foundry import IdentityFoundry
        from identity.fal_pulid import FalPuLIDBackend
        foundry = IdentityFoundry(
            image_backend=img, pulid_backend=FalPuLIDBackend(budget=gate),
            bundle=orc.bundle_for(sid))
        for cid, desc in be.char_desc.items():
            foundry.mint_anchor(cid, desc)
        be.foundry = foundry
        log(f"合成身份铸造：{list(be.char_desc)} 锚脸已铸，主角单人镜 PuLID 锁脸")
    else:
        log("合成身份铸造：本次关闭（FOUNDRY=off）")

    # 自动驱动；非成片人工闸凭用户已确认「按闸门正确方式」裁决放行；成片终确认保留
    OK = {"approved": True, "decided_by": "operator",
          "note": "用户已确认剧本方向与人脸质量，授权全量运行"}
    releases: list[str] = []
    for it in range(40):
        orc.run_until_quiescent(export_media=True)
        st = orc.get_story(sid)
        hold = st.get("hold") or {}
        reason = hold.get("reason")
        log(f"驱动#{it}: stage={st['stage']} status={st['status']} "
            f"落点={reason or '-'} 预算已用={gate.spent_units}/{gate.max_units}")
        if st["status"] != "WAITING_HUMAN" or reason not in NONFINAL_GATES:
            break                                    # 成片终确认 / 终态 / 卡住 → 停
        if reason == "identity_review":              # 人脸闸：逐个 Gate 正式裁决
            for gid in list(hold.get("gate_ids") or []):
                orc.resolve_asset_gate(sid, gid, dict(OK))
        elif reason == "script_gate3":               # 剧本闸：专用裁决
            orc.approve_script_gate(sid, dict(OK))
        releases.append(reason)
        log(f"  放行非成片人工闸：{reason}（用户已确认）")

    # ---- 收口报告 ----
    st = orc.get_story(sid)
    fr = orc.final_review_for(sid)
    b = orc.bundle_for(sid)
    ctx = orc._final_review_ctx(sid)
    vj = ctx.get("vision_judge") or {}
    lip = orc._lip_sync.get(sid, {})
    cut = ctx.get("rough_cut") or {}
    rr = orc._render_results.get(sid)

    composited = []
    if rr:
        for r in rr.rendered:
            if r.get("screen_composited"):
                composited.append({"shot": r["shot_id"], "screens": r["screen_composited"]})

    playable = None
    for rel in (f"10_outputs/{sid}_final.mp4", "10_outputs/drama_audio_final.mp4",
                "10_outputs/rough_cut.mp4"):
        if b.exists(rel):
            playable = b.path_for(rel); break

    log("=== 收口报告 ===")
    log(f"终态 stage={st['stage']} status={st['status']} 落点={(st.get('hold') or {}).get('reason')}")
    log(f"成片：{cut.get('shot_count')} 镜 / {cut.get('total_duration_sec')}s")
    if fr:
        log(f"商业闸：{fr.verdict} 商业可发布={fr.commercial_ready} 结构过={fr.structural_passed}")
        for c in fr.checklist:
            log(f"  {'PASS' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}")
    log(f"lip_synced={lip.get('lip_synced')}")
    if vj:
        log(f"视觉判断 fake={vj.get('fake')} motion={vj.get('motion')} 深判={vj.get('deep_judged')}")
    log(f"屏内合成镜：{composited or '无'}")
    log(f"放行的非成片人工闸：{releases}")
    log(f"预算：真实记账 {gate.spent_units} units（含 {gate.summary()['submission_count']} 次提交）")
    log(f"可播放：{playable}")

    report = {
        "verdict": fr.verdict if fr else None,
        "commercial_ready": fr.commercial_ready if fr else None,
        "structural_passed": fr.structural_passed if fr else None,
        "checklist": fr.checklist if fr else [],
        "lip_synced": lip.get("lip_synced"),
        "vision_judge": vj,
        "screen_composited_shots": composited,
        "released_nonfinal_gates": releases,
        "budget": gate.summary(),
        "shot_count": cut.get("shot_count"),
        "total_duration_sec": cut.get("total_duration_sec"),
        "final_stage": st["stage"], "final_status": st["status"],
        "hold_reason": (st.get("hold") or {}).get("reason"),
        "playable": str(playable) if playable else None,
        "render_summary": rr.summary() if rr else None,
    }
    (OUT / "plan_d_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"报告落盘：{OUT / 'plan_d_report.json'}")
    log("=== 完成 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
