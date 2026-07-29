"""只重渲 SH001（开场全景）—— 验证「锁定尾帧」根治 Kling 运动漂移。

上一样片 13/13 全锁 PuLID，仅 SH001 漂移：首帧锁对（→锚脸 0.165）但视频沿时间轴漂到
0.94 = Kling 动画中脸 morph 成另一人（见记忆 kling-motion-drift-tail）。根治 = 主角单人镜
一律出锁定尾帧 image_tail，Kling 在首尾两锁定帧间插值 → 脸被夹住不漂。

本脚本用生产同款 backend + 复用已铸锚脸/已好首帧，对 SH001 单合约 fresh 重渲一镜
（一次 Kling 提交，约 1.2 units），下载覆盖同路径 mp4，再量新漂移是否降到阈值 0.5 下。
不动其余 12 镜（已付费合格）。通过后用 plan_d_refinish 重装配成完整成片。

    python scripts/reroll_sh001_tail.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.common.bundle import Bundle  # noqa: E402
from assets_real.image_backend import TogetherImageBackend  # noqa: E402
from identity.fal_pulid import FalPuLIDBackend  # noqa: E402
from identity.foundry import IdentityFoundry  # noqa: E402
from render.backend import BackendCapabilities  # noqa: E402
from render.budget import BudgetGate, confirm_yes  # noqa: E402
from render.first_frame import (  # noqa: E402
    FirstFrameBackend, SceneGroundedImageToVideoBackend)
from render.registry import BackendRegistry  # noqa: E402
from render.service import RenderBrainService  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "workspace_plan_d"
BID = "bundle_20260724_001"
SHOT = "EP001_SC001_SH001"

# 角色外观锁(identity/appearance):具体固定服装+发型,身份实由 PuLID 锚脸锁定。
from identity.appearance import character_appearance as _desc_en  # noqa: E402


def _emb_at(app, vid: Path, t: float):
    import cv2
    with tempfile.TemporaryDirectory() as td:
        fp = os.path.join(td, "f.png")
        subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(vid),
                        "-frames:v", "1", fp], capture_output=True)
        if not os.path.exists(fp):
            return None
        fs = app.get(cv2.imread(fp))
        if not fs:
            return None
        return max(fs, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1])
                   ).normed_embedding


def _measure(app, anchor, vid: Path, label: str) -> float:
    import numpy as np

    def cos(a, b):
        return float(1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    ds = []
    for t in [0.3, 0.7, 1.2, 1.7, 2.2]:
        e = _emb_at(app, vid, t)
        ds.append(round(cos(anchor, e), 3) if e is not None else None)
    valid = [x for x in ds if x is not None]
    mx = max(valid) if valid else 99.0
    print(f"  {label} 多帧→锚脸: {ds}  最坏={mx}")
    return mx


def main() -> int:
    bundle = Bundle(OUT, BID)
    cost_table = {("kling-v1", "std", d): 1.2 for d in range(1, 13)}
    gate = BudgetGate(max_units=5.0, confirm=confirm_yes,
                      allowed_durations_sec=tuple(range(1, 13)), cost_table=cost_table)
    print(f"预算闸 armed={gate.armed} 上限={gate.max_units}（只烧 SH001 一镜）")

    img = TogetherImageBackend(budget=gate)
    be = SceneGroundedImageToVideoBackend(
        first_frame_backend=FirstFrameBackend(img),
        budget=gate, bundle_root=OUT, model_name="kling-v1", mode="std",
        sleep_fn=time.sleep)
    be.capabilities = BackendCapabilities(
        min_duration_sec=1.0, max_duration_sec=12.5, supports_reference_images=True,
        produces_media=True, is_async=True, accepts_any_resolution=True,
        duration_tolerance_sec=10.0)
    script = json.load(open(bundle.path_for("01_script/shooting_script.json"),
                            encoding="utf-8"))
    be.char_desc = {c["character_id"]: _desc_en(c)
                    for c in script.get("characters") or []}
    be.gold_packs = {}
    be.require_gold = False
    # 复用已铸锚脸：mint_anchor 幂等（文件在则只登记不重铸，无成本）
    foundry = IdentityFoundry(image_backend=img,
                              pulid_backend=FalPuLIDBackend(budget=gate), bundle=bundle)
    for cid, desc in be.char_desc.items():
        foundry.mint_anchor(cid, desc)
    be.foundry = foundry
    svc = RenderBrainService(bundle=bundle, registry=BackendRegistry(default=be))

    # 删旧尾帧缓存（若有）确保按新逻辑重出；起帧 0.165 已好，保留复用
    tail = bundle.path_for(f"08_submissions/first_frames/pulid_tail_shot__{SHOT}.png")
    if tail.is_file():
        tail.unlink()

    # 渲前基线：量旧 SH001 漂移
    from identity.insightface_embedder import InsightFaceEmbedder
    import cv2
    app = InsightFaceEmbedder()._get_app()
    anchor = app.get(cv2.imread(str(
        bundle.path_for("02_cast/anchors/char_linwan.png"))))[0].normed_embedding
    old_vid = bundle.path_for(f"09_downloads/task_{SHOT}/{SHOT}.mp4")
    print("\n[渲前基线]")
    _measure(app, anchor, old_vid, "旧 SH001")

    print(f"\n[重渲 {SHOT}] fresh Kling 提交（起帧复用 + 新锁定尾帧 image_tail）…")
    result = svc.render_all([json.load(open(
        bundle.path_for(f"06_shots/contracts/{SHOT}.json"), encoding="utf-8"))])
    if not result.rendered:
        print(f"  重渲失败: failed={result.failed} rejected={result.rejected}")
        return 1
    rr = result.rendered[0]
    tail_used = bundle.path_for(
        f"08_submissions/first_frames/pulid_tail_shot__{SHOT}.png").is_file()
    print(f"  已渲: {rr['shot_id']} file={rr['file_relpath']} "
          f"real={rr.get('is_real_media')} 尾帧生成={tail_used}")
    print(f"  预算真实记账 {gate.spent_units} units / {len(gate.submissions)} 次提交")

    # 落盘新 payload（供 refinish 重装配读取）
    payload_p = bundle.path_for(f"07_payloads/render/{SHOT}.json")
    json.dump(rr, open(payload_p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  新 render payload 已覆盖: {payload_p}")

    print("\n[渲后复检]")
    new_vid = bundle.path_for(rr["file_relpath"])
    worst = _measure(app, anchor, new_vid, "新 SH001")
    verdict = "✓通过(<0.5)" if worst < 0.5 else "✗仍漂移(≥0.5)"
    print(f"\n=== SH001 重渲结果: 最坏漂移 {worst} → {verdict} ===")
    if worst < 0.5:
        print("下一步: python scripts/plan_d_refinish.py 重装配成完整成片 + 复检商业闸")
    return 0 if worst < 0.5 else 2


if __name__ == "__main__":
    raise SystemExit(main())
