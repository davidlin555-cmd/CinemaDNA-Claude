"""UI Header Module: Top-level layout and parameter inputs for the Dashboard."""

import gradio as gr

def render_scriptbrain_header():
    """Renders the top 'Script Creation' tabs and settings for the ScriptBrain."""
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

    return script_outline, style_dropdown, aspect_dropdown, episode_dropdown
