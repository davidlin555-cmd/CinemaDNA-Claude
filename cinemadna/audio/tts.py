"""真实 TTS 后端 (Phase E2) —— ElevenLabs 文本转语音，预算闸前置保护。

与图像/视频后端同一套安全模型：
- 必须传 BudgetGate（默认关，未开预算任何真实提交被拒）
- HTTP 层可注入（测试用 mock，返回 (status, bytes)，不发真实请求、不花钱）
- 成功才记账

ElevenLabs TTS 返回的是二进制音频（audio/mpeg），所以 http 返回 (status, bytes)，
不是 JSON。时长按字数估算（中文约 4.5 字/秒），够"基本同步"用。
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import config
from render.budget import BudgetGate

#: 每字的预估计费单位（抽象 units，真实上限靠 BudgetGate.max_units 控）
_CHAR_UNIT_COST = 0.002
#: 中文语速估算：字/秒
_CHARS_PER_SEC = 4.5
_UA = "CinemaDNA-Audio/1.0"

#: http(method, url, headers, body) -> (status, bytes)
TTSHttpFn = Callable[[str, str, dict[str, str], dict[str, Any] | None], "tuple[int, bytes]"]


class TTSError(RuntimeError):
    """TTS 生成失败。"""


@dataclass
class TTSResult:
    text: str
    voice_id: str
    model: str
    file_relpath: str
    bytes: int
    duration_sec: float
    is_real_media: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "voice_id": self.voice_id, "model": self.model,
            "file_relpath": self.file_relpath, "bytes": self.bytes,
            "duration_sec": self.duration_sec, "is_real_media": True,
            "generation_method": "elevenlabs_tts",
        }


def estimate_duration(text: str) -> float:
    """按字数估算配音时长（秒），下限 0.8s。"""
    n = len([c for c in text if not c.isspace()])
    return round(max(0.8, n / _CHARS_PER_SEC), 2)


def _real_http(method: str, url: str, headers: dict[str, str],
               body: dict[str, Any] | None) -> tuple[int, bytes]:
    import json
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:  # type: ignore[name-defined]
        return e.code, e.read()


class ElevenLabsTTSBackend:
    """ElevenLabs TTS：给定文本 + 音色 → 真实音频文件。本地落盘、预算闸前置。"""

    def __init__(
        self,
        budget: BudgetGate,
        *,
        api_key: str | None = None,
        base: str = "https://api.elevenlabs.io",
        model: str = "eleven_multilingual_v2",
        http: TTSHttpFn | None = None,
        max_retries: int = 3,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(budget, BudgetGate):
            raise TTSError("TTS 后端必须传入 BudgetGate（真实付费前置保护）")
        self.budget = budget
        self.api_key = api_key or config.require("ELEVENLABS_API_KEY")
        self.base = base.rstrip("/")
        self.model = model
        self._http = http or _real_http
        self.max_retries = max_retries
        self._sleep = sleep_fn
        self.ext = ".mp3"      # ElevenLabs 返回 audio/mpeg

    def synthesize(self, *, text: str, voice_id: str, dest: Path,
                   item_id: str, settings: dict[str, float] | None = None) -> TTSResult:
        # ① 预算闸：不过闸绝不 POST
        est = self.budget.authorize_generic(
            item_id=item_id, est_units=_CHAR_UNIT_COST * max(1, len(text)),
            kind="audio", model=self.model)
        # ② 真实提交（429 退避重试）；情绪韵律 → voice_settings
        url = f"{self.base}/v1/text-to-speech/{voice_id}"
        headers = {"User-Agent": _UA, "xi-api-key": self.api_key,
                   "Content-Type": "application/json", "Accept": "audio/mpeg"}
        s = settings or {}
        body = {"text": text, "model_id": self.model, "voice_settings": {
            "stability": float(s.get("stability", 0.5)),
            "style": float(s.get("style", 0.3)),
            "similarity_boost": 0.75, "use_speaker_boost": True}}
        status, payload = 0, b""
        for attempt in range(self.max_retries):
            status, payload = self._http("POST", url, headers, body)
            if status != 429:
                break
            self._sleep(min(2 ** attempt, 8))
        if status != 200:
            msg = payload[:200].decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
            raise TTSError(f"{item_id}: TTS 失败 HTTP {status} {msg}")
        if not isinstance(payload, (bytes, bytearray)) or len(payload) < 200:
            raise TTSError(f"{item_id}: TTS 返回音频过小（{len(payload)} 字节）")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        # ③ 成功才记账
        self.budget.record(shot_id=item_id, units=est)
        return TTSResult(
            text=text, voice_id=voice_id, model=self.model,
            file_relpath=dest.name, bytes=len(payload),
            duration_sec=estimate_duration(text))


def _download(url: str, dest: Path) -> None:  # 备用（当前 TTS 直接返回 bytes）
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())


import urllib.error  # noqa: E402  —— 供 _real_http 的异常分支

__all__ = ["ElevenLabsTTSBackend", "TTSResult", "TTSError", "estimate_duration"]
