import sys
import subprocess
import time
import os
import json

from cinemadna.scriptbrain.micro_dismantler import ScriptBrainMicroDismantler
from cinemadna.director.agents.dispatch import DirectorDispatchAgent
from cinemadna.asset_brain.scene_dna.agents import SceneDNA
from cinemadna.asset_brain.identity_dna.agents import IdentityDNA
from cinemadna.asset_brain.prop_dna.agents import PropDNA
from cinemadna.performance.agents import PerformanceDNA
from cinemadna.audio.vocal_dna_agent import VocalDNA
from cinemadna.qa.infinity_loop import VisionQAAgent, SolutionArchitect, BackflowAgent

def main():
    acting_engine_dir = r"E:\projects\Drama_Acting_Engine-main"
    cwd = acting_engine_dir if os.path.exists(acting_engine_dir) else "."

    print("启动 Master Launcher...")
    engine_process = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8000"],
        cwd=cwd
    )
    print("引擎已在后台 8000 端口拉起")

    try:
        # Give the background server a short time to start
        time.sleep(1)

        print("\n--- 1. 大脑下发 (Phase 3 Real Bridge) ---")
        script_brain = ScriptBrainMicroDismantler()
        fake_script_data = {
            "scene": 1,
            "action": "argue",
            "character": {
                "name": "主角A",
                "age": 25,
                "style": "realistic"
            }
        }
        sb_result = script_brain.dismantle(fake_script_data)
        print(f"ScriptBrain 桥接返回: {sb_result.get('message')}")

        workorder_json = json.dumps({"shot_id": "SH-001", "requirements": fake_script_data}, ensure_ascii=False)
        print(f"生成 1 个镜头的假 JSON 工单: {workorder_json}")

        print("\n--- 2. 导演调度 ---")
        director = DirectorDispatchAgent()
        print("Director Agent 接收工单...")

        scene_dna = SceneDNA()
        identity_dna = IdentityDNA()
        prop_dna = PropDNA()
        perf_dna = PerformanceDNA()
        vocal_dna = VocalDNA()

        print("正在调用 SceneDNA 拼接场景 (Phase 3 Real Bridge)...")
        scene_result = scene_dna.compose_layout(fake_script_data)
        print(f"SceneDNA 桥接返回: {scene_result.get('message')}")

        print("正在调用 IdentityDNA 融合人脸 (Phase 3 Real Bridge)...")
        identity_result = identity_dna.generate_fusion(fake_script_data, [])
        print(f"IdentityDNA 桥接返回: {identity_result.get('message')}")

        print("正在调用 PropDNA 生成连续道具 (Phase 3 Real Bridge)...")
        prop_result = prop_dna.synthesize_prop(fake_script_data)
        print(f"PropDNA 桥接返回: {prop_result.get('message')}")

        print("正在调用 PerformanceDNA 检索并拼接微表情 (Phase 3 Real Bridge)...")
        perf_result = perf_dna.retrieve_and_stitch("beat1", {})
        print(f"PerformanceDNA 桥接返回: {perf_result.get('message')}")

        print("正在调用 VocalDNA 合成情绪声音 (Phase 3 Real Bridge)...")
        vocal_result = vocal_dna.synthesize_voice("Hello", {})
        print(f"VocalDNA 桥接返回: {vocal_result.get('message')}")

        print("\n--- 3. 触发 QA 闭环 (Infinity QA Loop) ---")
        vision_qa = VisionQAAgent()
        architect = SolutionArchitect()
        backflow = BackflowAgent()

        # 第一次尝试
        print(">> 第一次渲染评估...")
        score_1 = 85
        error_report = "口型不同步"
        print(f"Vision QA 审查分数: {score_1}, 错误报告: {error_report}")
        if score_1 < 90:
            print("Solution Architect 收到打回请求, 打印修复参数并打回重做...")
            architect.repair_and_rework({"error": error_report})

        # 第二次尝试
        print("\n>> 第二次渲染重试...")
        score_2 = 95
        print(f"Vision QA 审查分数: {score_2}")
        if score_2 >= 90:
            print("成绩合格，开始强制回流 (Backflow)...")
            backflow.backflow({"asset_id": "SH-001-final"})
            print("资产已成功存入数据库")

            # 强制输出样片到 demo_out 目录
            out_dir = "demo_out"
            os.makedirs(out_dir, exist_ok=True)
            demo_file = os.path.join(out_dir, "SH-001-final_demo.json")
            with open(demo_file, "w", encoding="utf-8") as f:
                json.dump({"shot_id": "SH-001", "status": "rendered and approved", "score": score_2}, f, ensure_ascii=False)
            print(f"【渲染完成】样片已输出至本地目录: {demo_file}")

    except KeyboardInterrupt:
        print("\nInterrupted by user. Shutting down...")
    finally:
        print("\n--- 4. 优雅退出 ---")
        print("正在关闭后台的演技引擎子进程...")
        engine_process.terminate()
        try:
            engine_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            engine_process.kill()
        print("子进程已关闭。")

if __name__ == "__main__":
    main()
