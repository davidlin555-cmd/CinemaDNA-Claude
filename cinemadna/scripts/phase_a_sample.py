"""Phase A 样片：真实角色人脸 + 真实短视频（image2video，角色一致性有根）。

    python scripts/phase_a_sample.py --img-budget 1 --vid-budget 5 --yes
    python scripts/phase_a_sample.py --dry-run       # 只出真实人脸，不出视频（省 units）

流程：
  1. ScriptBrain（免费）→ 剧本
  2. IdentityDNA **真实出图**（Together FLUX，图像预算闸）→ 真实合成人脸
  3. Director + Performance（免费）→ 合约（人物资产带真实人脸路径）
  4. 取一个镜头，KlingImageToVideoBackend **用那张脸出真实视频**（视频预算闸）
  5. 拼接

两个预算闸分开：图像（便宜）+ 视频（units）。默认关闭，--yes 才自动确认。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from assets_real.image_backend import TogetherImageBackend  # noqa: E402
from director.shot_contract import STATUS_DRAFT, sign_contract  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.assembly import assemble_rough_cut, export_cut  # noqa: E402
from render.budget import BudgetGate, confirm_interactive, confirm_yes  # noqa: E402
from render.kling_backend import KlingImageToVideoBackend  # noqa: E402
from render.registry import BackendRegistry  # noqa: E402
from render.service import RenderBrainService  # noqa: E402

THEME = "县城女护士深夜下班，走过空荡的医院走廊，接到催债电话"


def h(t): print(f"\n{'=' * 68}\n {t}\n{'=' * 68}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=THEME)
    ap.add_argument("--img-budget", type=float, default=0.0, help="图像预算(units)")
    ap.add_argument("--vid-budget", type=float, default=0.0, help="视频预算(units)")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="只出人脸，不出视频")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "workspace_phase_a"))
    args = ap.parse_args()

    h("0. 前置")
    for k in ("TOGETHER_API_KEY", "KLING_API_KEY", "KLING_API_BASE"):
        if not config.has(k):
            print(f"✗ 缺 {k}"); return 1
    print("✓ 图像(Together) + 视频(Kling) key 就绪")
    confirm = confirm_yes if args.yes else confirm_interactive

    # 图像预算闸（默认关=不出图；出图很便宜）
    img_budget = BudgetGate(max_units=args.img_budget or 0.0, confirm=confirm,
                            allowed_durations_sec=(5,))
    img_backend = (TogetherImageBackend(budget=img_budget)
                   if args.img_budget > 0 else None)
    if img_backend is None:
        print("⚠ 未开图像预算 → IdentityDNA 走 mock（无真实人脸）。加 --img-budget N 才出图。")

    root = Path(args.out)
    orc = PipelineOrchestrator(bundle_root=root, bundle_date="20260724",
                               image_backend=img_backend)

    h("1-2. 剧本 + 资产（IdentityDNA 真实出图）")
    s = orc.create_story(title="Phase A 样片")
    orc.run_scriptbrain(s.story_id, args.theme)
    orc.dispatch_assets(s.story_id)
    # 看真实人脸
    faces = []
    for cid, pack in orc.store.character_registry.items():
        if pack.get("is_real_image"):
            faces.append((cid, pack["base_face_image"]))
    print(f"真实人脸：{faces or '（无，未开图像预算）'}")

    h("3. Director + Performance + PreRender Gate（免费）")
    orc.run_director(s.story_id); orc.run_performance(s.story_id)
    pre = orc.run_prerender_gate(s.story_id)
    print(f"PreRender Gate: {pre['summary']['status']}  "
          f"(真实人脸被互检校验，require_real_faces=True)")
    print("12 项 flag:", {k: v for k, v in pre['report']['flags'].items()
                          if v != 'READY'} or "全部 READY")
    if pre['summary']['status'] != "READY":
        print("blockers:", pre['report']['blockers'][:4]); return 1
    contracts = orc.contracts_for(s.story_id)
    # 挑一个"有真实人脸的人物"的镜头
    one = None
    for c in contracts:
        for ch in c["assets"]["characters"]:
            if ch.get("base_face_image"):
                one = c; break
        if one: break
    one = one or contracts[0]
    print(f"选中镜头 {one['shot_id']}，人物参考图："
          f"{[ch.get('base_face_image') for ch in one['assets']['characters']]}")

    if args.dry_run or args.vid_budget <= 0:
        h("DRY-RUN：只到真实人脸，不出视频")
        print("已产出真实角色人脸参考；加 --vid-budget N --yes 才出真实视频。")
        print(f"人脸目录：{orc.bundle_for(s.story_id).root / '02_cast'}")
        return 0

    # 压到 5 秒并重签
    one["status"] = STATUS_DRAFT; one["duration_sec"] = 5.0; one["contract_hash"] = None
    sign_contract(one)

    h("4. Kling image2video（真实人脸驱动，视频预算闸）")
    vid_budget = BudgetGate(max_units=args.vid_budget, confirm=confirm,
                            allowed_durations_sec=(5,))
    backend = KlingImageToVideoBackend(budget=vid_budget, bundle_root=root,
                                       sleep_fn=time.sleep)
    svc = RenderBrainService(registry=BackendRegistry(default=backend),
                             bundle=orc.bundle_for(s.story_id))
    print("提交中…（真实 Kling image2video，轮询 ~1-2min）")
    result = svc.render_all([one])
    print("渲染:", json.dumps(result.summary(), ensure_ascii=False))
    print("视频预算:", json.dumps(vid_budget.summary(), ensure_ascii=False))
    print("图像预算:", json.dumps(img_budget.summary(), ensure_ascii=False))
    if not result.rendered:
        print("✗ 未出片:", result.failed or result.rejected); return 1

    r = result.rendered[0]
    mp4 = orc.bundle_for(s.story_id).path_for(r["file_relpath"])
    print(f"✓ 真实视频（真实人脸驱动）：{mp4}  ({mp4.stat().st_size} bytes)")

    h("5. 拼接")
    cut = assemble_rough_cut([one], story_id=s.story_id, bundle_id=s.bundle_id,
                             title="Phase A 样片")
    export_cut(cut, orc.bundle_for(s.story_id), video=True)
    print(f"成片：{orc.bundle_for(s.story_id).root / '10_outputs' / 'rough_cut.mp4'}")

    h("完成")
    print(f"Bundle: {orc.bundle_for(s.story_id).root}")
    print(f"真实人脸：{faces}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
