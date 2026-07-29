"""角色声音登记表 (Phase E4) —— 每角色固定一把嗓子，支持克隆音色接入。

从"按 id 哈希临时分配"升级为"可持久、可覆写、可接克隆音色"：
- 未登记的角色：仍按性别池稳定派生（跨镜一致）
- 已登记的角色：用登记的具体 voice_id（可以是 ElevenLabs 克隆音色 ID）
- 可序列化 → 落 Bundle / 复用；克隆音色只需 pin 一个 voice_id 即插入，无需改代码

真正的声纹克隆（上传样本训练）在 ElevenLabs 侧，本表提供"克隆结果如何被角色消费"
这一半闭环：拿到克隆 voice_id 后 `pin(cid, voice_id, cloned=True)` 即生效。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import voices


@dataclass
class CharacterVoiceRegistry:
    #: character_id → {voice_id, voice_name, gender, cloned}
    voices_by_char: dict[str, dict[str, Any]] = field(default_factory=dict)

    def pin(self, character_id: str, *, voice_id: str, voice_name: str = "",
            gender: str = "", cloned: bool = False) -> None:
        """给角色固定一把具体嗓子（含克隆音色）。"""
        self.voices_by_char[character_id] = {
            "voice_id": voice_id, "voice_name": voice_name or voice_id,
            "gender": gender, "cloned": bool(cloned)}

    def resolve(self, character_id: str, gender: str) -> dict[str, Any]:
        """取角色嗓子：登记优先，否则按性别池稳定派生。"""
        pinned = self.voices_by_char.get(character_id)
        if pinned:
            return dict(pinned)
        prof = voices.assign_character_voice(character_id, gender)
        return {"voice_id": prof["voice_id"], "voice_name": prof["name"],
                "gender": gender, "cloned": False}

    def apply(self, binding: dict[str, Any]) -> bool:
        """把角色嗓子写进台词绑定（voice_id/voice_name/cloned）。返回是否有改动。"""
        v = self.resolve(binding.get("speaker", ""), binding.get("voice_gender", ""))
        changed = binding.get("voice_id") != v["voice_id"]
        binding["voice_id"] = v["voice_id"]
        binding["voice_name"] = v["voice_name"]
        if v.get("cloned"):
            binding["voice_cloned"] = True
        return changed

    def to_dict(self) -> dict[str, Any]:
        return {"voices_by_char": dict(self.voices_by_char)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CharacterVoiceRegistry":
        return cls(voices_by_char=dict((data or {}).get("voices_by_char") or {}))


__all__ = ["CharacterVoiceRegistry"]
