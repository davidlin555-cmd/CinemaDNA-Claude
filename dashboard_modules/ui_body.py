"""UI Body Module: Core dynamic generation and output visualization components."""

import gradio as gr

def render_script_body():
    """Renders the output container for the ScriptBrain."""
    with gr.Row():
        parse_btn = gr.Button("🎬 解析与拆解剧本 (Parse and Dismantle Script)", variant="primary")

    # State variable to hold the structured parsed output data
    script_data_state = gr.State([])

    @gr.render(inputs=script_data_state)
    def render_accordion(episodes_data):
        """Native Accordion rendering utilizing Gradio 6 @gr.render"""
        if not episodes_data:
            gr.Markdown("📝 *等待拆解剧本...*")
            return

        for ep_index, ep_shots in enumerate(episodes_data):
            with gr.Accordion(label=f"📺 第 {ep_index + 1} 集", open=(ep_index == 0)):
                for idx, shot in enumerate(ep_shots):
                    markdown_content = f"""
### 🎬 【场景 {idx+1}】 {shot.get('scene', '未知场景')}
**🎥 【镜头 {shot.get('shot_id', '')}】**

*   **🏃‍♂️ 【动作描写】** {shot.get('action', '')} ({shot.get('character', '')})
*   **📝 【提示词】** *{shot.get('prompt', '')}*
                    """
                    gr.Markdown(markdown_content)

    return parse_btn, script_data_state

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
