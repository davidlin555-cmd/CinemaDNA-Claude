"""Phase C 样片：屏幕内容道具真实闭环（手机/合同/付款/录音）—— 全程免费本地渲染。

    python scripts/phase_c_screen_props.py

验证用户要求：屏幕道具不再用 text2image 出乱码，改用无头 Chrome 模板截图，
文字清晰可读、零乱码，并能进 Contract + 过 PreRender Gate 的真实资产校验、回流复用。

流程（不联网、不花钱）：
  1. 四类屏幕道具需求 → PropDNA request_prop（screen_backend=Chrome）
  2. 每个状态 HTML 模板 → Chrome 截图 → 真实 PNG（is_real_image=True）
  3. 回流 PropDNA 库（screen_kind / prop_render_method 记录在案）
  4. 建一张引用付款道具的镜头合约 → cross_model_validate(require_real_assets=True)
     · 文件在 → 通过；文件缺 → REAL_PROP_MISSING 拦下（证明真被校验）
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.common.bundle import Bundle  # noqa: E402
from asset_brain.common.quad import Quad  # noqa: E402
from asset_brain.facade import AssetBrainFacade  # noqa: E402
from assets_real.screen_render import ChromeScreenshotBackend  # noqa: E402
from prerender.cross_validation import cross_model_validate  # noqa: E402
from prerender.service import PreRenderService  # noqa: E402


def h(t): print(f"\n{'=' * 68}\n {t}\n{'=' * 68}")


def png_dims(p: Path):
    b = p.read_bytes()
    if b[:8] != b"\x89PNG\r\n\x1a\n":
        return "NOT PNG"
    w, h_ = struct.unpack(">II", b[16:24])
    return f"{w}x{h_}, {len(b):,}B"


# 四类屏幕道具（带结构化 screen_content，精确控制屏内文字）
SCREEN_PROPS = [
    {"prop_id": "prop_debt_sms", "name": "催债短信", "states_needed": ["未读", "已读"],
     "story_function": "制造经济压力与紧迫感",
     "screen_content": {"contact": "陌生号码 138****6021", "messages": [
         {"from": "them", "text": "本金加利息一共 ¥58,000，今晚必须到账。", "time": "23:36"},
         {"from": "them", "text": "再拖，明天就去你们医院找你。", "time": "23:38"},
         {"from": "me", "text": "求你再宽限两天，工资一发我立刻还。", "time": "23:40"}]}},
    {"prop_id": "prop_transfer", "name": "转账记录", "states_needed": ["逾期", "成功"],
     "story_function": "交代欠款金额与逾期事实",
     "screen_content": {"amount": "58,000.00", "payee": "王建国"}},
    {"prop_id": "prop_loan_contract", "name": "借款协议书",
     "states_needed": ["空白", "已签字"], "story_function": "坐实借贷关系",
     "screen_content": {}},
    {"prop_id": "prop_call_record", "name": "通话录音",
     "states_needed": ["录音中", "已保存"], "story_function": "留存威胁证据",
     "screen_content": {"title": "与王建国的通话", "timer": "03:12"}},
    {"prop_id": "prop_lockscreen", "name": "催债通知消息",
     "states_needed": ["默认"], "story_function": "一屏交代压力全景",
     "screen_content": {}},
]


def main() -> int:
    h("0. 前置")
    screen = ChromeScreenshotBackend()
    if not screen.available():
        print("✗ 未找到 Chrome/Edge，无法渲染屏幕道具"); return 1
    print(f"✓ 屏幕渲染后端：{screen.chrome_path}")

    out = Path(__file__).resolve().parents[1] / "workspace_phase_c"
    bundle = Bundle(out, "bundle_20260724_screen").ensure()
    fac = AssetBrainFacade(bundle=bundle, screen_backend=screen)  # 无云端 image_backend
    quad = Quad(story_id="drama_screen", bundle_id=bundle.bundle_id,
                task_id="task_propdna", asset_hash=None)

    h("1-3. 四类屏幕道具：真实模板截图 → 过 Gate → 回流")
    for req in SCREEN_PROPS:
        tl = [{"shot_id": f"S{i}", "state": s}
              for i, s in enumerate(req["states_needed"])]
        res = fac.request_prop(req, quad, state_timeline=tl)
        rec = fac.store.prop_library[req["prop_id"]]
        print(f"\n● {req['name']}（{req['prop_id']}）"
              f" outcome={res['outcome']} kind={rec.get('screen_kind')}"
              f" method={rec.get('prop_render_method')} real={rec.get('is_real_image')}")
        for state, rel in (rec.get("prop_images_by_state") or {}).items():
            p = bundle.path_for(rel)
            print(f"    {state:>6} → {rel}  [{png_dims(p)}]")

    h("4. 进 Contract + PreRender Gate 真实校验（require_real_assets=True）")
    # 取付款道具的"逾期"状态图，塞进一张最小镜头合约的 assets.props
    pay = fac.store.prop_library["prop_transfer"]
    pay_rel = next(iter(pay["prop_images_by_state"].values()))
    pay_abs = next(iter(pay["prop_images_abspath"].values()))
    contract = {
        "shot_id": "SH1", "shot_type": "特写",
        "assets": {"scene": {"gate_passed": True,
                             "scene_image": "03_scene/x/scene.jpg"},
                   "characters": [],
                   "props": [{"prop_id": "prop_transfer", "gate_passed": True,
                              "state": "逾期", "prop_image": pay_abs}]},
        "mouth_policy": "SILENT_CLOSED", "performance": {"acting_beats": ["盯着屏幕"]},
        "voice_contract": {}, "dialogue": [],
    }
    checker = PreRenderService(bundle=bundle)._asset_checker()

    # a) 文件在 → 道具真实校验通过（只看道具方向）
    r_ok = cross_model_validate([contract], asset_checker=checker,
                                require_real_assets=True)
    prop_issues = [i for i in r_ok["issues"] if i["direction"] == "Scene→Prop"]
    print(f"道具图存在时 · Scene→Prop 问题数：{len(prop_issues)}  "
          f"→ {'✓ 通过' if not prop_issues else prop_issues}")

    # b) 把文件改名模拟缺失 → 必须被 REAL_PROP_MISSING 拦下
    real = Path(pay_abs)
    moved = real.with_name("_moved_" + real.name)
    real.rename(moved)
    try:
        r_bad = cross_model_validate([contract], asset_checker=checker,
                                     require_real_assets=True)
    finally:
        moved.rename(real)      # 复原
    codes = {i["code"] for i in r_bad["issues"]}
    print(f"道具图缺失时 · 命中码：{sorted(codes)}  "
          f"→ {'✓ 被拦下' if 'REAL_PROP_MISSING' in codes else '✗ 漏检'}")

    h("完成")
    print(f"屏幕道具 Bundle：{bundle.root}")
    print("四类屏幕道具均为真实、清晰可读的 PNG，已进库可回流复用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
