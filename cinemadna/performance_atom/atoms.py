"""表情/动作原子数据结构 + 种子原子库。

原子 = **通用可复用的表演单元**（不是从影视成片整段扒的表演）：
  - ExpressionAtom：一个表情动作（皱眉/瞪眼/咬唇…），带情绪/强度/面部区域/时长/可叠加
  - ActionAtom：一个肢体动作（握拳/后退/前逼/掏东西…），带身体部位/强度/时长/可否接触道具
  - ComposedSequence：把原子排成的一条镜头表演时间轴（表情链 + 动作链）

种子库覆盖短剧高频情绪（愤怒/绝望/威胁/决绝/恐惧/无助/冷静/屈辱）与动作，
每条都可被 AtomRetriever 按导演意图检索、SequenceComposer 组合、Backflow 回流。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExpressionAtom:
    atom_id: str
    tag: str                        # 皱眉/瞪眼/咬唇/垂眼/冷笑…
    emotion: str                    # 愤怒/绝望/威胁/决绝/恐惧/无助/冷静/屈辱
    intensity: float                # 0-1
    face_region: str                # brow/eyes/mouth/jaw/whole
    duration_sec: float
    stackable: bool = True          # 可与其它区域原子同时叠加

    def to_dict(self) -> dict[str, Any]:
        return {"atom_id": self.atom_id, "tag": self.tag, "emotion": self.emotion,
                "intensity": self.intensity, "face_region": self.face_region,
                "duration_sec": self.duration_sec, "stackable": self.stackable,
                "kind": "expression"}


@dataclass
class ActionAtom:
    atom_id: str
    tag: str                        # 握拳/后退半步/前逼/掏出手机/撕/指向/低头/转身…
    emotion: str
    intensity: float
    body_part: str                  # hands/arms/torso/head/whole
    duration_sec: float
    contact: bool = False           # 是否与道具/对手接触（手/桌/门/手机）
    stackable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"atom_id": self.atom_id, "tag": self.tag, "emotion": self.emotion,
                "intensity": self.intensity, "body_part": self.body_part,
                "duration_sec": self.duration_sec, "contact": self.contact,
                "stackable": self.stackable, "kind": "action"}


@dataclass
class ComposedSequence:
    shot_id: str
    story_function: str
    expression_track: list[dict[str, Any]] = field(default_factory=list)  # [{t, atom}]
    action_track: list[dict[str, Any]] = field(default_factory=list)
    #: 说话节拍（加重/停顿/爆发）——供 AudioDNA 韵律用；**还不是最终逐帧口型**
    speaking_beats: list[dict[str, Any]] = field(default_factory=list)
    is_final_lip_frames: bool = False    # 诚实：口型逐帧由音频阶段跟真实波形执行
    total_sec: float = 0.0
    atoms_used: list[str] = field(default_factory=list)
    serves_function: bool = True
    exaggerated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"shot_id": self.shot_id, "story_function": self.story_function,
                "expression_track": self.expression_track,
                "action_track": self.action_track,
                "speaking_beats": self.speaking_beats,
                "is_final_lip_frames": self.is_final_lip_frames,
                "total_sec": round(self.total_sec, 2),
                "atoms_used": self.atoms_used, "serves_function": self.serves_function,
                "exaggerated": self.exaggerated}

    def prosody_hint(self) -> dict[str, Any]:
        """给 AudioDNA 的语气提示（加重词/停顿/爆发）——表演语气驱动语音。"""
        return {"emphasis": [b for b in self.speaking_beats if b["type"] == "emphasis"],
                "pause": [b for b in self.speaking_beats if b["type"] == "pause"],
                "burst": [b for b in self.speaking_beats if b["type"] == "burst"]}

    def render_hint(self) -> str:
        """把原子时间轴翻译成 Kling 能用的**结构化提示词**（当前工具=提示词级驱动）。"""
        faces = "、".join(dict.fromkeys(e["atom"]["tag"] for e in self.expression_track))
        bodies = " → ".join(a["atom"]["tag"] for a in self.action_track)
        parts = []
        if bodies:
            parts.append(f"动作：{bodies}")
        if faces:
            parts.append(f"表情：{faces}")
        return "；".join(parts)


# ---------------------------------------------------------------------------
# 种子原子库（通用可复用；Backflow 会往里加成功组合）
# ---------------------------------------------------------------------------

def seed_expression_atoms() -> list[ExpressionAtom]:
    E = ExpressionAtom
    rows = [
        ("愤怒", "皱眉紧盯", "brow", 0.7), ("愤怒", "咬紧牙关", "jaw", 0.8),
        ("绝望", "垂眼失焦", "eyes", 0.6), ("绝望", "嘴角下拉", "mouth", 0.6),
        ("威胁", "冷冷斜视", "eyes", 0.7), ("威胁", "嘴角冷笑", "mouth", 0.7),
        ("决绝", "抿唇上扬", "mouth", 0.8), ("决绝", "眼神发狠", "eyes", 0.85),
        ("恐惧", "瞳孔收缩", "eyes", 0.7), ("恐惧", "嘴唇发颤", "mouth", 0.6),
        ("无助", "眼眶泛红", "eyes", 0.6), ("屈辱", "别过头去", "head", 0.6),
        ("冷静", "目光沉稳", "eyes", 0.5), ("冷静", "面无表情", "whole", 0.4),
    ]
    return [E(atom_id=f"exp_{i:03d}", tag=t, emotion=emo, intensity=inten,
              face_region=reg, duration_sec=1.2)
            for i, (emo, t, reg, inten) in enumerate(rows, 1)]


def seed_action_atoms() -> list[ActionAtom]:
    A = ActionAtom
    rows = [
        ("愤怒", "攥紧拳头", "hands", 0.8, False), ("愤怒", "一拳砸下", "hands", 0.9, True),
        ("绝望", "低头掩面", "head", 0.6, False), ("无助", "后退半步", "torso", 0.6, False),
        ("威胁", "上前逼近", "torso", 0.8, False), ("威胁", "抬手打断", "arms", 0.75, False),
        ("决绝", "掏出证据", "hands", 0.8, True), ("决绝", "撕碎纸张", "hands", 0.85, True),
        ("决绝", "指向对方", "arms", 0.8, False), ("恐惧", "手足无措", "whole", 0.6, False),
        ("屈辱", "攥皱单据", "hands", 0.7, True), ("冷静", "缓缓起身", "torso", 0.5, False),
    ]
    return [A(atom_id=f"act_{i:03d}", tag=t, emotion=emo, intensity=inten,
              body_part=bp, duration_sec=1.3, contact=contact)
            for i, (emo, t, bp, inten, contact) in enumerate(rows, 1)]


__all__ = ["ExpressionAtom", "ActionAtom", "ComposedSequence",
           "seed_expression_atoms", "seed_action_atoms"]
