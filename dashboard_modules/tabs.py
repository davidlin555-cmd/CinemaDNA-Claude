"""Individual UI Tab components for the Dashboard constructed via Header, Body, Footer modules."""

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
from .ui_header import render_scriptbrain_header
from .ui_body import (
    render_script_body,
    render_dna_forge_body,
    render_director_studio_body,
    render_vocal_workshop_body
)
from .ui_footer import create_gatekeeper_ui

def render_scriptbrain_tab():
    with gr.Tab("📝 剧本中枢 (ScriptBrain)"):
        with gr.Row():
            # Left Panel: Controls (scale=1)
            with gr.Column(scale=1):
                script_outline, style_dropdown, aspect_dropdown, episode_dropdown = render_scriptbrain_header()

                # Massive 'Generate' button mapped at the very bottom of the Left Control panel
                parse_btn = gr.Button("🎬 解析与拆解剧本\n(Parse and Dismantle Script)", variant="primary", size="lg")

            # Right Panel: Output Canvas (scale=3)
            with gr.Column(scale=3):
                # The @gr.render logic for the gr.Accordion is exclusively rendered here
                script_rich_output = render_script_body()

        # Floating Footer: Gatekeeper Textbox and Approve/Reject buttons below the main Row.
        gate_feedback, approve_btn, reject_btn = create_gatekeeper_ui()

        # Wire the Left-Panel Generate button to populate the Right-Panel Canvas
        parse_btn.click(
            fn=parse_script,
            inputs=[script_outline, style_dropdown, aspect_dropdown, episode_dropdown],
            outputs=[script_rich_output, gate_feedback]
        )

        # The reject button actively triggers a regeneration passing the feedback as context
        reject_btn.click(
            fn=parse_script,
            inputs=[script_outline, style_dropdown, aspect_dropdown, episode_dropdown, gate_feedback],
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

                char_gen_btn.click(fn=generate_character, inputs=[char_attributes], outputs=[char_gallery])

            with gr.Tab("场景库 (Scene Library)"):
                with gr.Row():
                    with gr.Column():
                        scene_attributes = gr.Textbox(label="场景属性 (Scene Attributes)", lines=3)
                        scene_gen_btn = gr.Button("生成场景 (Generate Scene)")
                    with gr.Column():
                        scene_gallery = gr.Gallery(label="场景基准图 (Scene Reference Images)")

                scene_gen_btn.click(fn=generate_scene, inputs=[scene_attributes], outputs=[scene_gallery])

        with gr.Row():
            lock_assets_btn = gr.Button("锁定全部资产并进入拍摄 (Lock All Assets and Proceed to Shooting)")

        lock_assets_btn.click(fn=lock_assets, inputs=[], outputs=[])

def render_director_studio_tab():
    with gr.Tab("🎬 片场导播台 (Director Studio)"):
        # Body
        shot_prompt, transition_checkbox, render_btn, shot_video = render_director_studio_body()

        # Footer
        gate_feedback, approve_btn, reject_btn = create_gatekeeper_ui()

        # Wire
        render_btn.click(fn=render_shot, inputs=[shot_prompt, transition_checkbox], outputs=[shot_video, gate_feedback])

        # Regeneration via reject
        reject_btn.click(fn=render_shot, inputs=[shot_prompt, transition_checkbox, gate_feedback], outputs=[shot_video, gate_feedback])

def render_vocal_workshop_tab():
    with gr.Tab("🎙️ 声音车间 (Vocal Workshop)"):
        # Body
        dialogue_input, synth_btn, audio_out = render_vocal_workshop_body()

        # Footer
        gate_feedback, approve_btn, reject_btn = create_gatekeeper_ui()

        # Wire
        synth_btn.click(fn=synthesize_audio, inputs=[dialogue_input], outputs=[audio_out, gate_feedback])

        # Regeneration via reject
        reject_btn.click(fn=synthesize_audio, inputs=[dialogue_input, gate_feedback], outputs=[audio_out, gate_feedback])

def render_final_assembly_tab():
    with gr.Tab("🎞️ 终极剪辑室 (Final Assembly)"):
        with gr.Row():
            assemble_btn = gr.Button("一键合成最终成片 (One-Click Assemble Final Video)")
        with gr.Row():
            final_video = gr.Video(label="最终成片 (Final Assembled Video)")

        assemble_btn.click(fn=assemble_final, inputs=[], outputs=final_video)
