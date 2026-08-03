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

# Define Gradio UI
with gr.Blocks(title="CinemaDNA (DramaOS) Web Dashboard") as app:
    gr.Markdown("# CinemaDNA (DramaOS) Web Dashboard")

    with gr.Row():
        with gr.Column():
            gr.Markdown("### 系统状态面板 (System Status)")
            engine_status_text = gr.Markdown("🔴 引擎已关闭 (Stopped)")
            toggle_engine_btn = gr.Button("启动/关闭本地后台引擎")

        with gr.Column():
            gr.Markdown("### 流水线调度室 (Pipeline Control)")
            story_prompt_input = gr.Textbox(
                label="一句话剧本输入框 (Story Prompt Input)",
                value="男主角在暴雨中跪在女主家门前，请求原谅，气氛压抑。",
                lines=2
            )
            run_loop_btn = gr.Button("触发流水线 (Run Pipeline)", variant="primary")

    gr.Markdown("### 实时日志窗口 (Real-time Log Viewer)")
    log_output = gr.Textbox(label="Logs", lines=20, max_lines=30, interactive=False)

    # Event handlers
    toggle_engine_btn.click(fn=toggle_engine, outputs=engine_status_text)
    run_loop_btn.click(fn=run_mock_loop, inputs=story_prompt_input, outputs=log_output)

if __name__ == "__main__":
    app.launch()
