"""样片通路演示：假剧本 → 资产 → Shot Contract → 渲染 → 镜头 QA → 假成片。

    python scripts/run_sample_pipeline_demo.py --benchmark
    python scripts/run_sample_pipeline_demo.py --theme "婆媳矛盾与养老困局"
    python scripts/run_sample_pipeline_demo.py --media mock      # 不出媒体，纯结构

对照主规格的标杆样片指标：1 场景、1 主角、5–8 镜头、30–60 秒。

产出（写在 Bundle 的 `10_outputs/`）：
  rough_cut.json   时间线 + 字幕 + EDL（结构化）
  rough_cut.edl    人读的镜头表
  subtitles.vtt    标准 WebVTT
  animatic.html    自包含动态分镜，浏览器打开按真实时长播
  rough_cut.mp4    **真实可播放 MP4**（需 ffmpeg；画面是占位色板）

诚实声明：`--media placeholder` 出的是**真实文件、占位画面**——
色板加字，没有任何模型参与作画。要真出片见文末「差什么」。
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asset_brain.common import schemas  # noqa: E402
from orchestrator import PipelineOrchestrator  # noqa: E402
from render.backend import ffmpeg_available  # noqa: E402
from render.registry import mock_registry, placeholder_registry  # noqa: E402

DEFAULT_THEME = "县城女护士被高利贷追债，最后逆袭翻身"

BENCHMARK_SCRIPT = {
    "schema_version": schemas.SCHEMA_SHOOTING_SCRIPT,
    "script_id": "script_benchmark",
    "title": "标杆样片·深夜出租屋",
    "characters": [{
        "character_id": "char_linwan", "name": "林晚", "role_type": "主角",
        "age_range": "25-28", "gender": "female",
        "personality_keywords": ["压抑", "坚韧", "疲惫"],
        "need_age_line": True, "need_family": False,
    }],
    "props": [{
        "prop_id": "prop_hospital_bill", "name": "医院缴费单",
        "description": "旧的纸质医院缴费单", "states_needed": ["完整"],
        "story_function": "建立经济压力", "continuity_critical": True,
    }],
    "episodes": [{"episode_id": "EP001", "scenes": [{
        "scene_id": "EP001_SC001", "episode_id": "EP001", "beat_type": "CONFLICT",
        "location_description": "县城老旧出租屋", "time_of_day": "深夜",
        "mood": "压抑、疲惫", "camera_intent": "中近景，压迫感",
        "required_atoms": ["室内布局", "光线", "家具陈设", "窗户"],
        "spatial_needs": "可支持人物走动与特写",
        "estimated_duration_sec": 40,
        "characters": ["char_linwan"], "props": ["prop_hospital_bill"],
        "prop_states": {"prop_hospital_bill": "完整"},
        "dialogue": [{"character_id": "char_linwan", "line": "我没有退路了。"}],
    }]}],
}


def h(t: str) -> None:
    print(f"\n{'=' * 74}\n {t}\n{'=' * 74}")


def jd(o) -> str:
    return json.dumps(o, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", default=DEFAULT_THEME)
    ap.add_argument("--out", default=None)
    ap.add_argument("--benchmark", action="store_true",
                    help="跑「1 场景 1 主角」标杆样片")
    ap.add_argument("--media", choices=("auto", "placeholder", "mock"), default="auto",
                    help="placeholder=出真实占位 MP4；mock=只出结构")
    args = ap.parse_args()

    root = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="cinemadna_cut_"))
    root.mkdir(parents=True, exist_ok=True)

    use_media = args.media == "placeholder" or (
        args.media == "auto" and ffmpeg_available()
    )
    registry = placeholder_registry() if use_media else mock_registry()

    orc = PipelineOrchestrator(bundle_root=root, bundle_date="20260723",
                               max_concurrent_scripts=1)

    # ---- 1. 剧本 ---------------------------------------------------------
    h("1. 剧本")
    s = orc.create_story(title="样片")
    if args.benchmark:
        orc.start_script(s.story_id)
        signal = orc.submit_script(s.story_id, script_ref="benchmark.json")
        script = BENCHMARK_SCRIPT
        print("使用手写标杆剧本：1 场景、1 主角、40 秒")
    else:
        res = orc.run_scriptbrain(s.story_id, args.theme)
        signal, script = res["parallel_signal"], None
        print(f"题材：{args.theme}")
        print(jd(res["summary"]))
    print(f"并行信号：new_story_allowed={signal['new_story_allowed']}")

    # ---- 2. 资产 ---------------------------------------------------------
    h("2. 资产匹配（查库 → 建单 → Gate → 回流）")
    disp = orc.dispatch_assets(s.story_id, script)
    print(f"结局：{disp['state']}   {disp['summary']['by_outcome']}")

    # ---- 3. Director -----------------------------------------------------
    h("3. DirectorDNA：排镜头 + 签合约 + 连续性账本")
    d = orc.run_director(s.story_id)
    print(jd(d["summary"]))
    for c in d["contracts"]:
        cam = c["camera"]
        print(f"  {c['order']:02d} {c['shot_id']:<26} {c['shot_type']:<13}"
              f" {c['duration_sec']:>5.1f}s  {cam['shot_size']}/{cam['movement']}")

    # ---- 4. Performance --------------------------------------------------
    h("4. PerformanceDNA：表演节拍 → 签发合约")
    p = orc.run_performance(s.story_id)
    print(jd(p["summary"]))

    # ---- 5. Render -------------------------------------------------------
    h(f"5. Render Brain（后端：{registry.default.name}）")
    r = orc.run_render(s.story_id, registry=registry)
    print(jd(r["summary"]))
    print("\n路由与实际执行后端：")
    for dec in r["result"].routing:
        print(f"  {dec['shot_id']:<26} {dec['model']:<16} {dec['tier']:<6}"
              f" → {dec['backend']:<18} real_backend={dec['real_backend']}")

    # ---- 6. 镜头级 QA ----------------------------------------------------
    h("6. 镜头级 QA + Repair Planner")
    q = orc.run_qa(s.story_id)
    print(f"结论：{q['verdict']}   {jd(q['summary'])}")
    if q["repair_plan"]["target_stage"]:
        print("修复计划：", jd(q["repair_plan"]))
    else:
        notes = orc._qa_results[s.story_id].warnings
        print("提示（不阻塞）：")
        for n in dict.fromkeys(notes):
            print(f"  · {n}")

    # ---- 7. 假成片 -------------------------------------------------------
    h("7. 拼接与导出「假成片」")
    cut = orc.assemble_rough_cut(s.story_id, export_media=True)
    print(f"镜头数 {cut['shot_count']}   总时长 {cut['total_duration_sec']}s"
          f"   30–60 秒达标：{cut['duration_in_target']}")
    print(f"media_ready={cut['media_ready']}  "
          f"is_generated_footage={cut['is_generated_footage']}")
    for line in cut["edl"]:
        print("  " + line)

    bundle_root = orc.bundle_for(s.story_id).root
    print("\n导出产物：")
    for key, val in (cut.get("exports") or {}).items():
        if isinstance(val, dict):
            if val.get("ok"):
                print(f"  {key:<10} {bundle_root / val['relpath']}  "
                      f"({val['bytes'] / 1024:.0f} KB, {val['duration_sec']}s)")
            else:
                print(f"  {key:<10} 未导出 —— {val.get('reason')}")
        else:
            print(f"  {key:<10} {bundle_root / val}")

    # ---- 8. 收尾 ---------------------------------------------------------
    h("8. 故事状态")
    story = orc.get_story(s.story_id)
    print(f"stage={story['stage']}  status={story['status']}")
    print(jd({
        "shots": story["shot_summary"].get("shot_count"),
        "render": story["render_summary"],
        "qa": story["qa_summary"],
        "cut": story["cut_summary"],
    }))
    print(f"\n完整产物目录: {root}")

    h("距离真正出片还差什么")
    gaps = [
        "画面不是生成的：占位后端只画色板，没有任何模型参与",
        "IdentityDNA / SceneDNA / PropDNA 仍是结构 mock，没有真实人脸/场景/道具素材",
        "QA 不看画面：只做结构判定 + mock 分数，人脸崩坏/动作畸变查不出来",
        "拼接只做无损 concat：没有转场、调色、音频",
        "AudioDNA / Post（配音、唇同步、BGM、字幕烧录）尚未实现",
    ]
    for g in gaps:
        print(f"  · {g}")
    print("\n接真实模型只需：实现一个 RenderBackend，然后")
    print("    registry.register('cloud-face-v1', YourBackend())")
    print("  其余链路（路由/参考注入/重试/一致性/拒渲/QA/拼接）不用改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
