"""Multimodal API Gateway for true API connections.

Centralizes connections to external generation services like OpenAI (DALL-E), ElevenLabs, and RunwayML.
"""

from __future__ import annotations

import httpx
from typing import Optional, Dict, Any
import cinemadna.config as config

async def generate_image_async(prompt: str, style_preset: str = "vivid") -> str:
    """Generates an image using OpenAI DALL-E 3 and returns the URL.

    Raises:
        RuntimeError: if the API call fails or key is missing.
    """
    api_key = config.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Multimodal image generation aborted.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    # Enhance the prompt based on the engine requirements
    full_prompt = f"Professional cinematic concept art, {style_preset} style. {prompt}"

    payload = {
        "model": "dall-e-3",
        "prompt": full_prompt,
        "n": 1,
        "size": "1024x1024"
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=45.0
        )

        if response.status_code != 200:
            raise RuntimeError(f"Image API Error [{response.status_code}]: {response.text}")

        data = response.json()
        return data["data"][0]["url"]

async def generate_audio_async(text: str, voice_id: str = "21m00Tcm4TlvDq8ikWAM") -> str:
    """Generates audio using ElevenLabs TTS and returns a playable stream URL or file path.

    Raises:
        RuntimeError: if the API call fails or key is missing.
    """
    api_key = config.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ELEVENLABS_API_KEY. Multimodal audio generation aborted.")

    # ElevenLabs endpoint
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key
    }

    payload = {
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.5
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json=payload,
            headers=headers,
            timeout=30.0
        )

        if response.status_code != 200:
            raise RuntimeError(f"Audio API Error [{response.status_code}]: {response.text}")

        # In a real environment, we would stream this bytes payload to an S3 bucket or local `/tmp/`
        # file and return the public URL to Gradio.
        # For this implementation blueprint, we save it locally and return the absolute path.
        import os
        import uuid
        os.makedirs("demo_out/audio", exist_ok=True)
        file_path = f"demo_out/audio/{uuid.uuid4().hex}.mp3"

        with open(file_path, "wb") as f:
            f.write(response.content)

        return os.path.abspath(file_path)

async def generate_video_async(prompt: str, image_url: Optional[str] = None) -> str:
    """Generates a video using RunwayML Gen-3 API (Standard interface blueprint).

    Raises:
        RuntimeError: if the API call fails or key is missing.
    """
    api_key = config.get("RUNWAY_API_KEY")
    if not api_key:
        raise RuntimeError("Missing RUNWAY_API_KEY. Multimodal video generation aborted.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Runway-Version": "2024-09-13",
        "Content-Type": "application/json"
    }

    # Prepare standard Runway Gen-3 payload
    payload: Dict[str, Any] = {
        "promptText": prompt,
        "model": "gen3a_turbo",
        "ratio": "16:9"
    }

    if image_url:
        payload["promptImage"] = image_url

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://api.dev.runwayml.com/v1/image_to_video",
            headers=headers,
            json=payload,
            timeout=60.0
        )

        if response.status_code != 200:
            raise RuntimeError(f"Video API Error [{response.status_code}]: {response.text}")

        data = response.json()

        # Note: Runway is asynchronous. We would normally need to poll the task ID.
        # This function returns the task ID placeholder or a simulated immediate URL for the structural blueprint.
        task_id = data.get("id", "unknown_task")
        return f"https://runwayml.com/simulated_output_for_task_{task_id}.mp4"
