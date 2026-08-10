import gradio as gr
import pandas as pd
import requests
import traceback
from dotenv import load_dotenv
from cinemadna.scriptbrain.micro_dismantler import ScriptBrainMicroDismantler

load_dotenv()

# Backend API Helper
def _post_to_engine(endpoint, payload):
    url = f"http://127.0.0.1:8700{endpoint}"
    response = requests.post(url, json=payload, headers={'Content-Type': 'application/json'})
    response.raise_for_status()
    return response.json()

# Real Backend Integrations
def parse_script(file, outline, canvas, style, ratio, progress=gr.Progress()):
    progress(0.1, desc="Reading inputs...")
    file_content = ""
    if file:
        try:
            with open(file.name, "r", encoding="utf-8") as f:
                file_content = f.read()
        except Exception as e:
            raise gr.Error(f"Failed to read file: {e}")

    actual_script = outline or canvas or file_content
    print("========== 收到前端解析请求 ==========")
    print(f"收到剧本大纲: {actual_script}")
    progress(0.3, desc="Dismantling script using ScriptBrain AI...")
    try:
        dismantler = ScriptBrainMicroDismantler()
        script_data = {"outline": actual_script, "style": style, "ratio": ratio}

        # Call the actual backend dismantler logic
        result = dismantler.dismantle(script_data)

        # Ensure result is a list for iteration
        if not isinstance(result, list):
            result = [result] if result else []

        markdown_str = ""
        current_scene = None

        for shot in result:
            # Safely extract fields
            scene_dna = shot.get("SceneDNA", "")
            shot_num = shot.get("镜号", "")
            cam_light = shot.get("Camera_Lighting", "")
            ident = shot.get("IdentityDNA", "")
            perf = shot.get("PerformanceDNA", "")
            prop = shot.get("PropDNA", "")
            vocal = shot.get("VocalDNA", "")

            # If scene changes or first scene, print the Scene header
            if scene_dna != current_scene:
                current_scene = scene_dna
                markdown_str += f"\n### 【场景】 {scene_dna}\n"

            # Format the shot list element
            markdown_str += f"* **镜头 {shot_num}**：{cam_light} - {ident} {perf}。{prop}。\n"
            if vocal:
                markdown_str += f"> \"{vocal}\"\n"

        # Convert the returned result JSON array into a dataframe with exact headers
        df = pd.DataFrame(result, columns=[
            "镜号", "Camera_Lighting", "SceneDNA", "IdentityDNA", "PerformanceDNA", "PropDNA", "VocalDNA"
        ])

        return markdown_str, df

    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def generate_identity(attributes, batch_size, progress=gr.Progress()):
    progress(0.2, desc="Generating identity assets...")
    try:
        response = _post_to_engine('/api/v1/identity', {"attributes": attributes, "batch_size": batch_size})
        return response.get("images", [])
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def generate_scene(attributes, batch_size, progress=gr.Progress()):
    progress(0.2, desc="Generating scene assets...")
    try:
        response = _post_to_engine('/api/v1/scene', {"attributes": attributes, "batch_size": batch_size})
        return response.get("images", [])
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def generate_prop(attributes, batch_size, progress=gr.Progress()):
    progress(0.2, desc="Generating prop assets...")
    try:
        response = _post_to_engine('/api/v1/prop', {"attributes": attributes, "batch_size": batch_size})
        return response.get("images", [])
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def lock_identity(index, progress=gr.Progress()):
    progress(0.9, desc="Locking identity asset...")
    try:
        response = _post_to_engine('/api/v1/identity/lock', {"index": index})
        return f"已锁定身份资产编号: {index}"
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def lock_scene(index, progress=gr.Progress()):
    progress(0.9, desc="Locking scene asset...")
    try:
        response = _post_to_engine('/api/v1/scene/lock', {"index": index})
        return f"已锁定场景基准图编号: {index}"
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def lock_prop(index, progress=gr.Progress()):
    progress(0.9, desc="Locking prop asset...")
    try:
        response = _post_to_engine('/api/v1/prop/lock', {"index": index})
        return f"已锁定道具资产编号: {index}"
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def render_performance(micro, macro, progress=gr.Progress()):
    progress(0.3, desc="Rendering performance video...")
    try:
        response = _post_to_engine('/api/v1/performance', {"micro_expression": micro, "macro_action": macro})
        return response.get("video_url")
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def synthesize_voice(text, emotion, progress=gr.Progress()):
    progress(0.4, desc="Synthesizing voice audio...")
    try:
        response = _post_to_engine('/api/v1/vocal', {"text": text, "emotion": emotion})
        return response.get("audio_url")
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

