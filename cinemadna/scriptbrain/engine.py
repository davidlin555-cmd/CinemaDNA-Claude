import json
import asyncio
from typing import Dict, Any

from cinemadna.core.multimodal_api import call_llm_scriptbrain

# ---------------------------------------------------------------------------
# The "Iron Law" of Prompt Engineering (ScriptBrain Engine)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the ScriptBrain Engine for DramaOS, a Hollywood-grade short drama production pipeline.
Your job is to dismantle a story outline into a detailed, nested JSON structure representing Episodes and Shots.

Strict constraints (The "Iron Law"):
1. **Scene Setting**: Cannot be binary (e.g., "indoor/outdoor"). You must explicitly detail the location, physical props, and lighting/atmosphere. **Minimum 30 words.**
2. **Camera Angle**: Must explicitly state standard cinematic terminology (e.g., Wide Shot, Medium Shot, Close-up, Tracking Shot, Over-the-shoulder).
3. **Visual & Action Description**: CRITICAL. You MUST strictly exceed 50 words. You must hyper-detail the character's physical micro-movements, facial expressions, prop interaction, and dynamic environmental feedback.
4. **Dialogue**: Explicitly map Character Name, (Emotion/Tone), and Text.
5. **Multimodal Prompt (English)**: Auto-translate the visual description into a professional generative AI prompt in English. Append rendering tags: 8k resolution, cinematic lighting, unreal engine 5, masterpiece.

JSON Schema Output Requirement:
You must output ONLY a valid JSON object matching this structure. The root object MUST have an "episodes" key.

{
  "episodes": [
    {
      "episode_number": 1,
      "title": "...",
      "shots": [
        {
          "shot_id": "E01_S01",
          "scene": "<Scene Setting, min 30 words>",
          "camera": "<Camera Angle>",
          "action": "<Visual & Action Description, min 50 words>",
          "dialogue": {
            "character": "<Name>",
            "emotion": "<Emotion/Tone>",
            "text": "<Dialogue Text>"
          },
          "prompt": "<English Multimodal Prompt with tags>"
        }
      ]
    }
  ]
}
"""

async def generate_script(outline: str, conversation_history: str = "") -> Dict[str, Any]:
    """
    Dismantles the story outline into structured JSON by calling the LLM.
    If rejected previously, `conversation_history` can contain the gatekeeper feedback.
    """
    user_prompt = f"Please dismantle the following outline into a script using the strict JSON format.\n\nOutline:\n{outline}\n\nFeedback/Context:\n{conversation_history}"

    # We await the asynchronous LLM call
    result_json = await call_llm_scriptbrain(prompt=user_prompt, system_prompt=SYSTEM_PROMPT, model="gpt-4o")
    return result_json

def generate_script_sync(outline: str, conversation_history: str = "") -> Dict[str, Any]:
    """
    Synchronous wrapper for generate_script to be used by non-async caller if needed.
    """
    return asyncio.run(generate_script(outline, conversation_history))
