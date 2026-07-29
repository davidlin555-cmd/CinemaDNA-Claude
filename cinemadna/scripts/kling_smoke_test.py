"""Kling 真实出片最小验证：合约 → 真实视频 → 拼接（只跑一个 5 秒镜头）。

    python scripts/kling_smoke_test.py --budget 25 --yes      # 真实提交一次
    python scripts/kling_smoke_test.py --dry-run              # 只走到提交前（不花钱）

安全设计（本脚本的重点）：
- 预算闸**必须先上**：不给 --budget（>0）就是 dry-run，绝不真实提交。
- **只跑一个镜头**：走完整 ScriptBrain→资产→Director→Performance 拿到真实合约，
  但只渲染第一个镜头，最多消耗一次生成的 units。
- 逐次确认：--yes 才自动确认；否则命令行交互确认。
- 预算上限兜底：即使有 bug，也花不超过 --budget。

产出：真实 5 秒 MP4 落进 Bundle 的 09_downloads/，并拼成 10_outputs/rough_cut.mp4。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.assembly import assemble_rough_cut, export_cut  # noqa: E402
from render.budget import BudgetGate, confirm_interactive, confirm_yes  # noqa: E402
from render.kling_backend import KlingTextToVideoBackend  # noqa: E402
from render.registry import BackendRegistry  # noqa: E402
from render.service import RenderBrainService  # noqa: E402

THEME = "县城女护士深夜下班，走过空荡的医院走廊"


def h(t):
    print(f"\n{'=' * 68}\n {t}\n{'=' * 68}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=THEME)
    ap.add_argument("--budget", type=float, default=0.0,
                    help="预算上限(units)。0=dry-run 不真实提交")
    ap.add_argument("--yes", action="store_true", help="自动确认提交（否则命令行交互确认）")
    ap.add_argument("--dry-run", action="store_true", help="只走到提交前，不真实提交")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "workspace_kling"))
    args = ap.parse_args()

    # --- 前置检查 ---------------------------------------------------------
    h("0. 前置检查")
    if not config.has("KLING_API_KEY") or not config.has("KLING_API_BASE"):
        print("✗ 缺 KLING_API_KEY / KLING_API_BASE，请先配 .env"); return 1
    print(f"✓ KLING_API_BASE = {config.get('KLING_API_BASE')}")
    print(f"✓ 认证 = Authorization: Bearer <key>（key 不打印）")

    dry = args.dry_run or args.budget <= 0
    print(f"模式：{'DRY-RUN（不真实提交、不花钱）' if dry else f'真实提交，预算上限 {args.budget} units'}")

    # --- 预算闸（先上）----------------------------------------------------
    h("1. 预算闸")
    gate = BudgetGate(
        max_units=args.budget,                     # 0 → 未开启 → dry-run
        allowed_durations_sec=(5,),                # 只允许 5 秒
        confirm=confirm_yes if args.yes else confirm_interactive,
    )
    print(json.dumps({"armed": gate.armed, "max_units": gate.max_units,
                      "allowed_durations_sec": list(gate.allowed_durations_sec)},
                     ensure_ascii=False))

    # --- 真实管线到 Performance（全 mock、免费）拿到真实合约 --------------
    h("2. 跑到 Performance，拿真实 Shot Contract（免费）")
    orc = PipelineOrchestrator(bundle_root=Path(args.out), bundle_date="20260724")
    s = orc.create_story(title="Kling 最小验证")
    orc.run_scriptbrain(s.story_id, args.theme)
    orc.dispatch_assets(s.story_id)
    orc.run_director(s.story_id)
    orc.run_performance(s.story_id)
    contracts = orc.contracts_for(s.story_id)
    print(f"✓ 生成 {len(contracts)} 个合约，只取第 1 个做真实渲染")

    one = contracts[0]
    # 把时长压到 5 秒（预算闸只允许 5 秒），并重新签发（改了字段必须重签）
    from director.shot_contract import sign_contract, compute_contract_hash, STATUS_DRAFT
    one["status"] = STATUS_DRAFT
    one["duration_sec"] = 5.0
    one["contract_hash"] = None
    sign_contract(one)
    print(f"  镜头 {one['shot_id']}  时长 {one['duration_sec']}s  "
          f"提示词预览：{_prompt_preview(one)}")

    # --- 渲染（真实 Kling，预算闸前置）------------------------------------
    h("3. 渲染这一个镜头")
    backend = KlingTextToVideoBackend(budget=gate, sleep_fn=time.sleep)
    if dry:
        # dry-run：只走到"提交前授权"，预期被预算闸拦下，证明不会误花钱
        try:
            backend.submit(_req(one))
            print("✗ 意外：dry-run 竟然提交成功了"); return 1
        except Exception as e:
            print(f"✓ dry-run 如预期被拦：{type(e).__name__}: {e}")
        print("\nDRY-RUN 结束：预算闸有效，未发生任何真实提交。加 --budget N --yes 才真实出片。")
        return 0

    svc = RenderBrainService(registry=BackendRegistry(default=backend),
                             bundle=orc.bundle_for(s.story_id))
    print("提交中…（真实调用 Kling，轮询等待，约 30s–2min）")
    result = svc.render_all([one])

    print("\n渲染结果：", json.dumps(result.summary(), ensure_ascii=False))
    print("预算闸：", json.dumps(gate.summary(), ensure_ascii=False))
    if not result.rendered:
        print("✗ 未出片：", result.failed or result.rejected); return 1

    r = result.rendered[0]
    mp4 = orc.bundle_for(s.story_id).path_for(r["file_relpath"])
    print(f"✓ 真实视频：{mp4}  ({mp4.stat().st_size} bytes)")
    print(f"  is_real_media={r['is_real_media']}  is_generated_footage={r['is_generated_footage']}")

    # --- 拼接（证明"合约→视频→拼接"闭环）--------------------------------
    h("4. 拼接")
    cut = assemble_rough_cut([one], story_id=s.story_id, bundle_id=s.bundle_id,
                             title="Kling 最小验证")
    export_cut(cut, orc.bundle_for(s.story_id), video=True)
    print(json.dumps({"shot_count": cut["shot_count"],
                      "total_duration_sec": cut["total_duration_sec"],
                      "media_ready": cut["media_ready"],
                      "is_generated_footage": cut["is_generated_footage"],
                      "exports": {k: (v.get("relpath") if isinstance(v, dict) else v)
                                  for k, v in cut["exports"].items()}},
                     ensure_ascii=False, indent=1))

    h("完成")
    print(f"Bundle: {orc.bundle_for(s.story_id).root}")
    print(f"消耗预估 {gate.spent_units} units（真实用量以平台账单为准）")
    return 0


def _req(contract):
    from render.router import route_shot
    return RenderBrainService.build_request(contract, route_shot(contract))


def _prompt_preview(contract):
    return _req(contract).prompt[:50]


if __name__ == "__main__":
    raise SystemExit(main())
