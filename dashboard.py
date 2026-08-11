"""DramaOS Web Dashboard Assembly Engine.

This file serves purely as an orchestrator to stitch together modularized UI components.
Never put complex UI logic or backend communication in this file.
"""

import gradio as gr
from dashboard_modules.tabs import (
    render_scriptbrain_tab,
    render_dna_forge_tab,
    render_director_studio_tab,
    render_vocal_workshop_tab,
    render_final_assembly_tab
)

with gr.Blocks(title="DramaOS - Hollywood Director's Console") as demo:
    gr.Markdown("# DramaOS 导播台 (Director's Console)")

    with gr.Tabs():
        render_scriptbrain_tab()
        render_dna_forge_tab()
        render_director_studio_tab()
        render_vocal_workshop_tab()
        render_final_assembly_tab()

if __name__ == "__main__":
    demo.launch()
