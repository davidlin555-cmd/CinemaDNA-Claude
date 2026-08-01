"""
ScriptBrainMicroDismantler Agent Class Stub
"""
import json
import urllib.request
import urllib.error
import os

class ScriptBrainMicroDismantler:
    """
    ScriptBrain: 剧本微观拆解
    Responsible for breaking down scripts into manageable, actionable micro-components.
    """
    def __init__(self):
        self.engine_url = "http://127.0.0.1:8000/api/v1/dismantle"
        self.llm_api_key = os.environ.get("LLM_API_KEY", "")
        self.llm_api_base = os.environ.get("LLM_API_BASE", "https://api.openai.com/v1/chat/completions")
        self.system_prompt = (
            "你是一个专业的短剧分镜师。请根据用户输入的一句话剧本大纲，输出符合 DramaOS 规范的 JSON 数组。"
            "每个元素必须包含：shot_id, scene_prompt, action, character_details。"
            "只输出纯 JSON，不要有任何其他解释文字。"
        )

    def dismantle(self, story_prompt):
        """
        Dismantles the story prompt into a JSON array of shots.
        Calls a real LLM if API Key is configured, otherwise falls back to local engine bridge.
        """
        if self.llm_api_key:
            # 真实大模型调用逻辑 (OpenAI 兼容接口)
            payload = {
                "model": "gpt-4o", # 可以是任意支持该接口的模型，如通义千问, DeepSeek等
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": story_prompt}
                ],
                "temperature": 0.7
            }
            data = json.dumps(payload).encode('utf-8')
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.llm_api_key}'
            }
            req = urllib.request.Request(self.llm_api_base, data=data, headers=headers)

            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    # 尝试解析返回的 JSON 字符串
                    content = result.get('choices', [{}])[0].get('message', {}).get('content', '[]')
                    # 清理 markdown code blocks 如果有的话
                    content = content.strip().removeprefix('```json').removesuffix('```').strip()
                    try:
                        parsed_json = json.loads(content)
                        return {"status": "success", "message": f"LLM 拆解成功", "data": parsed_json}
                    except json.JSONDecodeError:
                        return {"status": "success", "message": f"LLM 拆解成功但 JSON 解析失败: {content}", "data": []}
            except urllib.error.URLError as e:
                return {"status": "error", "message": f"LLM 接口调用失败: {str(e)}", "data": []}
            except Exception as e:
                return {"status": "error", "message": f"未知大模型调用错误: {str(e)}", "data": []}
        else:
            # Fallback 逻辑：如果没有配置 Key，走之前的 Mock/Engine 逻辑
            payload = {"script_data": story_prompt}
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(self.engine_url, data=data, headers={'Content-Type': 'application/json'})

            try:
                with urllib.request.urlopen(req, timeout=5) as response:
                    result = json.loads(response.read().decode('utf-8'))
                    return {"status": "success", "message": f"ScriptBrain API调用成功: {result}", "data": []}
            except urllib.error.URLError as e:
                return {"status": "error", "message": f"ScriptBrain 连接真实引擎失败: {str(e)}", "data": []}
            except Exception as e:
                return {"status": "error", "message": f"未知错误: {str(e)}", "data": []}
