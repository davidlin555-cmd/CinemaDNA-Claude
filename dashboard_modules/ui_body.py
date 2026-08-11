"""UI Body Module: Core dynamic generation and output visualization components."""

import gradio as gr

def render_script_body():
    """Renders the output container for the ScriptBrain.
    Now optimized to live entirely in the Right Canvas (scale=3) of a Liblib-style topology.
    """
    # State variable to hold the structured parsed output data
    script_data_state = gr.State([])

    with gr.Group():
        @gr.render(inputs=script_data_state)
        def render_accordion(episodes_data):
            """Native Accordion rendering utilizing Gradio 6 @gr.render"""
            if not episodes_data:
                gr.Markdown("## 📝 等待剧本拆解...\n在左侧调整参数并点击生成。")
                return

            for ep_index, ep_shots in enumerate(episodes_data):
                with gr.Accordion(label=f"📺 第 {ep_index + 1} 集 分镜表", open=(ep_index == 0)):
                    for idx, shot in enumerate(ep_shots):
                        subsystem = shot.get("subsystem_tasks", {})
                        identity = subsystem.get("1_identity_dna", {})
                        scene = subsystem.get("2_scene_dna", {})
                        performance = subsystem.get("3_performance_dna", {})
                        vocal = subsystem.get("4_vocal_dna", {})
                        audio = subsystem.get("5_audio_dna", {})

                        markdown_content = f"""
### 🎬 【镜头 {shot.get('shot_id', idx+1)}】

**【场景环境】**
{shot.get('scene_setting', '未知')}

**【画面与动作描写】**
{shot.get('narrative_action', '未知')}

**👤 Identity DNA**
*   **角色名:** {identity.get('character_name', '未指定')}
*   **外观服装:** {identity.get('appearance_and_clothing', '未指定')}
*   **面部微表情:** {identity.get('facial_micro_expression', '未指定')}

**🖼️ Scene DNA**
*   **地点细节:** {scene.get('location_details', '未指定')}
*   **电影灯光:** {scene.get('cinematic_lighting', '未指定')}

**🎬 Performance DNA**
*   **摄影机运动:** {performance.get('camera_work', '未指定')}
*   **主体物理动作:** {performance.get('subject_physics', '未指定')}

**🎙️ Vocal DNA**
*   **台词文本:** {vocal.get('dialogue_text', 'None')}
*   **情感标签:** {vocal.get('vocal_emotion_tags', '未指定')}

**🎵 Audio DNA**
*   **拟音音效:** {audio.get('foley_sfx', '未指定')}
*   **背景音乐氛围:** {audio.get('bgm_mood', '未指定')}

**📝 【Midjourney 提示词】**
*{shot.get('midjourney_prompt', '未指定')}*
                        """
                        gr.Markdown(markdown_content)

    return script_data_state

def render_dna_forge_body():
    """Renders the DNA forge core interaction area."""
    with gr.Tabs():
        with gr.Tab("角色库 (Character Library)"):
            with gr.Row():
                with gr.Column():
                    char_attributes = gr.Textbox(label="角色属性 (Character Attributes)", lines=3)
                    char_gen_btn = gr.Button("生成角色 (Generate Character)")
                with gr.Column():
                    char_gallery = gr.Gallery(label="角色定妆照/三视图 (Character Reference Sheets)")

            return "character", char_attributes, char_gen_btn, char_gallery

        with gr.Tab("场景库 (Scene Library)"):
            with gr.Row():
                with gr.Column():
                    scene_attributes = gr.Textbox(label="场景属性 (Scene Attributes)", lines=3)
                    scene_gen_btn = gr.Button("生成场景 (Generate Scene)")
                with gr.Column():
                    scene_gallery = gr.Gallery(label="场景基准图 (Scene Reference Images)")

            return "scene", scene_attributes, scene_gen_btn, scene_gallery

def render_director_studio_body():
    """Renders the Director Studio core layout."""
    with gr.Row():
        with gr.Column():
            locked_assets_gallery = gr.Gallery(label="锁定资产参考 (Locked Asset References)")
        with gr.Column():
            shot_prompt = gr.Textbox(label="当前分镜提示词 (Current Shot Prompt)", lines=4)
            transition_checkbox = gr.Checkbox(label="启用首尾帧过渡 (Enable Start/End Frame Transition)")
            render_btn = gr.Button("渲染当前镜头 (Render Current Shot)", variant="primary")
        with gr.Column():
            shot_video = gr.Video(label="当前分镜片段 (Current Shot Video)")

    return shot_prompt, transition_checkbox, render_btn, shot_video

def render_vocal_workshop_body():
    """Renders the Vocal Workshop input and output."""
    with gr.Row():
        dialogue_input = gr.Textbox(label="台词 (Dialogue)", placeholder="输入要合成的台词...")
        synth_btn = gr.Button("合成音频 (Synthesize Audio)")
    with gr.Row():
        audio_out = gr.Audio(label="合成结果 (Synthesized Audio)")

    return dialogue_input, synth_btn, audio_out
