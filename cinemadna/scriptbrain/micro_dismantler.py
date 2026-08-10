"""
ScriptBrainMicroDismantler Agent
"""

import json
import openai
from cinemadna import config

class ScriptBrainMicroDismantler:
    """
    ScriptBrain: 剧本微观拆解
    Responsible for breaking down scripts into manageable, actionable micro-components.
    """
    def __init__(self):
        # Read API key from the centralized config registry. This will throw if missing.
        self.api_key = config.require("OPENAI_API_KEY")
        self.client = openai.OpenAI(api_key=self.api_key)

    def dismantle(self, script_data):
        """
        Dismantles the script data by sending it to an LLM.
        """
        outline = script_data.get('outline', '')
        style = script_data.get('style', '')
        ratio = script_data.get('ratio', '')

        system_prompt = """
You are the ScriptBrain Micro-Dismantler for DramaOS.
Your job is to read the provided script outline, style, and aspect ratio, and break it down into a highly detailed shot-by-shot sequence.
You MUST output a valid JSON array of objects. Do not include Markdown wrappers like ```json.
Each object in the array represents one shot and MUST contain EXACTLY the following 7 keys:
1. "镜号": (string) e.g., "1", "2"
2. "Camera_Lighting": (string) e.g., "Close up, dramatic shadow"
3. "SceneDNA": (string) The scene location, e.g., "Street, daytime"
4. "IdentityDNA": (string) The character description, e.g., "Hero, male, 25"
5. "PerformanceDNA": (string) The macro/micro action, e.g., "Walking briskly"
6. "PropDNA": (string) Any props in the scene, e.g., "Briefcase"
7. "VocalDNA": (string) Dialogue for this specific shot, or empty string.

Ensure your output is strictly a JSON array parsing the story logically.
"""

        user_prompt = f"Outline: {outline}\nStyle: {style}\nRatio: {ratio}\n\nPlease dismantle this into a JSON array of shots."

        response = self.client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7
        )

        content = response.choices[0].message.content.strip()

        # Clean up any potential markdown wrapper returned by LLM
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        content = content.strip()

        return json.loads(content)
