"""LightingAgent（H 块 owner）—— 每镜灯光块的唯一产出者。

复盘：A–K 里**灯光整块原本无人产出**。灯光直接改生成输入（首帧/渲染的 mood、
轮廓分离光 key_rim），是"能改生成输入"的专业块，故补这一个专用 Agent（非讨论型）。

只写 H 灯光块（经 write_block 归属校验），不碰其它块。按镜的叙事功能 + 情绪强度 +
场次灯光宪法决定 mood / key_rim / 继承或覆盖。禁默认值填空——每镜显式产出。
"""

from __future__ import annotations

from typing import Any

from director.shot_task_sheet import ShotMasterTaskSheet, write_block

#: 叙事功能 → 灯光基调（英文任务单枚举）
_MOOD_BY_FN = {
    "hook": "cold", "pressure": "pressure", "evidence": "focused",
    "reaction": "soft", "reverse": "cold", "cliff": "pressure",
}
#: 需要轮廓分离光（把人物从暗背景里剥出来）的功能
_RIM_FUNCS = ("pressure", "reverse", "cliff")


class LightingAgent:
    name = "lighting"

    def produce(self, sheet: ShotMasterTaskSheet, *, story_function: str,
                intensity: float = 0.6, scene_lighting: dict[str, Any] | None = None,
                night: bool = True) -> ShotMasterTaskSheet:
        """产出并写入 H 灯光块（唯一 owner=lighting）。返回同一 sheet。"""
        scene_mood = (scene_lighting or {}).get("mood")
        mood = _MOOD_BY_FN.get(story_function, scene_mood or "neutral")
        # 轮廓光：强情绪/对峙功能/夜戏需要把主体从暗背景分离
        key_rim = bool(intensity >= 0.6 or story_function in _RIM_FUNCS
                       or (night and mood in ("pressure", "cold")))
        # 与场次灯光宪法一致则继承，否则显式覆盖（可追溯）
        source = "inherit_scene" if (scene_mood and mood == scene_mood) else "override"
        content = {
            "mood": mood,
            "key_rim": key_rim,
            "source": source,
            "inherit_scene": scene_mood,
            "contrast": "high" if mood in ("pressure", "cold") else "soft",
            "notes": ("夜戏轮廓分离，压迫高反差" if key_rim
                      else "柔光，情绪缓和"),
        }
        return write_block(sheet, "lighting", self.name, content)

    def review(self, sheet: ShotMasterTaskSheet) -> list[dict[str, Any]]:
        """自查：灯光块是否齐（mood + key_rim）。缺 → 返回问题（BLOCKED 由 validator 定）。"""
        lg = sheet.lighting or {}
        out: list[dict[str, Any]] = []
        if not str(lg.get("mood") or "").strip():
            out.append({"agent": self.name, "block": "lighting", "message": "缺 mood"})
        if lg.get("key_rim") not in (True, False):
            out.append({"agent": self.name, "block": "lighting",
                        "message": "缺 key_rim"})
        return out


__all__ = ["LightingAgent"]
