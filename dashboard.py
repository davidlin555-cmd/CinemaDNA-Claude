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
def parse_script(outline, style, progress=gr.Progress()):
    progress(0.5, desc="Parsing script...")
    # Simulated structure returned by ScriptBrain
    df = pd.DataFrame({
        "镜号": ["1", "2"],
        "场景": ["街头", "室内"],
        "动作": ["走动", "坐下"],
        "角色": ["主角", "配角"],
        "提示词": ["A person walking on the street", "Someone sitting in a room"]
    })
    progress(1.0, desc="Done")
    return df

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
        # Simulate video rendering API call
        # result = _post_to_engine("/api/v1/render", {"prompt": prompt, "transition": transition})
        # video_url = result.get("data", {}).get("video_url")
        import time
        time.sleep(2)
        # Using a public sample video URL for demonstration purposes
        video_url = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
        progress(1.0, desc="Render Complete")
        return video_url # gr.Video expects a string URL or local path
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to render shot: {e}")

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
            with gr.Row():
                script_outline = gr.Textbox(label="剧本大纲 (Script Outline)", placeholder="输入剧本大纲...", lines=5)
            with gr.Row():
                style_dropdown = gr.Dropdown(choices=["写实 (Realistic)", "3D", "动漫 (Anime)"], label="画风 (Style)")
            with gr.Row():
                parse_btn = gr.Button("解析与拆解剧本 (Parse and Dismantle Script)")
            with gr.Row():
                shot_list_df = gr.Dataframe(
                    headers=["镜号", "场景", "动作", "角色", "提示词"],
                    label="分镜单 (Shot List)",
                    interactive=True,
                    row_count=5
                )

            parse_btn.click(fn=parse_script, inputs=[script_outline, style_dropdown], outputs=shot_list_df)

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

            render_btn.click(fn=render_shot, inputs=[shot_prompt, transition_checkbox], outputs=shot_video)

        # Tab 4: 🎞️ 终极剪辑室 (Final Assembly)
        with gr.Tab("🎞️ 终极剪辑室 (Final Assembly)"):
            with gr.Row():
                assemble_btn = gr.Button("一键合成最终成片 (One-Click Assemble Final Video)")
            with gr.Row():
                final_video = gr.Video(label="最终成片 (Final Assembled Video)")

            assemble_btn.click(fn=assemble_final, inputs=[], outputs=final_video)

if __name__ == "__main__":
    demo.launch()
