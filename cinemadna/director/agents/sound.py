"""SoundIntentAgent（J 块 owner）—— 每镜声音意图块的唯一产出者（上游意图，非执行）。

执行层已有 AudioDNA；但**上游声音意图**（对白优先级 / 音效线索 / BGM 情绪）原本无人
authoring。这块改的是音频生成输入（AudioDNA 据此配 SFX/BGM），属"能改生成输入"，
故补一个薄产出 Agent。sfx_cues 由本镜真实动作/道具派生（禁默认填空）。
"""

from __future__ import annotations

from typing import Any

from director.shot_task_sheet import ShotMasterTaskSheet, write_block

#: 动作/道具关键词 → 音效线索（按本镜真实动作派生，不猜）
_SFX_BY_KEYWORD = (
    ("掏出", "纸张/布料摩擦声"), ("撕", "撕纸声"), ("摔", "重物撞击声"),
    ("砸", "拍击声"), ("门", "开关门声"), ("手机", "手机提示音"),
    ("单据", "纸张翻动声"), ("欠条", "纸张翻动声"), ("脚步", "脚步声"),
    ("逼近", "逼近脚步声"),
)
#: 叙事功能 → BGM 情绪
_BGM_BY_FN = {"hook": "unease", "pressure": "tense", "evidence": "reveal",
              "reaction": "hold", "reverse": "turn", "cliff": "suspense"}


class SoundIntentAgent:
    name = "sound"

    def produce(self, sheet: ShotMasterTaskSheet, *, story_function: str,
                speech: bool, action_verbs: list[str] | None = None,
                prop_ids: list[str] | None = None) -> ShotMasterTaskSheet:
        """产出并写入 J 声音意图块（唯一 owner=sound）。"""
        blob = " ".join((action_verbs or []) + (prop_ids or []))
        sfx = sorted({v for k, v in _SFX_BY_KEYWORD if k in blob})
        content = {
            # 声音总线优先级：对白 > 音效 > BGM
            "dialogue_priority": 1 if speech else 0,
            "sfx_cues": sfx,
            "bgm_mood": _BGM_BY_FN.get(story_function, "neutral"),
            "bgm_duck_under_dialogue": bool(speech),
        }
        return write_block(sheet, "sound", self.name, content)

    def review(self, sheet: ShotMasterTaskSheet) -> list[dict[str, Any]]:
        j = sheet.sound or {}
        out: list[dict[str, Any]] = []
        if j.get("dialogue_priority") not in (0, 1):
            out.append({"agent": self.name, "block": "sound",
                        "message": "缺 dialogue_priority"})
        if not j.get("bgm_mood"):
            out.append({"agent": self.name, "block": "sound", "message": "缺 bgm_mood"})
        return out


__all__ = ["SoundIntentAgent"]
