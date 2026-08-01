"""免费中文神经 TTS 后端 (Phase E4) —— Microsoft Edge TTS（edge-tts）。

不需要 API Key、免费，中文神经音色质量高（zh-CN-Xiaoxiao 女 / Yunxi 男 等），
直接解掉"中文真实配音"这条卡点（ElevenLabs 权限/本机无中文音色都绕开）。

需要联网（走 Microsoft 在线合成）。合成器可注入（测试用假 synth，不联网、不产声）。
按 voice_id 反查性别 + 池序号 → 选一把对应中文神经嗓子；情绪 → 语速 rate。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from audio import voices

#: 每性别的中文神经音色池（与 ElevenLabs 池一一对位，保证跨后端角色嗓子稳定）
_EDGE_VOICES = {
    "female": ["zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural",
               "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural"],
    "male": ["zh-CN-YunxiNeural", "zh-CN-YunyangNeural",
             "zh-CN-YunjianNeural", "zh-CN-YunxiaNeural"],
}

#: synth(text, edge_voice, rate_str, dest) -> None
EdgeSynthFn = Callable[[str, str, str, Path], None]


class EdgeTTSError(RuntimeError):
    pass


@dataclass
class EdgeTTSResult:
    text: str
    voice_id: str
    model: str
    file_relpath: str
    bytes: int
    duration_sec: float
    is_real_media: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "voice_id": self.voice_id, "model": self.model,
                "file_relpath": self.file_relpath, "bytes": self.bytes,
                "duration_sec": self.duration_sec, "is_real_media": True,
                "generation_method": "edge_tts"}


def _rate_str(settings: dict[str, float] | None) -> str:
    rate = float((settings or {}).get("rate", 1.0))
    pct = int(round((rate - 1.0) * 100))
    return f"{pct:+d}%"


def edge_voice_for(voice_id: str) -> str:
    """ElevenLabs voice_id → 对应中文神经音色（同性别、同池序号 → 角色嗓子稳定）。"""
    g = voices.gender_of(voice_id)
    pool = _EDGE_VOICES.get(g, _EDGE_VOICES["female"])
    return pool[voices.pool_index(voice_id) % len(pool)]


def _estimate(text: str) -> float:
    n = len([c for c in text if not c.isspace()])
    return round(max(0.6, n / 4.5), 2)


def _default_synth(text: str, voice: str, rate: str, dest: Path) -> None:
    import edge_tts

    async def go() -> None:
        await edge_tts.Communicate(text, voice, rate=rate).save(str(dest))

    asyncio.run(go())


class EdgeTTSBackend:
    """免费中文神经 TTS。budget 可选（免费）；synth 可注入（测试）。"""

    ext = ".mp3"

    def __init__(self, *, budget=None, synth: EdgeSynthFn | None = None) -> None:
        self.budget = budget
        self._synth = synth or _default_synth

    def synthesize(self, *, text: str, voice_id: str, dest: Path,
                   item_id: str, settings: dict[str, float] | None = None
                   ) -> EdgeTTSResult:
        if self.budget is not None:
            self.budget.authorize_generic(item_id=item_id, est_units=0.0,
                                          kind="audio", model="edge_tts")
        dest.parent.mkdir(parents=True, exist_ok=True)
        edge_voice = edge_voice_for(voice_id)
        try:
            self._synth(text, edge_voice, _rate_str(settings), dest)
        except Exception as e:  # noqa: BLE001
            raise EdgeTTSError(f"{item_id}: edge-tts 失败 {str(e)[:160]}") from e
        if not dest.is_file() or dest.stat().st_size < 200:
            raise EdgeTTSError(f"{item_id}: edge-tts 未产出有效音频")
        if self.budget is not None:
            self.budget.record(shot_id=item_id, units=0.0)
        return EdgeTTSResult(
            text=text, voice_id=voice_id, model=f"edge_tts/{edge_voice}",
            file_relpath=dest.name, bytes=dest.stat().st_size,
            duration_sec=_estimate(text))


__all__ = ["EdgeTTSBackend", "EdgeTTSResult", "EdgeTTSError", "edge_voice_for"]
