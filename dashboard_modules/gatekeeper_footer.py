"""Reusable UI components for the dashboard, including the Agent-in-the-loop Gatekeeper."""

import gradio as gr
from typing import Tuple
from .callbacks import pass_gate, reject_gate

def create_gatekeeper_ui() -> Tuple[gr.Textbox, gr.Button, gr.Button]:
    """
    Creates and returns the Gatekeeper Agent feedback and approval components.
    Physically isolating this prevents accidental deletion when updating parent containers.
    """
    with gr.Row():
        gate_feedback = gr.Textbox(label="🤖 Agent 质检报告 (Gatekeeper Feedback)", interactive=False)
    with gr.Row():
        approve_btn = gr.Button("✅ 审核通过并锁定 (Approve & Lock)")
        reject_btn = gr.Button("🔄 附带意见打回重做 (Reject & Regenerate)")

    # Bind the internal UI dummy events for approve/reject logic
    approve_btn.click(fn=pass_gate, outputs=gate_feedback)
    reject_btn.click(fn=reject_gate, outputs=gate_feedback)

    return gate_feedback, approve_btn, reject_btn
