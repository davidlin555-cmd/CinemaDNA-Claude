"""LLM 多模态视觉深判 —— 把 VisionJudge 的深判插槽接上真模型（⑤·0 视频成本）。

ffmpeg 像素层只能判"静帧/纯色"，判不出**变脸/手脸崩坏/画内乱码/表演僵硬**——短剧
最致命的观感杀手。这里用 Claude 视觉对**一帧**做结构化判读，回填 VisionJudge 的
`deep`，让"评分可信"（不再 deep_judged=False 记 PENDING）。

诚实：这是判官准确度升级，**不改生成、不刷分**——判得更准可能分更低。
成本：每帧 1 张小图 + 短提示，默认 claude-haiku（视觉便宜档）。判官可注入假 client
做 0 成本测试。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"       # 视觉便宜档，QA 判官够用

_PROMPT = (
    "你是短剧成片质检员。这是一帧 AI 生成的中文短剧画面。只判**客观视觉缺陷**，"
    "不评剧情。严格输出 JSON（不要多余文字）：\n"
    '{"ai_fake": bool,            // 明显 AI 假感/塑料感/不真实\n'
    ' "hand_face_broken": bool,   // 手指/五官崩坏、畸变、多指多肢\n'
    ' "face_swap": bool,          // 同一画面出现多张脸/脸被换/身份错乱\n'
    ' "garbled_text": bool,       // 画面里有乱码/伪造文字/无意义字符\n'
    ' "stiff": bool,              // 人物僵硬如假人/站桩无表演\n'
    ' "reason": "一句中文说明最主要的问题，无问题写\'无明显缺陷\'"}\n'
    "无缺陷时所有 bool 为 false。"
)


class AnthropicVisionJudge:
    """Claude 视觉深判后端，实现 VisionLLMBackend.judge_frame。"""

    name = "anthropic-vision"

    def __init__(self, *, model: str = _DEFAULT_MODEL, client: Any = None,
                 api_key: str | None = None, max_tokens: int = 300) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self._client = client
        self._api_key = api_key

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic
            from assets_real.image_backend import config
            key = self._api_key or config.require("ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def judge_frame(self, frame: Path) -> dict[str, Any]:
        b64 = base64.standard_b64encode(Path(frame).read_bytes()).decode()
        media = "image/png" if str(frame).lower().endswith(".png") else "image/jpeg"
        msg = self._get_client().messages.create(
            model=self.model, max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": media, "data": b64}},
                {"type": "text", "text": _PROMPT}]}])
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
        return parse_vision_verdict(text)

    def judge_semantic(self, frame: Path, intent: str) -> "SemanticVisionResult":
        """语义判读：这一帧是否符合导演意图 + 有无不当内容（接吻/亲密等）。"""
        b64 = base64.standard_b64encode(Path(frame).read_bytes()).decode()
        media = "image/png" if str(frame).lower().endswith(".png") else "image/jpeg"
        prompt = _SEMANTIC_PROMPT.format(intent=intent[:200])
        msg = self._get_client().messages.create(
            model=self.model, max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": media, "data": b64}},
                {"type": "text", "text": prompt}]}])
        text = "".join(getattr(b, "text", "") for b in msg.content).strip()
        start, end = text.find("{"), text.rfind("}")
        try:
            d = json.loads(text[start:end + 1]) if start >= 0 and end > start else {}
        except (ValueError, TypeError):
            # 解析失败：诚实标记，默认符合意图(不误杀),但不冒充判过
            d = {"parse_error": True, "reason": f"语义判读无法解析:{text[:60]}"}
        return SemanticVisionResult(d)


_SEMANTIC_PROMPT = (
    "你是短剧成片质检员。这一帧的**导演意图**是：「{intent}」。只判两件事，严格输出 "
    "JSON（不要多余文字）：\n"
    '{{"matches_intent": bool,       // 画面演的是否符合导演意图的动作/情境\n'
    ' "inappropriate": bool,         // **仅**指与剧情无关的亲密/色情类灾难：'
    '接吻/搂抱/床戏/裸露/情欲。⚠️这是防AI把对峙戏错渲成亲密戏的专项闸。\n'
    ' "inappropriate_kind": "若有,一词说明如\'接吻\';无则空",\n'
    ' "reason": "一句中文说明"}}\n'
    "**严禁误报**：剧情道具与冲突元素都属正常,一律 inappropriate=false——"
    "账单/欠条/借条/单据/手机/钱/合同/文件等道具、争吵/推搡/瞪视/哭泣/下跪/受伤等"
    "剧情冲突,都不是不当内容。只有出现**接吻/亲密/裸露**这类才 true。"
)


class SemanticVisionResult:
    def __init__(self, d: dict[str, Any]):
        self.matches_intent = bool(d.get("matches_intent", True))
        self.inappropriate = bool(d.get("inappropriate", False))
        self.inappropriate_kind = str(d.get("inappropriate_kind", ""))
        self.reason = str(d.get("reason", ""))
        self.parse_error = bool(d.get("parse_error", False))


def parse_vision_verdict(text: str) -> dict[str, Any]:
    """从模型输出里稳健解析 JSON 判读；解析失败 → 标记不可解析（不假装通过）。"""
    raw = text.strip()
    if "```" in raw:                                # 去 markdown 围栏
        raw = raw.split("```")[1].replace("json", "", 1).strip() if raw.count("```") >= 2 \
            else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            d = json.loads(raw[start:end + 1])
        except (ValueError, TypeError):
            d = None
    else:
        d = None
    if not isinstance(d, dict):
        return {"ai_fake": False, "hand_face_broken": False, "stiff": False,
                "parse_error": True, "reason": f"深判输出无法解析：{text[:80]}"}
    out = {k: bool(d.get(k, False)) for k in
           ("ai_fake", "hand_face_broken", "face_swap", "garbled_text", "stiff")}
    out["reason"] = str(d.get("reason", ""))[:200]
    return out


__all__ = ["AnthropicVisionJudge", "parse_vision_verdict", "SemanticVisionResult"]
