"""Phase B 样片：三大资产真实出图闭环（Scene + Prop + Identity）+ 回流复用。

    python scripts/phase_b_scene_prop.py --img-budget 2 --yes
    python scripts/phase_b_scene_prop.py                 # 无预算 → mock，只验结构

验证用户要求的统一标准：
    解析需求 → 真实生成 → Gate → 写入 Contract → 互检 → 回流

流程（全部免费，除了 FLUX 出图很便宜）：
  1. ScriptBrain（免费）→ 剧本（场景/人物/道具需求）
  2. dispatch_assets：SceneDNA / IdentityDNA / PropDNA **全部真实出图**（图像预算闸）
  3. Director + Performance（免费）→ 每镜合约带真实场景图/人脸图/道具图路径
  4. PreRender Gate V2：require_real_assets=True，互检真实校验三大资产文件是否存在
  5. 回流复用：第二个故事复用同一剧本，命中全局库 → 不再出图、不再花钱

图像预算闸默认关闭（=走 mock）；--img-budget N 才真实出图，--yes 才自动确认。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from assets_real.image_backend import TogetherImageBackend  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.budget import BudgetGate, confirm_interactive, confirm_yes  # noqa: E402

THEME = "县城女护士深夜收到催债短信，翻出攥皱的医院缴费单和手机转账记录"


def h(t): print(f"\n{'=' * 68}\n {t}\n{'=' * 68}")


def _real_assets(orc):
    """从三大库里挑出带真实图的资产，供报告。"""
    scenes = [(a["asset_id"], a.get("scene_image"))
              for a in orc.store.scene_atoms.values() if a.get("is_real_image")]
    faces = [(c, p.get("base_face_image"))
             for c, p in orc.store.character_registry.items() if p.get("is_real_image")]
    props = [(pid, p.get("prop_images_by_state"))
             for pid, p in orc.store.prop_library.items() if p.get("is_real_image")]
    return scenes, faces, props


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=THEME)
    ap.add_argument("--img-budget", type=float, default=0.0, help="图像预算(units)")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--out", default=str(
        Path(__file__).resolve().parents[1] / "workspace_phase_b"))
    args = ap.parse_args()

    h("0. 前置")
    real_mode = args.img_budget > 0
    if real_mode and not config.has("TOGETHER_API_KEY"):
        print("✗ 缺 TOGETHER_API_KEY（真实出图需要）"); return 1
    confirm = confirm_yes if args.yes else confirm_interactive
    img_budget = BudgetGate(max_units=args.img_budget or 0.0, confirm=confirm,
                            allowed_durations_sec=(5,))
    img_backend = TogetherImageBackend(budget=img_budget) if real_mode else None
    print("✓ 真实出图模式（Together FLUX）" if real_mode else
          "⚠ mock 模式（未开 --img-budget）：只验结构，不出真实图")

    root = Path(args.out)
    orc = PipelineOrchestrator(bundle_root=root, bundle_date="20260724",
                               image_backend=img_backend)

    # ---- 故事 A：真实生成三大资产 --------------------------------------
    h("1-2. 故事A：ScriptBrain → dispatch（三大资产真实出图）")
    a = orc.create_story(title="Phase B 三大资产闭环")
    orc.run_scriptbrain(a.story_id, args.theme)
    orc.dispatch_assets(a.story_id)
    print("资产结局:", json.dumps(a.asset_summary["by_outcome"], ensure_ascii=False))

    scenes, faces, props = _real_assets(orc)
    print(f"\n真实场景图 ({len(scenes)}):")
    for sid, img in scenes:
        print(f"  {sid}: {img}")
    print(f"真实人脸图 ({len(faces)}):")
    for cid, img in faces:
        print(f"  {cid}: {img}")
    print(f"真实道具图 ({len(props)}):")
    for pid, imgs in props:
        print(f"  {pid}: {imgs}")

    h("3-4. 故事A：Director + Performance + PreRender Gate（真实资产校验）")
    orc.run_director(a.story_id)
    orc.run_performance(a.story_id)
    pre = orc.run_prerender_gate(a.story_id)
    print(f"PreRender Gate: {pre['summary']['status']}  "
          f"(require_real_assets={img_backend is not None})")
    bad = {k: v for k, v in pre['report']['flags'].items() if v != 'READY'}
    print("非 READY 的 flag:", bad or "全部 READY ✓")
    if bad:
        print("blockers:", json.dumps(pre['report']['blockers'][:6], ensure_ascii=False))

    # Contract V5 里三大资产真实路径 + is_real 标记
    v5 = orc.prerender_for(a.story_id)
    if v5 is not None and v5.contracts_v5:
        c0 = v5.contracts_v5[0]
        print("\nContract V5（首镜）真实资产投影:")
        print("  scene:", json.dumps(c0["scene_contract"], ensure_ascii=False))
        print("  identity:", json.dumps(c0["identity_contract"], ensure_ascii=False))
        print("  prop:", json.dumps(c0["prop_contract"], ensure_ascii=False))

    # ---- 故事 B：回流复用（同剧本 → 命中全局库，不再出图）--------------
    h("5. 故事B：回流复用（同需求命中全局库 → 不再花钱出图）")
    spent_before = img_budget.spent_units
    b = orc.create_story(title="Phase B 回流复用验证")
    # 同一主题再跑一遍：同样的场景/人物/道具需求 → 命中全局库
    orc.run_scriptbrain(b.story_id, args.theme)
    orc.dispatch_assets(b.story_id)
    print("资产结局:", json.dumps(b.asset_summary["by_outcome"], ensure_ascii=False))
    spent_after = img_budget.spent_units
    print(f"图像预算：复用前 {spent_before:.4f} → 复用后 {spent_after:.4f} units")
    if abs(spent_after - spent_before) < 1e-9:
        print("✓ 回流复用未产生任何新出图开销（三大资产跨剧复用成立）")
    else:
        print(f"⚠ 复用仍出了图（+{spent_after - spent_before:.4f}），检查 search_internal 命中")

    h("完成")
    print(f"图像预算总览: {json.dumps(img_budget.summary(), ensure_ascii=False)}")
    print(f"故事A Bundle: {orc.bundle_for(a.story_id).root}")
    print(f"故事B Bundle: {orc.bundle_for(b.story_id).root}")
    return 0 if not real_mode or pre['summary']['status'] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
