"""声纹克隆 (Phase E5) —— 上传样本 → 克隆音色 ID，登记给角色。

诚实边界：真正的声纹**训练**在 ElevenLabs 侧（上传样本、付费 Pro 权限）。本模块提供
"请求-侧 + 消费-侧"的完整闭环——上传接口 + 拿到 voice_id 后 pin 进 CharacterVoiceRegistry。
真实上传需要有克隆权限的 key（当前 .env 的 key 连 text_to_speech 都缺权限，会 401）。

uploader 可注入：测试用假 uploader，不联网、不上传。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import config

#: uploader(name, sample_paths, api_key) -> voice_id
UploaderFn = Callable[[str, list[Path], str], str]


class CloneError(RuntimeError):
    pass


def _real_uploader(name: str, samples: list[Path], api_key: str) -> str:
    """真实调 ElevenLabs /v1/voices/add（multipart）。需克隆权限，否则 401。"""
    import requests  # 项目已装
    files = [("files", (p.name, p.read_bytes(), "audio/mpeg")) for p in samples]
    resp = requests.post(
        "https://api.elevenlabs.io/v1/voices/add",
        headers={"xi-api-key": api_key},
        data={"name": name}, files=files, timeout=120)
    if resp.status_code != 200:
        raise CloneError(f"克隆失败 HTTP {resp.status_code} {resp.text[:160]}")
    vid = (resp.json() or {}).get("voice_id")
    if not vid:
        raise CloneError("克隆返回无 voice_id")
    return vid


class ElevenLabsVoiceCloner:
    """把角色的参考音频克隆成专属音色，并登记给该角色。"""

    def __init__(self, *, api_key: str | None = None,
                 uploader: UploaderFn | None = None) -> None:
        self._api_key = api_key
        self._uploader = uploader or _real_uploader

    def clone_for(self, registry, *, character_id: str, name: str,
                  samples: list[Path], gender: str = "") -> dict[str, Any]:
        """上传样本克隆音色，成功后 pin 进登记表。返回结果字典（含 voice_id）。"""
        if not samples or not all(Path(p).is_file() for p in samples):
            return {"ok": False, "error": "缺有效样本音频"}
        key = self._api_key or (config.require("ELEVENLABS_API_KEY")
                                if config.has("ELEVENLABS_API_KEY") else "")
        if not key:
            return {"ok": False, "error": "缺 ELEVENLABS_API_KEY"}
        try:
            vid = self._uploader(name, [Path(p) for p in samples], key)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        registry.pin(character_id, voice_id=vid, voice_name=f"{name}(克隆)",
                     gender=gender, cloned=True)
        return {"ok": True, "character_id": character_id, "voice_id": vid,
                "cloned": True}


__all__ = ["ElevenLabsVoiceCloner", "CloneError"]
