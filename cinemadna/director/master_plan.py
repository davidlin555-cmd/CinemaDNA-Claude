"""DirectorMasterPlan —— 全厂唯一上游真源（宪法层数据结构）。

导演理解剧本后输出《导演总谱》，hash 锁定。下游 SceneDNA/IdentityDNA/PropDNA/
PerformanceDNA/Render **只读执行**，不得改叙事字段、不得发明总谱里没有的角色/道具/
动作。任何成功产物必须能追溯到总谱字段。

结构（对应任务书三）：
  DirectorMasterPlan: story_spine / cast_lock / scene_layout / prop_plan /
                      emotion_curve / density_plan / shot_list[]
  ShotPlan: shot_id / story_function / action_beats[] / performance_intent /
            dialogue_owner / camera / prop_usage / entry_state / exit_state
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

#: 合法的镜头叙事功能（每镜必须有其一）
STORY_FUNCTIONS = ("钩子", "施压", "揭示", "反应", "反转", "决策", "收束", "铺垫")

SCHEMA = "director_master_plan.v1"


@dataclass
class ShotPlan:
    shot_id: str
    order: int
    scene_id: str
    story_function: str                    # 施压/揭示/反应/反转/决策/钩子/收束
    action_beats: list[dict[str, Any]]     # 可执行动作：body_action/face_action/micro_beats
    performance_intent: str
    dialogue_owner: str | None             # 说话人 character_id，无台词=None
    line: str
    camera: dict[str, Any]                 # shot_size/angle/movement/duration_sec/intent
    prop_usage: dict[str, Any]             # {prop_id, contact} 或 {}
    entry_state: str
    exit_state: str
    beat_type: str = ""
    internal_shift: str = ""               # 镜内状态变化 起→转→结
    emotion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shot_id": self.shot_id, "order": self.order, "scene_id": self.scene_id,
            "story_function": self.story_function, "action_beats": self.action_beats,
            "performance_intent": self.performance_intent,
            "dialogue_owner": self.dialogue_owner, "line": self.line,
            "camera": self.camera, "prop_usage": self.prop_usage,
            "entry_state": self.entry_state, "exit_state": self.exit_state,
            "beat_type": self.beat_type, "internal_shift": self.internal_shift,
            "emotion": self.emotion,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ShotPlan":
        return ShotPlan(
            shot_id=d["shot_id"], order=int(d["order"]), scene_id=d["scene_id"],
            story_function=d.get("story_function", ""),
            action_beats=list(d.get("action_beats") or []),
            performance_intent=d.get("performance_intent", ""),
            dialogue_owner=d.get("dialogue_owner"), line=d.get("line", ""),
            camera=dict(d.get("camera") or {}), prop_usage=dict(d.get("prop_usage") or {}),
            entry_state=d.get("entry_state", ""), exit_state=d.get("exit_state", ""),
            beat_type=d.get("beat_type", ""), internal_shift=d.get("internal_shift", ""),
            emotion=d.get("emotion", ""))


@dataclass
class DirectorMasterPlan:
    story_id: str
    story_spine: dict[str, Any]            # 冲突主线/人物关系/本剧目标
    cast_lock: dict[str, dict[str, Any]]   # character_id → {name/role/stance/power/appearance}
    scene_layout: list[dict[str, Any]]     # [{scene_id, location, spatial, blocking}]
    prop_plan: list[dict[str, Any]]        # [{prop_id, state, contact}]
    emotion_curve: list[dict[str, Any]]    # [{shot_id, intensity, tone}]
    density_plan: dict[str, Any]           # {hook, reveals[], turns[]}
    shot_list: list[ShotPlan]
    plan_hash: str = ""
    locked: bool = False
    schema_version: str = SCHEMA

    # -- hash 锁定 ------------------------------------------------------

    def _hashable(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "story_id": self.story_id,
            "story_spine": self.story_spine, "cast_lock": self.cast_lock,
            "scene_layout": self.scene_layout, "prop_plan": self.prop_plan,
            "emotion_curve": self.emotion_curve, "density_plan": self.density_plan,
            "shot_list": [s.to_dict() for s in self.shot_list],
        }

    def compute_hash(self) -> str:
        blob = json.dumps(self._hashable(), ensure_ascii=False, sort_keys=True)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def lock(self) -> "DirectorMasterPlan":
        """签发锁定：算 hash、置 locked。只有验收通过后才应调用。"""
        self.plan_hash = self.compute_hash()
        self.locked = True
        return self

    def verify_lock(self) -> bool:
        """校验总谱未被篡改（下游只读的凭证）。"""
        return self.locked and self.plan_hash == self.compute_hash()

    def to_dict(self) -> dict[str, Any]:
        d = self._hashable()
        d.update({"plan_hash": self.plan_hash, "locked": self.locked})
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "DirectorMasterPlan":
        return DirectorMasterPlan(
            story_id=d["story_id"], story_spine=dict(d.get("story_spine") or {}),
            cast_lock=dict(d.get("cast_lock") or {}),
            scene_layout=list(d.get("scene_layout") or []),
            prop_plan=list(d.get("prop_plan") or []),
            emotion_curve=list(d.get("emotion_curve") or []),
            density_plan=dict(d.get("density_plan") or {}),
            shot_list=[ShotPlan.from_dict(x) for x in d.get("shot_list") or []],
            plan_hash=d.get("plan_hash", ""), locked=bool(d.get("locked")),
            schema_version=d.get("schema_version", SCHEMA))

    # -- 便捷 -----------------------------------------------------------

    @property
    def shots(self) -> list[ShotPlan]:
        return sorted(self.shot_list, key=lambda s: s.order)


__all__ = ["DirectorMasterPlan", "ShotPlan", "STORY_FUNCTIONS", "SCHEMA"]
