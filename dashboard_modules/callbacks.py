"""Callback functions and API utilities for the Dashboard."""

import gradio as gr
import pandas as pd
import requests
import traceback
import time

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

def parse_script(outline, style, aspect_ratio, episodes, progress=gr.Progress()):
    progress(0.2, desc="Parsing script...")
    try:
        result = _post_to_engine("/api/v1/script/parse", {"outline": outline, "style": style})
        shots = result.get("data", {}).get("shots", [])

        ep_count = int(episodes.replace("集", "")) if episodes else 1
        html_content = "<div style='font-family: sans-serif;'>"

        for ep in range(1, ep_count + 1):
            html_content += f'''
            <details style="margin-bottom: 10px; border: 1px solid #ddd; border-radius: 5px; padding: 5px;">
                <summary style="font-weight: bold; cursor: pointer; padding: 5px; background-color: #f9f9f9;">
                    📺 第 {ep} 集：{style} 风格呈现 ({aspect_ratio})
                </summary>
                <div style="padding: 15px; margin-top: 10px;">
            '''
            for idx, shot in enumerate(shots):
                html_content += f'''
                    <div style="margin-bottom: 20px; border-bottom: 1px dashed #eee; padding-bottom: 10px;">
                        <h4 style="color: #2c3e50; margin: 0 0 5px 0;">🎬 【场景 {idx+1}】 {shot.get('scene', '未知场景')}</h4>
                        <p style="margin: 0 0 5px 0;"><strong>🎥 【镜头 {shot.get('shot_id', '')}】</strong></p>
                        <p style="margin: 0 0 5px 0; color: #555;"><strong>🏃‍♂️ 【动作描写】</strong> {shot.get('action', '')} ({shot.get('character', '')})</p>
                        <p style="margin: 0 0 5px 0; font-style: italic; color: #34495e;"><strong>📝 【提示词】</strong> {shot.get('prompt', '')}</p>
                    </div>
                '''
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
        result = _post_to_engine("/api/v1/identity", {"attributes": attributes})
        image_url = result.get("data", {}).get("image_url")
        progress(1.0, desc="Asset Received")
        if image_url:
            return [image_url]
        return []
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to generate character: {e}")

def generate_scene(attributes, progress=gr.Progress()):
    progress(0.2, desc="Requesting Scene Generation...")
    try:
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
        time.sleep(3)
        video_url = "http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4"
        progress(1.0, desc="Assembly Complete")
        return video_url
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to assemble final video: {e}")
