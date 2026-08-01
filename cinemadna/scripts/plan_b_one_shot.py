"""选项 2：单镜真实生成验证 —— 1 张真实人脸(FLUX) + 1 个 5 秒 image2video(Kling)。

    python scripts/plan_b_one_shot.py

目的（用户明确授权，只此一镜，约 1 unit）：
  1. 真实跑通 image2video 通路（合约参考图 → Kling → 真实 MP4）
  2. 验证视觉判断能识别**真实运动素材**（motion 应远高于占位静帧）
  3. 顺便观察屏内道具（手机催债短信）在 image2video 后是否可读

安全边界（全部硬前置）：
  - BudgetGate 先上，max_units=3（够 1 脸 0.01 + 1 视频 2.0，第二个视频会被拦）
  - 5 秒白名单；confirm=confirm_yes（用户已在对话中逐次授权此一镜）
  - 不印任何原始密钥；后端从 config 内部读取
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.common.bundle import Bundle  # noqa: E402
from assets_real.image_backend import TogetherImageBackend  # noqa: E402
from final_review.media_probe import MediaProbe  # noqa: E402
from final_review.vision_judge import VisionJudge  # noqa: E402
from render.backend import MediaSink, RenderRequest  # noqa: E402
from render.budget import BudgetGate, confirm_yes  # noqa: E402
from render.kling_backend import KlingImageToVideoBackend  # noqa: E402

CID = "char_linwan"
FACE_REL = "02_cast/faces/linwan.png"

# 关键镜头：县城女护士深夜，疲惫，手持手机，屏幕显示催债短信（同时测脸/运动/屏内道具）
FACE_PROMPT = (
    "cinematic realistic photo, a tired young Chinese county-town nurse in white "
    "uniform sitting alone in a dim room at night, holding a smartphone, the phone "
    "screen clearly showing a debt-collection text message in Chinese, exhausted "
    "worried face, soft moody lighting, vertical 9:16 portrait, photorealistic"
)
MOTION_PROMPT = (
    "she slowly lowers her eyes to read the phone message, subtle breathing, a small "
    "worried head movement, dim flickering light, gentle handheld camera, cinematic, "
    "realistic subtle motion"
)


def h(t: str) -> None:
    print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def main() -> int:
    out = Path(__file__).resolve().parents[1] / "workspace_plan_b"
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    bundle = Bundle(out, "one_shot_20260724")

    h("0. 预算闸先上（硬前置）")
    gate = BudgetGate(max_units=3.0, confirm=confirm_yes)  # 用户已授权此一镜
    print(f"  armed={gate.armed}  max_units={gate.max_units}  "
          f"5秒白名单={gate.allowed_durations_sec}")

    h("1. 真实人脸参考图（Together FLUX，~0.01 unit）")
    img = TogetherImageBackend(budget=gate)
    face_dest = bundle.path_for(FACE_REL)
    fres = img.generate(prompt=FACE_PROMPT, dest=face_dest, item_id=CID,
                        width=768, height=1024)
    print(f"  ✓ 人脸出图：{face_dest.name}  {fres.width}x{fres.height}  "
          f"{fres.bytes} 字节  seed={fres.seed}")
    print(f"  预算：已用 {gate.spent_units} / {gate.max_units} units")

    h("2. 真实 image2video（Kling，5s，~2 units 预估）")
    be = KlingImageToVideoBackend(budget=gate, bundle_root=out,
                                  model_name="kling-v1", mode="std",
                                  sleep_fn=time.sleep)   # 真实等待
    req = RenderRequest(
        shot_id="SH_KEY", task_id="task_key01", story_id="plan_b",
        bundle_id=bundle.bundle_id, contract_hash="sha256:planb-oneshot",
        model="kling-v1", tier="std", duration_sec=5.0, seed=fres.seed or 42,
        prompt=MOTION_PROMPT,
        reference_assets={"character_images": {CID: FACE_REL}})
    sink = MediaSink(bundle)
    print("  提交 → 轮询（真实，最多 ~6 分钟）…")
    t0 = time.time()
    rendered = be.render(req, sink=sink)
    dt = round(time.time() - t0)
    video = bundle.path_for(rendered["file_relpath"])
    print(f"  ✓ 出片：{rendered['file_relpath']}  {video.stat().st_size} 字节  "
          f"job={rendered.get('job_id')}  耗时 {dt}s")
    print(f"  预算：已用 {gate.spent_units} / {gate.max_units} units")

    h("3. 视觉判断（ffmpeg 真跑）——能否识别真实运动？")
    vj = VisionJudge().judge(video).to_dict()
    print(f"  fake={vj['fake']}  motion={vj['motion']}  动态范围={vj['luma_spread']}  "
          f"深判={vj['deep_judged']}")
    if vj["reasons"]:
        print(f"  理由：{'；'.join(vj['reasons'])}")

    h("4. 黑屏/冻帧探测（MediaProbe）")
    mp = MediaProbe().probe(video).to_dict()
    print(f"  时长={mp['duration']}s  帧数={mp['frames']}  fps={mp['fps']}  "
          f"黑屏={mp['black_events']}  冻帧={mp['freeze_events']}  丢帧={mp['dropped_frames']}")
    if mp["issues"]:
        print(f"  问题：{'；'.join(mp['issues'])}")

    # 抽一帧看屏内道具是否可读
    h("5. 抽帧（观察屏内催债短信是否可读）")
    frame = bundle.path_for("10_outputs/keyshot_frame.png")
    frame.parent.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "2.5",
                    "-i", str(video), "-frames:v", "1", str(frame)],
                   check=False)
    print(f"  抽帧：{frame}（人工肉眼判断屏内文字清晰度）")

    # ---- 结论 ----
    h("6. 结论")
    real_motion = vj["motion"] >= 0.05 and not vj["checks"].get("static")
    blocked = vj["fake"] or mp["freeze_events"] > 0 or mp["black_events"] > 0
    print(f"  消耗预算：{gate.spent_units} units（脸 0.01 + 视频 ~2.0 预估；实际 Kling ~1 unit）")
    print(f"  镜头文件：{video}")
    print(f"  视觉判断识别真实运动：{'✓ 是' if real_motion else '✗ 否'}"
          f"（占位样片 motion≈占位噪声，本镜 motion={vj['motion']}）")
    print(f"  仍被冻帧/假感拦截：{'是' if blocked else '否'}")

    report = {
        "budget_spent_units": gate.spent_units,
        "budget_summary": gate.summary(),
        "face": fres.to_dict(),
        "video_relpath": rendered["file_relpath"],
        "video_abspath": str(video),
        "job_id": rendered.get("job_id"),
        "elapsed_sec": dt,
        "vision_judge": vj,
        "media_probe": mp,
        "vision_detects_real_motion": real_motion,
        "still_blocked_by_freeze_or_fake": blocked,
        "frame_for_screen_prop": str(frame),
    }
    rpath = out / "plan_b_report.json"
    rpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整报告：{rpath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
