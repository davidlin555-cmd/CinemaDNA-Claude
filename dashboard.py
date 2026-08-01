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
        acting_engine_dir = r"E:\projects\Drama_Acting_Engine-main"
        cwd = acting_engine_dir if os.path.exists(acting_engine_dir) else "."

        try:
            engine_process = subprocess.Popen(
                [sys.executable, "-m", "http.server", "8000"],
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


def run_mock_loop():
    logs = ""
    def log(msg):
        nonlocal logs
        logs += msg + "\n"
        return logs

    yield log("--- 1. 大脑下发 ---")
    time.sleep(0.5)

    script_brain = ScriptBrainMicroDismantler()
    fake_script_data = {"scene": 1, "action": "argue"}
    workorder_json = json.dumps({"shot_id": "SH-001", "requirements": fake_script_data}, ensure_ascii=False)
    yield log(f"ScriptBrain 生成了 1 个镜头的假 JSON 工单: {workorder_json}")
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

    yield log("正在调用 SceneDNA 拼接场景...")
    scene_dna.compose_layout(fake_script_data)
    time.sleep(0.3)

    yield log("正在调用 IdentityDNA 融合人脸...")
    identity_dna.generate_fusion(fake_script_data, [])
    time.sleep(0.3)

    yield log("正在调用 PropDNA 生成连续道具...")
    prop_dna.synthesize_prop(fake_script_data)
    time.sleep(0.3)

    yield log("正在调用 PerformanceDNA 检索并拼接微表情...")
    perf_dna.retrieve_and_stitch("beat1", {})
    time.sleep(0.3)

    yield log("正在调用 VocalDNA 合成情绪声音...")
    vocal_dna.synthesize_voice("Hello", {})
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
            run_loop_btn = gr.Button("触发一键空转测试 (Run Mock Loop)", variant="primary")

    gr.Markdown("### 实时日志窗口 (Real-time Log Viewer)")
    log_output = gr.Textbox(label="Logs", lines=20, max_lines=30, interactive=False)

    # Event handlers
    toggle_engine_btn.click(fn=toggle_engine, outputs=engine_status_text)
    run_loop_btn.click(fn=run_mock_loop, outputs=log_output)

if __name__ == "__main__":
    app.launch()
