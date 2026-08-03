import gradio as gr
import subprocess
import sys
import os
import time
import json

from cinemadna.scriptbrain.micro_dismantler import ScriptBrainMicroDismantler
from cinemadna.director.agents.dispatch import DirectorDispatchAgent
from cinemadna.asset_brain.scene_dna.agents import SceneDNA
from cinemadna.asset_brain.identity_dna.agents import IdentityDNA
from cinemadna.asset_brain.prop_dna.agents import PropDNA
from cinemadna.performance.agents import PerformanceDNA
from cinemadna.audio.vocal_dna_agent import VocalDNA
from cinemadna.qa.infinity_loop import VisionQAAgent, SolutionArchitect, BackflowAgent

# Global state for the engine process
engine_process = None

def toggle_engine():
    global engine_process

    if engine_process is None or engine_process.poll() is not None:
        # Need to start
        acting_engine_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acting_engine")
        cwd = acting_engine_dir if os.path.exists(acting_engine_dir) else "."

        try:
            engine_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "main:app", "--port", "8000"],
                cwd=cwd
            )
            return "🟢 引擎已在后台 8000 端口拉起 (Running)"
        except Exception as e:
            return f"🔴 启动失败: {str(e)}"
    else:
        # Need to stop
        engine_process.terminate()
        try:
            engine_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            engine_process.kill()
        engine_process = None
        return "🔴 引擎已关闭 (Stopped)"


def run_mock_loop(story_prompt):
    logs = ""
    def log(msg):
        nonlocal logs
        logs += msg + "\n"
        return logs

    yield log(f"输入的一句话剧本: {story_prompt}")
    yield log("\n--- 1. 大脑下发 (Phase 3 Real Bridge + LLM) ---")
    time.sleep(0.5)

    script_brain = ScriptBrainMicroDismantler()

    yield log("正在请求大模型拆解剧本 (如果配置了 API Key，将调用真实 LLM)...")
    sb_result = script_brain.dismantle(story_prompt)
    yield log(f"ScriptBrain 桥接返回状态: {sb_result.get('message')}")

    # 获取拆解出来的数据，如果没有配置LLM或者报错，给一个兜底结构
    dismantled_data = sb_result.get("data", [])
    if not dismantled_data:
         dismantled_data = [{
            "shot_id": "SH-001",
            "scene_prompt": "A default arguing scene",
            "action": "argue",
            "character_details": {
                "name": "主角A",
                "age": 25,
                "style": "realistic"
            }
        }]

    # 这里我们只取第一个镜头往下流转作为展示
    fake_script_data = dismantled_data[0]

    workorder_json = json.dumps(dismantled_data, ensure_ascii=False, indent=2)
    yield log(f"\n大模型生成的完整 JSON 工单流:\n{workorder_json}\n")
    time.sleep(0.5)

    yield log("\n--- 2. 导演调度 ---")
    director = DirectorDispatchAgent()
    yield log("Director Agent 接收工单...")
    time.sleep(0.5)

    scene_dna = SceneDNA()
    identity_dna = IdentityDNA()
    prop_dna = PropDNA()
    perf_dna = PerformanceDNA()
    vocal_dna = VocalDNA()

    yield log("正在调用 SceneDNA 拼接场景 (Phase 3 Real Bridge)...")
    scene_result = scene_dna.compose_layout(fake_script_data)
    yield log(f"SceneDNA 桥接返回: {scene_result.get('message')}")
    time.sleep(0.3)

    yield log("正在调用 IdentityDNA 融合人脸 (Phase 3 Real Bridge)...")
    identity_result = identity_dna.generate_fusion(fake_script_data, [])
    yield log(f"IdentityDNA 桥接返回: {identity_result.get('message')}")
    time.sleep(0.3)

    yield log("正在调用 PropDNA 生成连续道具 (Phase 3 Real Bridge)...")
    prop_result = prop_dna.synthesize_prop(fake_script_data)
    yield log(f"PropDNA 桥接返回: {prop_result.get('message')}")
    time.sleep(0.3)

    yield log("正在调用 PerformanceDNA 检索并拼接微表情 (Phase 3 Real Bridge)...")
    perf_result = perf_dna.retrieve_and_stitch("beat1", {})
    yield log(f"PerformanceDNA 桥接返回: {perf_result.get('message')}")
    time.sleep(0.3)

    yield log("正在调用 VocalDNA 合成情绪声音 (Phase 3 Real Bridge)...")
    vocal_result = vocal_dna.synthesize_voice("Hello", {})
    yield log(f"VocalDNA 桥接返回: {vocal_result.get('message')}")
    time.sleep(0.5)

    yield log("\n--- 3. 触发 QA 闭环 (Infinity QA Loop) ---")
    vision_qa = VisionQAAgent()
    architect = SolutionArchitect()
    backflow = BackflowAgent()
    time.sleep(0.5)

    # 第一次尝试
    yield log(">> 第一次渲染评估...")
    score_1 = 85
    error_report = "口型不同步"
    yield log(f"Vision QA 审查分数: {score_1}, 错误报告: {error_report}")
    time.sleep(0.5)

    if score_1 < 90:
        yield log("Solution Architect 收到打回请求, 打印修复参数并打回重做...")
        architect.repair_and_rework({"error": error_report})
        time.sleep(1.0)

    # 第二次尝试
    yield log("\n>> 第二次渲染重试...")
    score_2 = 95
    yield log(f"Vision QA 审查分数: {score_2}")
    time.sleep(0.5)

    if score_2 >= 90:
        yield log("成绩合格，开始强制回流 (Backflow)...")
        backflow.backflow({"asset_id": "SH-001-final"})
        yield log("资产已成功存入数据库")

        # 强制输出样片到 demo_out 目录
        out_dir = "demo_out"
        os.makedirs(out_dir, exist_ok=True)
        demo_file = os.path.join(out_dir, "SH-001-final_demo.json")
        with open(demo_file, "w", encoding="utf-8") as f:
            json.dump({"shot_id": "SH-001", "status": "rendered and approved", "score": score_2}, f, ensure_ascii=False)
        yield log(f"【渲染完成】样片已输出至本地目录: {demo_file}")

        time.sleep(0.5)

    yield log("\n--- 测试流程结束 ---")

