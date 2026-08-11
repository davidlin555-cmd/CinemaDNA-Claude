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

def parse_script(outline, style, aspect_ratio, episodes, feedback="", progress=gr.Progress()):
    progress(0.2, desc="Parsing script...")
    try:
        payload = {"outline": outline, "style": style}
        if feedback:
            payload["feedback"] = feedback

        result = _post_to_engine("/api/v1/script/parse", payload)
        shots = result.get("data", {}).get("shots", [])

        ep_count = int(episodes.replace("集", "")) if episodes else 1

        # Structure data as a list of episodes, where each episode is a list of shots
        episodes_data = []
        for ep in range(1, ep_count + 1):
            episodes_data.append(shots) # Reusing the same shots for mocking episodes

        gate_feedback = result.get("gate_feedback", "无反馈")
        progress(1.0, desc="Done")
        return episodes_data, gate_feedback
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
        result = _post_to_engine("/api/v1/scene", {"attributes": attributes})
        image_url = result.get("data", {}).get("image_url")
        progress(1.0, desc="Asset Received")
        if image_url:
            return [image_url]
        return []
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to generate scene: {e}")

def lock_assets():
    return None

def render_shot(prompt, transition, feedback="", progress=gr.Progress()):
    progress(0.2, desc="Rendering Shot...")
    try:
        payload = {"prompt": prompt, "transition": transition}
        if feedback:
            payload["feedback"] = feedback

        result = _post_to_engine("/api/v1/performance/generate", payload)
        video_url = result.get("data", {}).get("video_url")
        gate_feedback = result.get("gate_feedback", "无反馈")
        progress(1.0, desc="Render Complete")
        return video_url, gate_feedback
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to render shot: {e}")

def synthesize_audio(dialogue, feedback="", progress=gr.Progress()):
    progress(0.2, desc="Synthesizing Audio...")
    try:
        payload = {"dialogue": dialogue}
        if feedback:
            payload["feedback"] = feedback

        result = _post_to_engine("/api/v1/audio/synthesize", payload)
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
        result = _post_to_engine("/api/v1/assemble", {})
        video_url = result.get("data", {}).get("video_url")
        progress(1.0, desc="Assembly Complete")
        return video_url
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Failed to assemble final video: {e}")
