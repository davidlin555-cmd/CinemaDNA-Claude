"""声音档 (Phase E2/E3) —— 性别 → 具体音色 + 每角色独立音色 + 情绪韵律。

- 每个性别一个**音色池**，按 character_id 稳定派生 → 每个角色一把独立嗓子（不再只两档）
- 情绪韵律：由镜头情绪强度推 voice_settings（稳定度/风格）+ 语速 rate
"""

from __future__ import annotations

import hashlib
from typing import Any

#: 每性别的音色池（ElevenLabs 公开 voice_id，多语言模型下支持中文）
VOICE_POOLS: dict[str, list[dict[str, str]]] = {
    "female": [
        {"voice_id": "21m00Tcm4TlvDq8ikWAM", "name": "Rachel"},
        {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Bella"},
        {"voice_id": "AZnzlk1XvdvUeBnXmlld", "name": "Domi"},
        {"voice_id": "MF3mGyEYCl7XYWbV9V6O", "name": "Elli"},
    ],
    "male": [
        {"voice_id": "pNInz6obpgDQGcFmaJgB", "name": "Adam"},
        {"voice_id": "ErXwobaYiN019PkySvjV", "name": "Antoni"},
        {"voice_id": "TxGEqnHWrfWFTfGW9XjX", "name": "Josh"},
        {"voice_id": "VR6AewLTigWG4xSOukaG", "name": "Arnold"},
    ],
}

#: 向后兼容：每性别的默认（池首个）
VOICE_PROFILES: dict[str, dict[str, str]] = {
    "female": VOICE_POOLS["female"][0],
    "male": VOICE_POOLS["male"][0],
}


def voice_for(gender: str) -> dict[str, str]:
    return VOICE_PROFILES.get(gender, VOICE_PROFILES["female"])


def gender_of(voice_id: str) -> str:
    """反查某 voice_id 属于哪个性别池（跨后端映射用）；未知退 female。"""
    for g, pool in VOICE_POOLS.items():
        if any(v["voice_id"] == voice_id for v in pool):
            return g
    return "female"


def pool_index(voice_id: str) -> int:
    """该 voice_id 在其性别池里的序号（给别的后端选对应第几把嗓子）。"""
    for pool in VOICE_POOLS.values():
        for i, v in enumerate(pool):
            if v["voice_id"] == voice_id:
                return i
    return 0


def assign_character_voice(character_id: str, gender: str) -> dict[str, str]:
    """按角色 id 稳定分一把独立嗓子（同角色跨镜一致、不同角色尽量不同）。"""
    pool = VOICE_POOLS.get(gender if gender in VOICE_POOLS else "female")
    idx = int(hashlib.sha1((character_id or "").encode("utf-8")).hexdigest(), 16) % len(pool)
    return pool[idx]


def voice_settings_for(emotion: dict[str, Any] | None,
                       prosody: dict[str, Any] | None = None) -> dict[str, float]:
    """情绪 → 韵律参数。强情绪：更不稳定（起伏大）、风格更强、语速略快。

    若 PerformanceDNA 的对白语气 Agent 给了 `prosody`（rate/stability/style），
    优先采用它——这就是声画一致（嘴上的语气 = 耳朵里的语气）。
    """
    intensity = float((emotion or {}).get("intensity", 0.5) or 0.5)
    base = {
        "stability": round(max(0.15, 0.65 - 0.45 * intensity), 3),
        "style": round(min(0.9, 0.15 + 0.65 * intensity), 3),
        "rate": round(1.0 + 0.25 * (intensity - 0.5) * 2, 3),   # ~0.75..1.25
        "intensity": round(intensity, 3),
    }
    if prosody:
        for k in ("rate", "stability", "style"):
            if k in prosody:
                base[k] = round(float(prosody[k]), 3)
    return base


def apply_voice(binding: dict[str, Any]) -> bool:
    """按 speaker + voice_gender 分配独立音色，补 voice_id / voice_name。返回是否有改动。"""
    prof = assign_character_voice(binding.get("speaker", ""),
                                  binding.get("voice_gender", ""))
    changed = False
    if binding.get("voice_id") != prof["voice_id"]:
        binding["voice_id"] = prof["voice_id"]
        binding["voice_name"] = prof["name"]
        changed = True
    return changed


__all__ = [
    "VOICE_POOLS", "VOICE_PROFILES", "voice_for", "assign_character_voice",
    "voice_settings_for", "apply_voice",
]
