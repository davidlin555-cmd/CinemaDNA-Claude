"""
ScriptBrainMicroDismantler Agent Class
Connects to Anthropic's Claude to dismantle script outlines into structured JSON shot lists.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Dict

import anthropic
import cinemadna.config as config

_SYSTEM_PROMPT = """
你是一位顶级的商业短剧分镜师和导演。你的任务是将用户提供的故事大纲、风格和审核意见，拆解为符合好莱坞电影工业标准的专业分镜表（Shot List）。

【绝对规则】：
1. 你必须、且只能返回一个合法的 JSON 数组结构。
2. 绝对不允许输出任何 Markdown 代码块标记（例如 ```json），不要输出任何解释性前言或后记。
3. 你的输出必须完全匹配以下 JSON 格式。DramaOS 运行在严格定义的 5 大子模型架构上，ScriptBrain 必须为所有 5 个下游 DNA 引擎生成极其具体、独立的技术任务单。

【五大车间分镜任务单铁律 (输出格式要求)】：
[
  {
    "shot_id": 1,
    "scene_setting": "【场景环境 (Scene Setting)】：必须包含极具画面感的具体地点、物理细节与光影氛围（不得低于30字）。",
    "narrative_action": "【画面与动作描写 (Visual & Action Description)】：绝对铁律！绝对不能低于 50 个字。必须极其详细地描述物理动作、脸部微表情、互动道具和环境动态反馈。禁止出现类似『走动』这种干瘪词汇。",
    "subsystem_tasks": {
      "1_identity_dna": {
        "character_name": "画面聚焦的主体角色",
        "appearance_and_clothing": "具体的服装、发型和造型",
        "facial_micro_expression": "例如：瞳孔放大、嘴唇颤抖、冷笑"
      },
      "2_scene_dna": {
        "location_details": "建筑、道具、背景元素",
        "cinematic_lighting": "例如：体积光、赛博朋克霓虹灯、硬阴影"
      },
      "3_performance_dna": {
        "camera_work": "例如：极特写、推镜头、手持摇晃",
        "subject_physics": "例如：角色猛然转头、雨水溅在肩膀上"
      },
      "4_vocal_dna": {
        "dialogue_text": "确切的台词文本或 'None'",
        "vocal_emotion_tags": "例如：气喘吁吁、愤怒的耳语、哭泣"
      },
      "5_audio_dna": {
        "foley_sfx": "例如：湿砾石上的脚步声、远处的雷声",
        "bgm_mood": "例如：紧张的弦乐渐强、忧郁的钢琴"
      }
    },
    "midjourney_prompt": "结合 Identity + Scene + Lighting + 8k, masterpiece 的英文翻译"
  }
]

【拆解原则】：
1. 剧情密度极高，每个镜头（2-4秒）都要有视觉信息增量。
2. 将输入的大纲至少拆解出 4 到 8 个极其连贯的镜头。
3. 必须贴合用户要求的【画风风格】去撰写英文 prompt。
"""

class ScriptBrainMicroDismantler:
    """
    ScriptBrain: 剧本微观拆解
    Responsible for breaking down scripts into manageable, actionable micro-components via real LLM.
    """
    def __init__(self, model: str = "claude-3-haiku-20240307"):
        # We use Haiku for fast, structural generation, but allow overrides
        self.model = model
        self.client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))

    def dismantle(self, outline: str, style: str, feedback: str = "") -> List[Dict[str, Any]]:
        """
        Dismantles the script data using the Anthropic API.
        Includes a 3-pass retry loop for JSON structural integrity.
        """
        user_prompt = f"【剧本大纲】：\n{outline}\n\n【指定画风】：\n{style}\n"
        if feedback:
            user_prompt += f"\n【Gatekeeper 审核打回意见（请重点修正）】：\n{feedback}\n"

        user_prompt += "\n请立刻开始拆解，只输出 JSON 数组。"

        last_error = None
        for attempt in range(3):
            # If we are retrying, we inject the specific JSON error back to the model
            current_prompt = user_prompt
            if attempt > 0 and last_error:
                current_prompt += f"\n\n注意！你上一轮的输出导致了解析失败。错误信息为: {last_error}\n请务必检查并返回纯净、合法的 JSON 数组。"

            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=_SYSTEM_PROMPT,
                    messages=[
                        {"role": "user", "content": current_prompt}
                    ]
                )

                raw_text = "".join(block.text for block in response.content if hasattr(block, "text"))

                # Robust Extraction: Strip out any conversational padding using Regex
                # We look for the first '[' and the last ']'
                match = re.search(r"\[.*\]", raw_text, re.DOTALL)
                if not match:
                    raise ValueError("Failed to locate a JSON array in the LLM response.")

                json_str = match.group(0)
                shots = json.loads(json_str)

                # Structural validation
                if not isinstance(shots, list):
                    raise ValueError("The parsed JSON is not a list.")
                for idx, shot in enumerate(shots):
                    required_keys = ("shot_id", "scene_setting", "narrative_action", "subsystem_tasks", "midjourney_prompt")
                    if not all(k in shot for k in required_keys):
                        raise ValueError(f"Shot at index {idx} is missing required schema keys: {required_keys}")

                    subsystem_keys = ("1_identity_dna", "2_scene_dna", "3_performance_dna", "4_vocal_dna", "5_audio_dna")
                    if not isinstance(shot.get("subsystem_tasks"), dict) or not all(k in shot["subsystem_tasks"] for k in subsystem_keys):
                        raise ValueError(f"Shot at index {idx} subsystem_tasks is missing required keys: {subsystem_keys}")

                return shots

            except (json.JSONDecodeError, ValueError) as e:
                last_error = str(e)
                print(f"[ScriptBrain Dismantler] Attempt {attempt + 1} JSON/Schema failed: {last_error}")
                continue # Try again
            except anthropic.APIError as e:
                # Network or API level failures from Anthropic SDK
                last_error = f"API Error: {str(e)}"
                print(f"[ScriptBrain Dismantler] Attempt {attempt + 1} API failed: {last_error}")
                continue
            except Exception as e:
                last_error = f"Unexpected Error: {str(e)}"
                print(f"[ScriptBrain Dismantler] Attempt {attempt + 1} Unexpected failed: {last_error}")
                continue

        # If we exhausted 3 attempts
        raise RuntimeError(f"ScriptBrain 拆解彻底失败，LLM 连续3次未能成功生成。最终错误: {last_error}")