def assemble_final(progress=gr.Progress()):
    progress(0.5, desc="Assembling final cut...")
    try:
        response = _post_to_engine('/api/v1/assemble', {})
        return response.get("final_video_url")
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(str(e))

with gr.Blocks(title="DramaOS - Hollywood Director's Console") as demo:
    gr.Markdown("# DramaOS 导播台 (Director's Console)")

    with gr.Tabs():
        # Tab 1: 📝 剧本中枢 (ScriptBrain)
        with gr.Tab("📝 剧本中枢 (ScriptBrain)"):
            with gr.Group():
                gr.Markdown("### 剧本创作 (Script Creation)")
                with gr.Tabs():
                    with gr.Tab("上传剧本 (.docx/.txt)"):
                        script_file = gr.File(label="上传剧本文件", file_types=[".txt", ".docx"])
                    with gr.Tab("AI 生剧本 (输入大纲)"):
                        script_outline = gr.Textbox(label="剧本大纲", placeholder="输入剧本大纲让 AI 生成详细剧本...", lines=5)
                    with gr.Tab("自由画布"):
                        script_canvas = gr.Textbox(label="自由编辑区", placeholder="在这里自由书写...", lines=5)

                with gr.Row():
                    style_dropdown = gr.Dropdown(choices=["写实 (Realistic)", "3D渲染 (3D Render)", "二次元动漫 (Anime)"], label="画风 (Style)", value="写实 (Realistic)")
                    ratio_dropdown = gr.Dropdown(choices=["9:16 (竖屏)", "16:9 (横屏)", "1:1 (方块)"], label="视频比例 (Aspect Ratio)", value="9:16 (竖屏)")
                    parse_btn = gr.Button("立即创作 / 解析剧本", variant="primary")

            gr.Markdown("### 剧本拆解成果 (Dismantled Results)")
            with gr.Tabs():
                with gr.Tab("视图 A (文本流分镜)"):
                    shot_list_md = gr.Markdown("此处展示流式分镜文本...", label="文本流分镜")
                with gr.Tab("视图 B (五大 DNA 任务表)"):
                    shot_list_df = gr.Dataframe(
                        headers=["镜号", "Camera_Lighting", "SceneDNA", "IdentityDNA", "PerformanceDNA", "PropDNA", "VocalDNA"],
                        label="分镜单 (Shot List)",
                        interactive=True,
                        row_count=5
                    )

            parse_btn.click(fn=parse_script, inputs=[script_file, script_outline, script_canvas, style_dropdown, ratio_dropdown], outputs=[shot_list_md, shot_list_df])

        # Tab 2: 🗃️ 静态资产工坊 (Static DNA Forge)
        with gr.Tab("🗃️ 静态资产工坊 (Static DNA Forge)"):
            with gr.Tabs():
                with gr.Tab("IdentityDNA (角色库)"):
                    with gr.Row():
                        with gr.Column():
                            identity_attributes = gr.Textbox(label="角色特征 (Identity Attributes)", lines=3)
                            identity_batch = gr.Slider(minimum=1, maximum=4, value=4, step=1, label="生成数量 (Batch Size)")
                            identity_gen_btn = gr.Button("生成角色 (Generate Identity)")
                        with gr.Column():
                            identity_gallery = gr.Gallery(label="角色定妆照 (Identity Gallery)", columns=2)
                    with gr.Row():
                        identity_sel_num = gr.Number(label="选中图片编号 (1-4)", minimum=1, maximum=4, step=1)
                        identity_lock_btn = gr.Button("锁定该资产 (Lock Asset)", variant="primary")
                        identity_lock_status = gr.Textbox(label="锁定状态预览 (Locked Asset Status)")
                    identity_gen_btn.click(fn=generate_identity, inputs=[identity_attributes, identity_batch], outputs=identity_gallery)
                    identity_lock_btn.click(fn=lock_identity, inputs=identity_sel_num, outputs=identity_lock_status)

                with gr.Tab("SceneDNA (场景库)"):
                    with gr.Row():
                        with gr.Column():
                            scene_attributes = gr.Textbox(label="环境描写 (Scene Attributes)", lines=3)
                            scene_batch = gr.Slider(minimum=1, maximum=4, value=4, step=1, label="生成数量 (Batch Size)")
                            scene_gen_btn = gr.Button("生成场景 (Generate Scene)")
                        with gr.Column():
                            scene_gallery = gr.Gallery(label="场景基准图 (Scene Gallery)", columns=2)
                    with gr.Row():
                        scene_sel_num = gr.Number(label="选中图片编号 (1-4)", minimum=1, maximum=4, step=1)
                        scene_lock_btn = gr.Button("锁定该资产 (Lock Asset)", variant="primary")
                        scene_lock_status = gr.Textbox(label="锁定状态预览 (Locked Asset Status)")
                    scene_gen_btn.click(fn=generate_scene, inputs=[scene_attributes, scene_batch], outputs=scene_gallery)
                    scene_lock_btn.click(fn=lock_scene, inputs=scene_sel_num, outputs=scene_lock_status)

                with gr.Tab("PropDNA (道具库)"):
                    with gr.Row():
                        with gr.Column():
                            prop_attributes = gr.Textbox(label="道具描写 (Prop Attributes)", lines=3)
                            prop_batch = gr.Slider(minimum=1, maximum=4, value=4, step=1, label="生成数量 (Batch Size)")
                            prop_gen_btn = gr.Button("生成道具 (Generate Prop)")
                        with gr.Column():
                            prop_gallery = gr.Gallery(label="道具图 (Prop Gallery)", columns=2)
                    with gr.Row():
                        prop_sel_num = gr.Number(label="选中图片编号 (1-4)", minimum=1, maximum=4, step=1)
                        prop_lock_btn = gr.Button("锁定该资产 (Lock Asset)", variant="primary")
                        prop_lock_status = gr.Textbox(label="锁定状态预览 (Locked Asset Status)")
                    prop_gen_btn.click(fn=generate_prop, inputs=[prop_attributes, prop_batch], outputs=prop_gallery)
                    prop_lock_btn.click(fn=lock_prop, inputs=prop_sel_num, outputs=prop_lock_status)

        # Tab 3: 🎬 动态表演导播台 (Performance Engine)
        with gr.Tab("🎬 动态表演导播台 (Performance Engine)"):
            with gr.Row():
                with gr.Column():
                    micro_expr = gr.Textbox(label="微表情 (Micro-expression)", lines=2)
                    macro_action = gr.Textbox(label="肢体动作 (Macro-action)", lines=2)
                    render_btn = gr.Button("🎬 渲染动作视频 (Render Performance)", variant="primary")
                with gr.Column():
                    perf_video = gr.Video(label="表演监视器 (Performance Monitor)")

            render_btn.click(fn=render_performance, inputs=[micro_expr, macro_action], outputs=perf_video)

        # Tab 4: 🎙️ 声音车间 (VocalDNA Studio)
        with gr.Tab("🎙️ 声音车间 (VocalDNA Studio)"):
            with gr.Row():
                with gr.Column():
                    dialogue_text = gr.Textbox(label="台词 (Dialogue)", lines=3)
                    emotion_dropdown = gr.Dropdown(choices=["平静 (Calm)", "愤怒 (Angry)", "悲伤 (Sad)", "喜悦 (Happy)"], label="情绪标签 (Emotion)")
                    synth_btn = gr.Button("合成语音 (Synthesize Voice)")
                with gr.Column():
                    vocal_audio = gr.Audio(label="试听音频 (Audio Preview)")

            synth_btn.click(fn=synthesize_voice, inputs=[dialogue_text, emotion_dropdown], outputs=vocal_audio)

        # Tab 5: 🎞️ 终极剪辑室 (Infinity Assembly)
        with gr.Tab("🎞️ 终极剪辑室 (Infinity Assembly)"):
            with gr.Row():
                assemble_btn = gr.Button("一键合成最终成片 (One-Click Assemble Final Video)")
            with gr.Row():
                final_video = gr.Video(label="最终成片 (Final Assembled Video)")

            assemble_btn.click(fn=assemble_final, inputs=[], outputs=final_video)

if __name__ == "__main__":
    demo.launch()
