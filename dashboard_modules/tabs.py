"""Individual UI Tab components for the Dashboard."""

import gradio as gr
from .callbacks import (
    parse_script,
    generate_character,
    generate_scene,
    lock_assets,
    render_shot,
    synthesize_audio,
    assemble_final
)
from .gatekeeper_footer import create_gatekeeper_ui

def render_scriptbrain_tab():
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

        # Isolate footer logic
        gate_feedback, _, _ = create_gatekeeper_ui()

        parse_btn.click(
            fn=parse_script,
            inputs=[script_outline, style_dropdown, aspect_dropdown, episode_dropdown],
            outputs=[script_rich_output, gate_feedback]
        )

def render_dna_forge_tab():
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

def render_director_studio_tab():
    with gr.Tab("🎬 片场导播台 (Director Studio)"):
        with gr.Row():
            with gr.Column():
                locked_assets_gallery = gr.Gallery(label="锁定资产参考 (Locked Asset References)")
            with gr.Column():
                shot_prompt = gr.Textbox(label="当前分镜提示词 (Current Shot Prompt)", lines=4)
                transition_checkbox = gr.Checkbox(label="启用首尾帧过渡 (Enable Start/End Frame Transition)")
                render_btn = gr.Button("渲染当前镜头 (Render Current Shot)", variant="primary")
            with gr.Column():
                shot_video = gr.Video(label="当前分镜片段 (Current Shot Video)")

        # Isolate footer logic
        gate_feedback, _, _ = create_gatekeeper_ui()

        render_btn.click(fn=render_shot, inputs=[shot_prompt, transition_checkbox], outputs=[shot_video, gate_feedback])

def render_vocal_workshop_tab():
    with gr.Tab("🎙️ 声音车间 (Vocal Workshop)"):
        with gr.Row():
            dialogue_input = gr.Textbox(label="台词 (Dialogue)", placeholder="输入要合成的台词...")
            synth_btn = gr.Button("合成音频 (Synthesize Audio)")
        with gr.Row():
            audio_out = gr.Audio(label="合成结果 (Synthesized Audio)")

        # Isolate footer logic
        gate_feedback, _, _ = create_gatekeeper_ui()

        synth_btn.click(fn=synthesize_audio, inputs=[dialogue_input], outputs=[audio_out, gate_feedback])

def render_final_assembly_tab():
    with gr.Tab("🎞️ 终极剪辑室 (Final Assembly)"):
        with gr.Row():
            assemble_btn = gr.Button("一键合成最终成片 (One-Click Assemble Final Video)")
        with gr.Row():
            final_video = gr.Video(label="最终成片 (Final Assembled Video)")

        assemble_btn.click(fn=assemble_final, inputs=[], outputs=final_video)