# Phase 4 Refactored Logic for Tabbed Workflow
def step1_dismantle_script(story_prompt):
    script_brain = ScriptBrainMicroDismantler()
    sb_result = script_brain.dismantle(story_prompt)
    dismantled_data = sb_result.get("data", [])
    if not dismantled_data:
         dismantled_data = [{
            "shot_id": "SH-001",
            "Camera_and_Lighting": {"description": "特写，光线昏暗"},
            "SceneDNA": {"description": "暴雨中的街道"},
            "IdentityDNA": {"description": "男主，浑身湿透"},
            "PerformanceDNA": {"description": "眉头紧锁"},
            "PropDNA": {"description": "无"},
            "VocalDNA": {"description": "无"}
        }]
    return json.dumps(dismantled_data, ensure_ascii=False, indent=2), dismantled_data

def step2_scene_dna(edited_json_str):
    try:
        script_data = json.loads(edited_json_str)
    except:
        return None, "JSON 格式错误，请检查！"

    scene_dna = SceneDNA()
    # Mock visual feedback by returning a dummy image structure (or empty list for now since we don't have real images)
    # The agent will just print to console via the bridge
    scene_dna.compose_layout(script_data[0] if isinstance(script_data, list) else script_data)

    return [], "SceneDNA 场景原图已生成（模拟），请审核！"

def step3_identity_and_performance(edited_json_str):
    try:
        script_data = json.loads(edited_json_str)
    except:
        return None, "JSON 格式错误，请检查！"

    first_shot = script_data[0] if isinstance(script_data, list) else script_data

    identity_dna = IdentityDNA()
    prop_dna = PropDNA()
    perf_dna = PerformanceDNA()
    vocal_dna = VocalDNA()

    identity_dna.generate_fusion(first_shot, [])
    prop_dna.synthesize_prop(first_shot)
    perf_dna.retrieve_and_stitch("beat1", {})
    vocal_dna.synthesize_voice("Hello", {})

    return None, "表演与角色换脸视频生成完毕（模拟），等待最终审核！"

# Define Gradio UI
with gr.Blocks(title="CinemaDNA (DramaOS) Web Dashboard") as app:
    gr.Markdown("# CinemaDNA (DramaOS) Web Dashboard - 人机协同控制台")

    # Global state to pass the JSON across tabs
    script_state = gr.State([])

    with gr.Row():
        gr.Markdown("### 引擎控制 (System Status)")
        engine_status_text = gr.Markdown("🔴 引擎已关闭 (Stopped)")
        toggle_engine_btn = gr.Button("启动/关闭本地后台引擎")

    with gr.Tabs():
        with gr.Tab("Tab 1: 剧本中枢 (ScriptBrain)"):
            story_prompt_input = gr.Textbox(
                label="一句话剧本输入框 (Story Prompt Input)",
                value="男主角在暴雨中跪在女主家门前，请求原谅，气氛压抑。",
                lines=2
            )
            dismantle_btn = gr.Button("运行拆解 (Run ScriptBrain)", variant="primary")
            json_editor = gr.Code(label="JSON 分镜单 (可手动修改)", language="json", interactive=True)

        with gr.Tab("Tab 2: 场景生图审核 (SceneDNA)"):
            scene_generate_btn = gr.Button("生成场景原图 (Generate Scenes)", variant="primary")
            scene_gallery = gr.Gallery(label="SceneDNA 场景生成结果")
            scene_status = gr.Markdown("等待生成...")
            with gr.Row():
                scene_approve_btn = gr.Button("同意并进入下一环节 (Approve)", variant="secondary")
                scene_reject_btn = gr.Button("打回重绘 (Reject)", variant="stop")

        with gr.Tab("Tab 3: 角色与表演审核 (Identity & Performance)"):
            video_generate_btn = gr.Button("生成视频片段 (Generate Video)", variant="primary")
            final_video = gr.Video(label="Identity & Performance 动态生成结果")
            video_status = gr.Markdown("等待生成...")
            with gr.Row():
                video_approve_btn = gr.Button("最终审核通过 (Final Approve)", variant="secondary")
                video_reject_btn = gr.Button("打回重拍 (Reject)", variant="stop")

    # Event handlers
    toggle_engine_btn.click(fn=toggle_engine, outputs=engine_status_text)

    # Tab 1
    dismantle_btn.click(fn=step1_dismantle_script, inputs=story_prompt_input, outputs=[json_editor, script_state])

    # Tab 2
    scene_generate_btn.click(fn=step2_scene_dna, inputs=json_editor, outputs=[scene_gallery, scene_status])
    scene_approve_btn.click(fn=lambda: "审核通过，请前往 Tab 3", outputs=scene_status)
    scene_reject_btn.click(fn=lambda: "已打回，请修改 JSON 或重新生成", outputs=scene_status)

    # Tab 3
    video_generate_btn.click(fn=step3_identity_and_performance, inputs=json_editor, outputs=[final_video, video_status])
    video_approve_btn.click(fn=lambda: "【全流程完成】数据已回流资产库！", outputs=video_status)
    video_reject_btn.click(fn=lambda: "【打回】请重新调整角色或表演参数！", outputs=video_status)

if __name__ == "__main__":
    app.launch()
