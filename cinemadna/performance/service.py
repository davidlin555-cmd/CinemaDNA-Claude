"""PerformanceDNA · Phase 3 Service —— 让角色有表演，而不是僵硬表情

对应多智能体文档 §2.7。四个 Agent 落成方法：

| 文档中的 Agent                 | 本服务对应                          |
|--------------------------------|-------------------------------------|
| Acting Beat Planner            | `plan_acting_beats()`               |
| Micro-expression Designer      | `design_micro_expression()`         |
| Blocking & Space Matching      | `plan_blocking()`                   |
| Performance Bible Manager      | `update_bible()` / `bibles`         |
| Performance Driver Library     | 未实现（真人动作驱动，Phase 3 不做）|

**协作要点（文档原话）**：Performance 输出直接进入 Shot Contract 的
`performance` 字段，Render 必须遵守。所以本服务处理完就**签发合约**——
签发即锁定 contract_hash，之后任何改动都会让渲染阶段拒收。

微表情不是随机的：它由「人物性格关键词 + 当前情绪强度 + 景别」共同决定。
性格来自 ScriptBrain 的 personality_keywords，情绪来自 Director 的情绪曲线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from asset_brain.common import schemas
from director.shot_contract import SHOT_CLOSEUP, SHOT_INSERT, SHOT_REACTION, sign_contract

#: 性格关键词 → 标志性微表情（Performance Bible 的种子）
_EXPRESSION_BY_TRAIT: Final[dict[str, str]] = {
    "压抑": "下颌收紧，视线避开对方",
    "坚韧": "眉心微锁但目光不移",
    "疲惫": "眼睑沉重，呼吸放缓",
    "倨傲": "下巴微抬，嘴角轻蔑",
    "势利": "眼神先扫对方衣着",
    "掌控欲": "身体前倾，手指轻叩桌面",
    "刻薄": "唇线绷直，冷笑一瞬",
    "现实": "眼神快速权衡",
    "克制": "喉结滚动一次，不出声",
    "偏执": "瞳孔锁定不移",
    "敏锐": "眼球快速扫视细节",
    "冷静": "面部几乎不动",
    "深藏不露": "微笑但眼底无温度",
    "圆滑": "先笑后语",
    "隐忍": "咬肌轻微鼓动",
    "要强": "背脊挺直，肩线不塌",
    "倔强": "抿嘴，别开头",
    "疏离": "身体侧开半步",
    "固执": "眉头拧紧不松",
    "沉默": "长时间不接话",
    "清醒": "眼神澄澈直视",
    "天真": "眼睛睁大",
    "热心": "身体前倾靠近",
}

#: 情绪强度 → 通用生理反应（性格库没命中时的兜底）
_INTENSITY_FALLBACK: Final[tuple[tuple[float, str], ...]] = (
    (0.90, "呼吸加快，瞳孔收缩"),
    (0.70, "眉心微锁，吞咽一次"),
    (0.45, "神情克制，目光平稳"),
    (0.0, "面色平静"),
)

#: 景别 → 表演幅度（特写靠微表情，全景靠肢体）
_SCALE_BY_SHOT: Final[dict[str, str]] = {
    SHOT_CLOSEUP: "微表情主导，肢体几乎不动",
    SHOT_REACTION: "以眼神与呼吸变化承接对方台词",
    SHOT_INSERT: "只保留手部动作",
}
_DEFAULT_SCALE: Final = "肢体与走位主导，表情适度"


def design_micro_expression(
    traits: list[str], intensity: float, shot_type: str
) -> dict[str, Any]:
    """由性格 + 情绪强度 + 景别设计微表情。"""
    hits = [_EXPRESSION_BY_TRAIT[t] for t in traits if t in _EXPRESSION_BY_TRAIT]
    if not hits:
        hits = [next(text for th, text in _INTENSITY_FALLBACK if intensity >= th)]
    return {
        "primary": hits[0],
        "secondary": hits[1] if len(hits) > 1 else None,
        "physiological": next(
            text for th, text in _INTENSITY_FALLBACK if intensity >= th
        ),
        "scale": _SCALE_BY_SHOT.get(shot_type, _DEFAULT_SCALE),
        "intensity": round(float(intensity), 4),
    }


@dataclass
class PerformanceResult:
    contracts: list[dict[str, Any]]
    acting_beats: dict[str, dict[str, Any]]        # shot_id → beats 文档
    blocking: dict[str, dict[str, Any]]            # scene_id → 走位
    bibles: dict[str, dict[str, Any]]              # character_id → 表演圣经
    warnings: list[str] = field(default_factory=list)

    def artifacts(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for shot_id, doc in self.acting_beats.items():
            out[f"05_performance/acting_beats/{shot_id}.json"] = doc
        for scene_id, doc in self.blocking.items():
            out[f"05_performance/blocking/{scene_id}.json"] = doc
        for cid, doc in self.bibles.items():
            out[f"05_performance/bible/{cid}.json"] = doc
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "shots_with_performance": len(self.acting_beats),
            "beats_total": sum(
                len(d["beats"]) for d in self.acting_beats.values()
            ),
            "characters_with_bible": len(self.bibles),
            "warnings": self.warnings,
        }


class PerformanceDNAService:
    """PerformanceDNA Phase 3 Service。"""

    def __init__(self) -> None:
        #: 跨故事累积的角色表演圣经（真实系统里应落库）
        self.bibles: dict[str, dict[str, Any]] = {}
        #: PerformanceAtomDNA：表情/动作原子检索·组合·回流（跨镜共用一库，回流累积）
        from performance_atom.service import PerformanceAtomDNAService
        self._atom = PerformanceAtomDNAService()

    # ------------------------------------------------------------------

    def plan_acting_beats(
        self, contract: dict[str, Any], characters_by_id: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """把台词与动作拆成表演节拍，每个节拍配微表情与肢体。"""
        shot_id = contract["shot_id"]
        intensity = float(contract["emotion"].get("intensity", 0.5))
        beats: list[dict[str, Any]] = []

        in_frame = [c["character_id"] for c in contract["assets"]["characters"]]
        lines = contract.get("dialogue") or []
        # 这一镜要发生的真实动作（来自 LLM 节拍描述 = story_function），驱动表演肢体
        action = (contract.get("story_function") or "").strip()

        for i, line in enumerate(lines):
            cid = line["character_id"]
            traits = list((characters_by_id.get(cid) or {}).get("personality_keywords") or [])
            beats.append(
                {
                    "beat_index": len(beats),
                    "type": "LINE",
                    "character_id": cid,
                    "cue": line["line"],
                    "micro_expression": design_micro_expression(
                        traits, intensity, contract["shot_type"]
                    ),
                    # 真实动作优先（节拍描述），无则退到强度默认
                    "body": action or ("说话时手指收紧" if intensity >= 0.8 else "自然站姿"),
                }
            )
            # 有人说话就必然有人听：给同框的其他人配反应节拍
            for other in in_frame:
                if other == cid:
                    continue
                other_traits = list(
                    (characters_by_id.get(other) or {}).get("personality_keywords") or []
                )
                beats.append(
                    {
                        "beat_index": len(beats),
                        "type": "REACTION",
                        "character_id": other,
                        "cue": f"听到「{line['line']}」",
                        "micro_expression": design_micro_expression(
                            other_traits, intensity, contract["shot_type"]
                        ),
                        "body": "重心后移半步",
                    }
                )

        if not beats:
            # 无台词镜头（建立镜、插入镜）也必须有表演节拍，否则是死画面
            for cid in in_frame or ["__no_actor__"]:
                traits = list((characters_by_id.get(cid) or {}).get("personality_keywords") or [])
                beats.append(
                    {
                        "beat_index": len(beats),
                        "type": "ACTION",
                        "character_id": cid,
                        "cue": contract.get("notes") or "沉默中的动作",
                        "micro_expression": design_micro_expression(
                            traits, intensity, contract["shot_type"]
                        ),
                        "body": action or "缓慢转身/停顿",
                    }
                )

        # PerformanceAtomDNA：按导演意图检索+组合表情/动作原子 → 提示词级驱动 + 回流
        # 五阶段·第2阶段"表演规划"：表情原子链 + 肢体动作原子链 + **说话节拍**
        #（加重/停顿/爆发）。说话节拍还不是逐帧口型（那属第4阶段跟真实音频执行）。
        intent = {
            "shot_id": shot_id, "story_function": contract.get("story_function", ""),
            "emotion_target": (contract["emotion"].get("mood") or ""),
            "intensity": intensity, "exaggerate": intensity >= 0.85,
            "is_speaking": bool(lines),
            "line": (lines[0]["line"] if lines else ""),
            "beats": [str(b.get("cue", ""))[:10] for b in beats][:3] or None,
            "body_focus": "hands", "face_focus": "eyes + jaw"}
        atom = self._atom.compose(
            intent, target_sec=float(contract.get("duration_sec", 3)))
        contract["performance_atoms_hint"] = atom.sequence.render_hint()
        contract["performance_atoms_ok"] = atom.passed
        # 说话节拍下传给第3阶段（声音）：驱动 TTS 韵律的加重/停顿/爆发
        contract["speaking_beats"] = atom.sequence.speaking_beats
        return {
            "schema_version": schemas.SCHEMA_ACTING_BEATS,
            "shot_id": shot_id,
            "scene_id": contract["scene_id"],
            "shot_type": contract["shot_type"],
            "emotion_intensity": intensity,
            "beats": beats,
            "atom_sequence": atom.sequence.to_dict(),
            "atom_gate": atom.gate.report(),
        }

    def plan_blocking(
        self, scene: dict[str, Any], character_ids: list[str]
    ) -> dict[str, Any]:
        """人物在场景中的走位（与 SceneDNA 的空间需求对齐）。"""
        marks = ["A", "B", "C", "D"]
        return {
            "schema_version": schemas.SCHEMA_ACTING_BEATS,
            "scene_id": scene["scene_id"],
            "spatial_needs": scene.get("spatial_needs", ""),
            "positions": [
                {
                    "character_id": cid,
                    "mark": marks[i % len(marks)],
                    "movement": "静止" if i else "从画左入画后停在中景位",
                }
                for i, cid in enumerate(character_ids)
            ],
            "camera_relation": scene.get("camera_intent", ""),
        }

    def update_bible(
        self, character_id: str, character: dict[str, Any], expression: dict[str, Any],
        shot_ref: str,
    ) -> dict[str, Any]:
        """维护角色专属表演圣经：习惯动作与标志性微表情。

        `shot_ref` 必须是**跨故事唯一**的引用（story_id/shot_id）。
        """
        b = self.bibles.setdefault(
            character_id,
            {
                "schema_version": schemas.SCHEMA_PERFORMANCE_BIBLE,
                "character_id": character_id,
                "name": character.get("name", ""),
                "traits": list(character.get("personality_keywords") or []),
                "signature_expressions": [],
                "source_shots": [],
                "updated_at": None,
            },
        )
        primary = expression["primary"]
        if primary not in b["signature_expressions"]:
            b["signature_expressions"].append(primary)
        if shot_ref not in b["source_shots"]:
            b["source_shots"].append(shot_ref)
        b["updated_at"] = schemas.utc_now_iso()
        return b

    # ------------------------------------------------------------------

    def run(
        self,
        contracts: list[dict[str, Any]],
        *,
        shooting_script: dict[str, Any],
    ) -> PerformanceResult:
        """为所有合约补表演字段并**签发合约**。"""
        characters_by_id = {
            c["character_id"]: c for c in shooting_script.get("characters") or []
        }
        scenes_by_id = {
            s["scene_id"]: s
            for ep in shooting_script.get("episodes") or []
            for s in ep.get("scenes") or []
        }

        acting_beats: dict[str, dict[str, Any]] = {}
        blocking: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        for contract in contracts:
            scene = scenes_by_id.get(contract["scene_id"], {"scene_id": contract["scene_id"]})
            beats_doc = self.plan_acting_beats(contract, characters_by_id)
            acting_beats[contract["shot_id"]] = beats_doc

            if contract["scene_id"] not in blocking:
                blocking[contract["scene_id"]] = self.plan_blocking(
                    scene, list(scene.get("characters") or [])
                )

            # 表演圣经是**工厂级**资产（跨剧累积），而 shot_id 只在本剧内唯一
            # （两部剧都会有 EP001_SC001_SH001），所以引用必须带上 story_id。
            shot_ref = f"{contract['story_id']}/{contract['shot_id']}"
            for beat in beats_doc["beats"]:
                cid = beat["character_id"]
                if cid in characters_by_id:
                    self.update_bible(
                        cid, characters_by_id[cid], beat["micro_expression"], shot_ref
                    )

            contract["performance"] = {
                "acting_beats": beats_doc["beats"],
                "blocking": blocking[contract["scene_id"]],
                "performance_bible_refs": sorted(
                    {b["character_id"] for b in beats_doc["beats"]}
                ),
            }
            if not contract["assets"]["characters"]:
                warnings.append(f"{contract['shot_id']} 没有人物，只能当空镜处理")

        # 表演导演 Agent：跨镜挂语气/插停顿/升强度/补反应（签发前就地精修）
        from .direction import direct_performance
        direction = direct_performance(contracts, characters_by_id)

        for contract in contracts:
            acting_beats[contract["shot_id"]]["beats"] = \
                contract["performance"]["acting_beats"]
            # 表演到位 → 签发合约（此后再改任何字段都会导致拒渲）
            sign_contract(contract)

        used_bibles = {
            cid: b for cid, b in self.bibles.items() if cid in characters_by_id
        }
        return PerformanceResult(
            contracts=contracts,
            acting_beats=acting_beats,
            blocking=blocking,
            bibles=used_bibles,
            warnings=warnings,
        )
