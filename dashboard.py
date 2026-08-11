import gradio as gr
import pandas as pd
import requests
import traceback

# Engine endpoint URL
ENGINE_URL = "http://127.0.0.1:8700"

def _post_to_engine(endpoint: str, payload: dict) -> dict:
    """Utility to post data to the local acting engine API."""
    try:
        response = requests.post(f"{ENGINE_URL}{endpoint}", json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Engine connection failed: {str(e)}")

# Dummy functions for callbacks
def parse_script(outline, style, aspect_ratio, episodes, progress=gr.Progress()):
    progress(0.2, desc="Parsing script...")
    try:
        result = _post_to_engine("/api/v1/script/parse", {"outline": outline, "style": style})
        shots = result.get("data", {}).get("shots", [])

        # Build Rich HTML Output replacing the DataFrame
        # Grouping simulated shots into fake episodes to demonstrate accordion layout
        ep_count = int(episodes.replace("集", "")) if episodes else 1
        html_content = "<div style='font-family: sans-serif;'>"

        for ep in range(1, ep_count + 1):
            html_content += f"""
            <details style="margin-bottom: 10px; border: 1px solid #ddd; border-radius: 5px; padding: 5px;">
                <summary style="font-weight: bold; cursor: pointer; padding: 5px; background-color: #f9f9f9;">
                    📺 第 {ep} 集：{style} 风格呈现 ({aspect_ratio})
                </summary>
                <div style="padding: 15px; margin-top: 10px;">
            """
            for idx, shot in enumerate(shots):
                html_content += f"""
                    <div style="margin-bottom: 20px; border-bottom: 1px dashed #eee; padding-bottom: 10px;">
                        <h4 style="color: #2c3e50; margin: 0 0 5px 0;">🎬 【场景 {idx+1}】 {shot.get('scene', '未知场景')}</h4>
                        <p style="margin: 0 0 5px 0;"><strong>🎥 【镜头 {shot.get('shot_id', '')}】</strong></p>
                        <p style="margin: 0 0 5px 0; color: #555;"><strong>🏃‍♂️ 【动作描写】</strong> {shot.get('action', '')} ({shot.get('character', '')})</p>
                        <p style="margin: 0 0 5px 0; font-style: italic; color: #34495e;"><strong>📝 【提示词】</strong> {shot.get('prompt', '')}</p>
                    </div>
                """
            html_content += """
                </div>
            </details>
            """
        html_content += "</div>"

        gate_feedback = result.get("gate_feedback", "无反馈")
        progress(1.0, desc="Done")
        return html_content, gate_feedback
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to parse script: {e}")

def generate_character(attributes, progress=gr.Progress()):
    progress(0.2, desc="Requesting Identity Generation...")
    try:
        # Call the injected mock API
        result = _post_to_engine("/api/v1/identity", {"attributes": attributes})
        image_url = result.get("data", {}).get("image_url")
        progress(1.0, desc="Asset Received")
        if image_url:
            return [image_url]  # gr.Gallery strictly expects a list of URLs/paths
        return []
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to generate character: {e}")

def generate_scene(attributes, progress=gr.Progress()):
    progress(0.2, desc="Requesting Scene Generation...")
    try:
        # Assuming the orchestrator eventually implements /api/v1/scene
        # For now, simulate the request.
        # result = _post_to_engine("/api/v1/scene", {"attributes": attributes})
        # image_url = result.get("data", {}).get("image_url")
        import time
        time.sleep(2)
        image_url = "https://via.placeholder.com/600x400.png?text=Mock+Scene"
        progress(1.0, desc="Asset Received")
        if image_url:
            return [image_url]
        return []
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to generate scene: {e}")

def lock_assets():
    return None

def render_shot(prompt, transition, progress=gr.Progress()):
    progress(0.2, desc="Rendering Shot...")
    try:
        result = _post_to_engine("/api/v1/performance/generate", {"prompt": prompt, "transition": transition})
        video_url = result.get("data", {}).get("video_url")
        gate_feedback = result.get("gate_feedback", "无反馈")
        progress(1.0, desc="Render Complete")
        return video_url, gate_feedback
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to render shot: {e}")

def synthesize_audio(dialogue, progress=gr.Progress()):
    progress(0.2, desc="Synthesizing Audio...")
    try:
        result = _post_to_engine("/api/v1/audio/synthesize", {"dialogue": dialogue})
        audio_url = result.get("data", {}).get("audio_url")
        gate_feedback = result.get("gate_feedback", "无反馈")
        progress(1.0, desc="Audio Synthesized")
        return audio_url, gate_feedback
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to synthesize audio: {e}")

def pass_gate():
    return "已审核通过，已锁定"

def reject_gate():
    return "已打回，请根据意见修改重试"

def assemble_final(progress=gr.Progress()):
    progress(0.2, desc="Assembling Final Video...")
    try:
        # Simulate final assembly API call
        # result = _post_to_engine("/api/v1/assemble", {})
        # video_url = result.get("data", {}).get("video_url")
        import time
        time.sleep(3)
        # Using a public sample video URL for demonstration purposes
        video_url = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
        progress(1.0, desc="Assembly Complete")
        return video_url # gr.Video expects a string URL or local path
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to assemble final video: {e}")

with gr.Blocks(title="DramaOS - Hollywood Director's Console") as demo:
    gr.Markdown("# DramaOS 导播台 (Director's Console)")

    with gr.Tabs():
        # Tab 1: 📝 剧本中枢 (ScriptBrain)
        with gr.Tab("📝 剧本中枢 (ScriptBrain)"):
            with gr.Group():
                with gr.Tabs():
                    with gr.Tab("上传剧本 (.docx/.txt)"):
                        script_file = gr.File(label="上传本地剧本文件")
                    with gr.Tab("AI 生剧本 (输入大纲)"):
                        script_outline = gr.Textbox(label="剧本大纲 (Script Outline)", placeholder="输入剧本大纲...", lines=5)
                        with gr.Row():
                            style_dropdown = gr.Dropdown(choices=["90年代写实", "复古叙事", "二次元", "3D 动画"], label="画风库 (Style)", value="90年代写实")
                            aspect_dropdown = gr.Dropdown(choices=["9:16", "16:9"], label="画幅比例 (Aspect Ratio)", value="9:16")
                            episode_dropdown = gr.Dropdown(choices=["1集", "5集", "10集", "15集"], label="预计集数 (Episodes)", value="1集")
                    with gr.Tab("自由画布"):
                        script_canvas = gr.Textbox(label="自由文本", placeholder="随意写下灵感...", lines=5)

            with gr.Row():
                parse_btn = gr.Button("🎬 解析与拆解剧本 (Parse and Dismantle Script)", variant="primary")

            with gr.Row():
                script_rich_output = gr.HTML(label="专业剧本分镜阅读区")

            with gr.Row():
                script_gate_feedback = gr.Textbox(label="🤖 Agent 质检报告 (Gatekeeper Feedback)", interactive=False)
            with gr.Row():
                script_approve_btn = gr.Button("✅ 审核通过并锁定 (Approve & Lock)")
                script_reject_btn = gr.Button("🔄 附带意见打回重做 (Reject & Regenerate)")

            parse_btn.click(
                fn=parse_script,
                inputs=[script_outline, style_dropdown, aspect_dropdown, episode_dropdown],
                outputs=[script_rich_output, script_gate_feedback]
            )
            script_approve_btn.click(fn=pass_gate, outputs=script_gate_feedback)
            script_reject_btn.click(fn=reject_gate, outputs=script_gate_feedback)

        # Tab 2: 🗃️ 资产工坊 (The DNA Forge)
        with gr.Tab("🗃️ 资产工坊 (The DNA Forge)"):
            with gr.Tabs():
                with gr.Tab("角色库 (Character Library)"):
                    with gr.Row():
                        with gr.Column():
                            char_attributes = gr.Textbox(label="角色属性 (Character Attributes)", lines=3)
                            char_gen_btn = gr.Button("生成角色 (Generate Character)")
                        with gr.Column():
                            char_gallery = gr.Gallery(label="角色定妆照/三视图 (Character Reference Sheets)")

                    char_gen_btn.click(fn=generate_character, inputs=char_attributes, outputs=char_gallery)

                with gr.Tab("场景库 (Scene Library)"):
                    with gr.Row():
                        with gr.Column():
                            scene_attributes = gr.Textbox(label="场景属性 (Scene Attributes)", lines=3)
                            scene_gen_btn = gr.Button("生成场景 (Generate Scene)")
                        with gr.Column():
                            scene_gallery = gr.Gallery(label="场景基准图 (Scene Reference Images)")

                    scene_gen_btn.click(fn=generate_scene, inputs=scene_attributes, outputs=scene_gallery)

            with gr.Row():
                lock_assets_btn = gr.Button("锁定全部资产并进入拍摄 (Lock All Assets and Proceed to Shooting)")

            lock_assets_btn.click(fn=lock_assets, inputs=[], outputs=[])

        # Tab 3: 🎬 片场导播台 (Director Studio)
        with gr.Tab("🎬 片场导播台 (Director Studio)"):
            with gr.Row():
                with gr.Column():
                    locked_assets_gallery = gr.Gallery(label="锁定资产参考 (Locked Asset References)")
                with gr.Column():
                    shot_prompt = gr.Textbox(label="当前分镜提示词 (Current Shot Prompt)", lines=4)
                    transition_checkbox = gr.Checkbox(label="启用首尾帧过渡 (Enable Start/End Frame Transition)")
                    render_btn = gr.Button("渲染当前镜头 (Render Current Shot)", variant="primary") # Orange/primary button
                with gr.Column():
                    shot_video = gr.Video(label="当前分镜片段 (Current Shot Video)")

            with gr.Row():
                perf_gate_feedback = gr.Textbox(label="🤖 Agent 质检报告 (Gatekeeper Feedback)", interactive=False)
            with gr.Row():
                perf_approve_btn = gr.Button("✅ 审核通过并锁定 (Approve & Lock)")
                perf_reject_btn = gr.Button("🔄 附带意见打回重做 (Reject & Regenerate)")

            render_btn.click(fn=render_shot, inputs=[shot_prompt, transition_checkbox], outputs=[shot_video, perf_gate_feedback])
            perf_approve_btn.click(fn=pass_gate, outputs=perf_gate_feedback)
            perf_reject_btn.click(fn=reject_gate, outputs=perf_gate_feedback)

        # Tab 4: 🎙️ 声音车间 (Vocal Workshop)
        with gr.Tab("🎙️ 声音车间 (Vocal Workshop)"):
            with gr.Row():
                dialogue_input = gr.Textbox(label="台词 (Dialogue)", placeholder="输入要合成的台词...")
                synth_btn = gr.Button("合成音频 (Synthesize Audio)")
            with gr.Row():
                audio_out = gr.Audio(label="合成结果 (Synthesized Audio)")

            with gr.Row():
                vocal_gate_feedback = gr.Textbox(label="🤖 Agent 质检报告 (Gatekeeper Feedback)", interactive=False)
            with gr.Row():
                vocal_approve_btn = gr.Button("✅ 审核通过并锁定 (Approve & Lock)")
                vocal_reject_btn = gr.Button("🔄 附带意见打回重做 (Reject & Regenerate)")

            synth_btn.click(fn=synthesize_audio, inputs=[dialogue_input], outputs=[audio_out, vocal_gate_feedback])
            vocal_approve_btn.click(fn=pass_gate, outputs=vocal_gate_feedback)
            vocal_reject_btn.click(fn=reject_gate, outputs=vocal_gate_feedback)

        # Tab 5: 🎞️ 终极剪辑室 (Final Assembly)
        with gr.Tab("🎞️ 终极剪辑室 (Final Assembly)"):
            with gr.Row():
                assemble_btn = gr.Button("一键合成最终成片 (One-Click Assemble Final Video)")
            with gr.Row():
                final_video = gr.Video(label="最终成片 (Final Assembled Video)")

            assemble_btn.click(fn=assemble_final, inputs=[], outputs=final_video)

if __name__ == "__main__":
    demo.launch()
