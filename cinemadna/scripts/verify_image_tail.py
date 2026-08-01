"""验证方案A可行性：Kling 起帧+尾帧(image_tail) 让动作真发生吗？

PuLID 出 起帧(护士中性站立) + 尾帧(护士举起欠条) → 走真实 Kling image2video(带 image_tail)
→ 下载 clip → 抽起/中/末帧 + 运动量。看动作是否真发生、运动是否明显高于现在的 1-2。
成本 ~$1.5(2张PuLID + 1个Kling真镜)。预算闸前置。
"""

from __future__ import annotations

import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity.fal_pulid import FalPuLIDBackend  # noqa: E402
from render.backend import RenderRequest  # noqa: E402
from render.budget import BudgetGate, confirm_yes  # noqa: E402
from render.kling_backend import KlingImageToVideoBackend  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "workspace_verify_tail"
ANCHOR = (Path(__file__).resolve().parents[1] /
          "workspace_plan_d/bundle_20260724_001/08_submissions/first_frames"
          "/shot__EP001_SC001_SH001.png")

_START = ("cinematic film still, a young East Asian Chinese nurse in a beige top, "
          "standing calm in a neutral ready pose, hands relaxed at her sides, "
          "medium close-up, dim night Chinese street, moody lighting, photorealistic, "
          "natural skin texture, no text")
_END = ("cinematic film still, a young East Asian Chinese nurse in a beige top, "
        "holding up a crumpled paper IOU document with both hands at chest height, "
        "arms raised showing the document to camera, medium close-up, dim night "
        "Chinese street, moody lighting, photorealistic, natural skin texture, "
        "action fully completed, no text")


class VerifyKling(KlingImageToVideoBackend):
    def __init__(self, start_b64, tail_b64, **kw):
        super().__init__(**kw)
        self._s, self._t = start_b64, tail_b64

    def _reference_image_b64(self, request):
        return self._s

    def _tail_image_b64(self, request):
        return self._t


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"锚脸: {ANCHOR.name}  存在={ANCHOR.is_file()}")
    gate = BudgetGate(max_units=5.0, confirm=confirm_yes,
                      allowed_durations_sec=tuple(range(1, 13)),
                      cost_table={("kling-v1", "std", d): 1.5 for d in range(1, 13)})

    pulid = FalPuLIDBackend(budget=gate)
    s_png, e_png = OUT / "start.png", OUT / "end.png"
    print("① PuLID 起帧(中性)…")
    pulid.generate(reference_face=ANCHOR, prompt=_START, dest=s_png,
                   item_id="verify_start", width=768, height=1024)
    print("② PuLID 尾帧(举欠条)…")
    pulid.generate(reference_face=ANCHOR, prompt=_END, dest=e_png,
                   item_id="verify_end", width=768, height=1024)

    s_b64 = base64.b64encode(s_png.read_bytes()).decode()
    t_b64 = base64.b64encode(e_png.read_bytes()).decode()

    be = VerifyKling(s_b64, t_b64, budget=gate, model_name="kling-v1", mode="std")
    req = RenderRequest(
        shot_id="verify_tail", task_id="verify_tail", story_id="v", bundle_id="v",
        contract_hash="h", model="kling-v1", tier="std", duration_sec=5, seed=1,
        prompt="a nurse raises a crumpled IOU document to camera, night street",
        reference_assets={"character_images": {"linwan": "x"}, "props": {}})

    print("③ 提交 Kling image2video(带 image_tail)…")
    job = be.submit(req)
    print(f"   task_id={job}  预算已用={gate.spent_units}")
    url = None
    for i in range(60):
        time.sleep(10)
        done, u = be.poll(job)
        print(f"   轮询#{i} done={done}")
        if done:
            url = u
            break
    if not url:
        print("超时未出片")
        return 1
    clip = OUT / "tail_clip.mp4"
    be.download(url, clip)
    print(f"④ 下载成品: {clip}  {clip.stat().st_size} bytes")
    print(f"预算真实记账: {gate.spent_units} units")
    print(f"成品: {clip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
