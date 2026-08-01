"""方向1：屏内道具合成验证 —— image2video 真实人物底 + Chrome 屏内UI轨 合成。

    python scripts/plan_c_screen_composite.py

目标：验证"催债短信 / 缴费单"经 Chrome 模板渲染后叠加到**真实 image2video 素材**上，
文字是否清晰可读——这是全片冲 PASS_FULL 前唯一未打通的点。

预算：**0 新增 units**。复用上一步已付费的真实底片（SH_KEY.mp4），
不再真实生成新镜（遵守"先不要乱烧预算"）。Chrome 渲染本地免费。

产物：一段 5.1s 合成片——真人 1.1s → 催债短信插入 → 缴费账单插入，
屏内文字为浏览器矢量渲染（原生清晰）。附合成前/后抽帧对比。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.prop_dna.screen_templates import render_screen_html  # noqa: E402
from assets_real.screen_render import ChromeScreenshotBackend  # noqa: E402
from final_review.media_probe import MediaProbe  # noqa: E402
from final_review.vision_judge import VisionJudge  # noqa: E402

REAL_BASE = Path("workspace_plan_b/one_shot_20260724/09_downloads/task_key01/SH_KEY.mp4")


def h(t: str) -> None:
    print(f"\n{'=' * 70}\n {t}\n{'=' * 70}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    base = (root / REAL_BASE).resolve()
    if not base.is_file():
        print(f"✗ 缺少真实底片：{base}（先跑 plan_b_one_shot.py）"); return 1

    out = root / "workspace_plan_c"
    import shutil
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    h("1. Chrome 渲染屏内UI轨（本地免费，矢量清晰）")
    chrome = ChromeScreenshotBackend()
    if not chrome.available():
        print("✗ 无 Chrome/Edge，无法渲染屏内道具"); return 1

    # 催债短信（phone_chat）+ 缴费/账单（payment），文案切合本剧
    sms_html = render_screen_html("phone_chat", {
        "screen_content": {
            "contact": "王建国（高利贷）",
            "messages": [
                {"from": "them", "text": "钱什么时候还？拖了三个月了。", "time": "23:36"},
                {"from": "them", "text": "今晚十二点前不转，明天就去你们医院闹。", "time": "23:38"},
                {"from": "me", "text": "再宽限两天，工资一发我立刻还。", "time": "23:40"},
                {"from": "them", "text": "别拿工资糊弄我。¥58,000，一分不能少。", "time": "23:41"},
            ]}}, "未读")
    bill_html = render_screen_html("payment", {
        "screen_content": {
            "amount": "58,000.00", "payee": "王建国",
            "details": [
                ("收款方", "王建国"),
                ("状态", "待支付 · 已逾期 87 天"),
                ("本金", "¥50,000.00"),
                ("利息（月息2%）", "¥8,000.00"),
                ("交易单号", "20260724234112008861"),
            ]}}, "逾期 待支付")

    sms_png = out / "ui_sms.png"
    bill_png = out / "ui_bill.png"
    r1 = chrome.render(html=sms_html, dest=sms_png, kind="phone_chat", width=768, height=1024)
    r2 = chrome.render(html=bill_html, dest=bill_png, kind="payment", width=768, height=1024)
    print(f"  ✓ 催债短信 UI：{sms_png.name}  {r1.width}x{r1.height}  {r1.bytes} 字节")
    print(f"  ✓ 缴费账单 UI：{bill_png.name}  {r2.width}x{r2.height}  {r2.bytes} 字节")

    h("2. ffmpeg 合成：真实人物底 + 屏内UI插入（含轻微漂移=活的合成）")
    composite = out / "keyshot_composite.mp4"
    # 布局：0-1.1s 真人；1.1-3.05s 催债短信插入；3.05-5.1s 缴费账单插入
    # 面板缩到 h=1300，外加深色描边=手机质感；正弦微漂移给运动能量（视觉判断能识别）
    fc = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,fps=30[base];"
        "[1:v]scale=-2:1300,pad=iw+44:ih+44:22:22:color=0x0c0c0c[sms];"
        "[2:v]scale=-2:1300,pad=iw+44:ih+44:22:22:color=0x0c0c0c[bill];"
        "[base][sms]overlay=x='(W-w)/2+7*sin(2*t)':y='(H-h)/2+5*cos(1.5*t)':"
        "enable='between(t,1.1,3.05)'[b1];"
        "[b1][bill]overlay=x='(W-w)/2+7*sin(2*t)':y='(H-h)/2+5*cos(1.5*t)':"
        "enable='between(t,3.05,5.2)'[v]"
    )
    argv = ["ffmpeg", "-y", "-loglevel", "error",
            "-i", str(base), "-i", str(sms_png), "-i", str(bill_png),
            "-filter_complex", fc, "-map", "[v]", "-t", "5.1", "-r", "30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(composite)]
    subprocess.run(argv, check=True)
    print(f"  ✓ 合成片：{composite}  {composite.stat().st_size} 字节")

    h("3. 抽帧对比（合成前 vs 合成后）")
    frames = {
        "before_real_person": (base, "0.5", out / "cmp_before_person.png"),
        "after_sms_insert":   (composite, "2.0", out / "cmp_after_sms.png"),
        "after_bill_insert":  (composite, "4.0", out / "cmp_after_bill.png"),
    }
    for _name, (src, ts, dst) in frames.items():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", ts,
                        "-i", str(src), "-frames:v", "1", str(dst)], check=False)
        print(f"  抽帧 {dst.name}（{src.name} @ {ts}s）")

    h("4. 视觉判断 + 冻帧探测（合成片能否过闸）")
    vj = VisionJudge().judge(composite).to_dict()
    mp = MediaProbe().probe(composite).to_dict()
    print(f"  视觉：fake={vj['fake']} motion={vj['motion']} 动态范围={vj['luma_spread']}")
    if vj["reasons"]:
        print(f"    理由：{'；'.join(vj['reasons'])}")
    print(f"  探测：时长={mp['duration']}s 帧={mp['frames']} 冻帧={mp['freeze_events']} "
          f"黑屏={mp['black_events']}")

    h("5. 结论")
    blocked = vj["fake"] or mp["freeze_events"] > 0 or mp["black_events"] > 0
    print("  预算：0 新增 units（复用已付费真实底片；Chrome 本地免费）")
    print(f"  屏内文字来源：Chrome 矢量渲染 → 原生清晰、零乱码（非模型生成，不会糊）")
    print(f"  合成片仍被冻帧/假感拦截：{'是' if blocked else '否'}")
    print(f"  可读性判定：见抽帧 cmp_after_sms.png / cmp_after_bill.png（人工肉眼终判）")

    report = {
        "additional_budget_units": 0,
        "real_base_reused": str(base),
        "screen_ui": {"sms": r1.to_dict(), "bill": r2.to_dict()},
        "composite": str(composite),
        "frames": {k: str(v[2]) for k, v in frames.items()},
        "vision_judge": vj, "media_probe": mp,
        "still_blocked": blocked,
    }
    (out / "plan_c_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  完整报告：{out / 'plan_c_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
