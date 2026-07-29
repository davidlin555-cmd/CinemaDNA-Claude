"""Director Execution Engine（①）—— 把《导演总谱》编译成"可拍摄事实"并硬锁。

复盘定论：**管理层很全，执行层不够硬**——剧本之后缺"可拍摄事实"，渲染仍偏提示词。
本模块补的就是执行层内核：把 DirectorMasterPlan（叙事意图，多为散文）**编译**成每镜
一份机器可校验、可执行、hash 锁定的 **ShotExecutionContract**，并由 BlockingValidator
自检；**无执行合同 → 禁止渲染**（硬门 require_execution_contract）。

对应"六大子系统②Director Execution Engine"的数据结构（先落地①，②③后接）：
  - ActorSlot / ActorSlotRegistry  A/B/C 槽位死锁（slot↔character↔face↔wardrobe↔voice）
  - ActionPredicate                可执行动作谓词（谁 对 谁/什么 做 什么）
  - PropPhysicalState              道具物理态（在谁手/哪平面/是否接触）
  - SpatialBlocking                站位·朝向·空间关系
  - CameraExecutionContract        景别·目的·时长
  - ShotExecutionContract          一镜的全部可拍摄事实
  - DirectorExecutionContract      全片（槽位表 + 各镜合同）+ hash 锁

设计原则：合同只描述**可拍摄事实**，不含情绪散文；下游 Render 只执行合同、不自行补戏。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from director.master_plan import DirectorMasterPlan, ShotPlan

SCHEMA = "director_execution_contract.v1"

#: 指向对手的动作（有 object=对方槽位）
_DIRECTED_VERBS = ("逼近", "前逼", "指向", "指着", "打断", "推", "拉", "对峙",
                   "质问", "摔向", "扑向", "拦住", "拽")
#: 作用于道具的动作（有 object=道具）
_PROP_VERBS = ("掏出", "举", "举起", "撕", "攥", "抓起", "拿", "放下", "翻", "摔",
               "拍", "递", "抽出", "亮出", "拍下", "按")
#: 道具默认所在平面（按动作/状态推断）
_PLANE_BY_VERB = {"掏出": "hand", "举": "hand", "举起": "hand", "撕": "hand",
                  "攥": "hand", "亮出": "hand", "递": "hand", "放下": "table",
                  "拍": "table", "按": "table"}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ActorSlot:
    """A/B/C 槽位死锁：一个槽位 = 一个角色，脸/衣/声三绑定，全片不得改绑。"""
    slot: str                      # "A"/"B"/"C"...
    character_id: str
    face_ref: str                  # 身份锚（金图/参考图键），跨镜同脸
    wardrobe: str                  # 服装锁
    voice_id: str = ""             # 声音锁（可后接 AudioDNA 补）

    def to_dict(self) -> dict[str, Any]:
        return {"slot": self.slot, "character_id": self.character_id,
                "face_ref": self.face_ref, "wardrobe": self.wardrobe,
                "voice_id": self.voice_id}


@dataclass
class ActionPredicate:
    """可执行动作谓词：谁(subject) 做 什么(verb) 作用于 谁/什么(obj)。"""
    subject: str                   # slot
    verb: str                      # 可执行动作短语（掏出/逼近/指向…），非情绪词
    obj: str | None = None         # 对象：另一槽位 或 prop_id 或 None
    obj_kind: str = "none"         # "slot" / "prop" / "none"

    def to_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "verb": self.verb,
                "obj": self.obj, "obj_kind": self.obj_kind}


@dataclass
class PropPhysicalState:
    """道具物理态：谁持有(held_by)、是否接触(contact)、在哪平面(plane)。"""
    prop_id: str
    held_by: str | None            # 持有的槽位；None=在某平面上
    contact: bool
    plane: str                     # hand/table/ground/pocket/wall

    def to_dict(self) -> dict[str, Any]:
        return {"prop_id": self.prop_id, "held_by": self.held_by,
                "contact": self.contact, "plane": self.plane}


@dataclass
class SpatialBlocking:
    """站位·朝向·空间关系：槽位 → {mark, facing, relation}。"""
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"positions": self.positions}


@dataclass
class CameraExecutionContract:
    """景别·目的·时长合同。"""
    shot_size: str
    purpose: str                   # 这镜为什么存在（story_function/intent）
    duration_sec: float
    shot_type: str = ""
    angle: str = ""
    movement: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"shot_size": self.shot_size, "purpose": self.purpose,
                "duration_sec": round(float(self.duration_sec), 2),
                "shot_type": self.shot_type, "angle": self.angle,
                "movement": self.movement}


@dataclass
class ShotExecutionContract:
    """一镜的全部可拍摄事实（无这层，后面全白做）。"""
    shot_id: str
    order: int
    scene_id: str
    in_frame_slots: list[str]
    action_predicates: list[ActionPredicate]
    prop_states: list[PropPhysicalState]
    blocking: SpatialBlocking
    camera: CameraExecutionContract
    dialogue_slot: str | None          # 说话的槽位；无台词=None
    is_prop_insert: bool = False       # 纯道具插入镜（可无人）

    def to_dict(self) -> dict[str, Any]:
        return {"shot_id": self.shot_id, "order": self.order,
                "scene_id": self.scene_id, "in_frame_slots": self.in_frame_slots,
                "action_predicates": [p.to_dict() for p in self.action_predicates],
                "prop_states": [p.to_dict() for p in self.prop_states],
                "blocking": self.blocking.to_dict(), "camera": self.camera.to_dict(),
                "dialogue_slot": self.dialogue_slot,
                "is_prop_insert": self.is_prop_insert}


@dataclass
class DirectorExecutionContract:
    """全片执行合同：槽位死锁表 + 各镜可拍摄事实 + hash 锁。"""
    story_id: str
    slot_registry: dict[str, ActorSlot]      # slot → ActorSlot
    shots: list[ShotExecutionContract]
    plan_hash: str = ""                        # 上游总谱 hash（可追溯）
    contract_hash: str = ""
    validated: bool = False
    schema_version: str = SCHEMA

    def slot_of(self, character_id: str) -> str | None:
        for s, a in self.slot_registry.items():
            if a.character_id == character_id:
                return s
        return None

    def shot(self, shot_id: str) -> ShotExecutionContract | None:
        return next((s for s in self.shots if s.shot_id == shot_id), None)

    def _hashable(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "story_id": self.story_id,
                "plan_hash": self.plan_hash,
                "slot_registry": {s: a.to_dict() for s, a in self.slot_registry.items()},
                "shots": [s.to_dict() for s in self.shots]}

    def compute_hash(self) -> str:
        blob = json.dumps(self._hashable(), ensure_ascii=False, sort_keys=True)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def lock(self) -> "DirectorExecutionContract":
        self.contract_hash = self.compute_hash()
        return self

    def to_dict(self) -> dict[str, Any]:
        d = self._hashable()
        d.update({"contract_hash": self.contract_hash, "validated": self.validated})
        return d


class ExecutionContractError(RuntimeError):
    """无执行合同 / 合同未自检通过 → 硬门拒绝。"""


# ---------------------------------------------------------------------------
# 编译器：DirectorMasterPlan → DirectorExecutionContract
# ---------------------------------------------------------------------------

class ExecutionContractCompiler:
    """把总谱编译成可拍摄事实。承载①的 6 个 Agent 职责（先内聚成方法，②③后拆）。"""

    def compile(self, plan: DirectorMasterPlan) -> DirectorExecutionContract:
        registry = self._build_slot_registry(plan)       # ActorSlotAgent
        char_to_slot = {a.character_id: s for s, a in registry.items()}
        shots = [self._compile_shot(sp, plan, char_to_slot) for sp in plan.shots]
        ec = DirectorExecutionContract(
            story_id=plan.story_id, slot_registry=registry, shots=shots,
            plan_hash=plan.plan_hash)
        return ec

    # -- ActorSlotAgent：A/B/C 死锁 -------------------------------------

    def _build_slot_registry(self, plan: DirectorMasterPlan) -> dict[str, ActorSlot]:
        # 按"首次出场顺序"分配槽位（先说话/先动作者 = A），稳定可复现
        order: list[str] = []
        for sp in plan.shots:
            for cid in self._chars_in_plan_shot(sp):
                if cid and cid not in order:
                    order.append(cid)
        # 补齐 cast_lock 里未出场的角色（次要）
        for cid in plan.cast_lock:
            if cid not in order:
                order.append(cid)
        letters = [chr(ord("A") + i) for i in range(len(order))]
        reg: dict[str, ActorSlot] = {}
        for slot, cid in zip(letters, order):
            info = plan.cast_lock.get(cid) or {}
            reg[slot] = ActorSlot(
                slot=slot, character_id=cid, face_ref=cid,   # face_ref=角色键，下游解析金图/首帧
                wardrobe=str(info.get("appearance") or info.get("wardrobe") or ""),
                voice_id=str(info.get("voice_id") or ""))
        return reg

    # -- 每镜编译 -------------------------------------------------------

    def _compile_shot(self, sp: ShotPlan, plan: DirectorMasterPlan,
                      c2s: dict[str, str]) -> ShotExecutionContract:
        in_chars = self._chars_in_plan_shot(sp)
        in_slots = [c2s[c] for c in in_chars if c in c2s]
        dlg_slot = c2s.get(sp.dialogue_owner) if sp.dialogue_owner else None

        # ActionPredicateAgent：body_action → (subject, verb, obj)
        subj = dlg_slot or (in_slots[0] if in_slots else None)
        other = next((s for s in in_slots if s != subj), None)
        preds: list[ActionPredicate] = []
        prop_id = (sp.prop_usage or {}).get("prop_id")
        for ab in sp.action_beats or []:
            verb = str(ab.get("body_action") or "").strip()
            if not verb or subj is None:
                continue
            if prop_id and any(v in verb for v in _PROP_VERBS):
                preds.append(ActionPredicate(subj, verb, prop_id, "prop"))
            elif other and any(v in verb for v in _DIRECTED_VERBS):
                preds.append(ActionPredicate(subj, verb, other, "slot"))
            else:
                preds.append(ActionPredicate(subj, verb, None, "none"))

        # PropPhysicalAgent：道具在谁手/哪平面/是否接触
        prop_states: list[PropPhysicalState] = []
        if prop_id:
            pu = sp.prop_usage or {}
            contact = bool(pu.get("contact", True))
            verb0 = preds[0].verb if preds else ""
            plane = next((pl for v, pl in _PLANE_BY_VERB.items() if v in verb0),
                         "hand" if contact else "table")
            prop_states.append(PropPhysicalState(
                prop_id=prop_id, held_by=(subj if contact else None),
                contact=contact, plane=plane))

        # BlockingPlanner：从 scene_layout 取站位，落到在场槽位
        blocking = self._blocking_for(sp, plan, in_slots)

        # CameraContractAgent：景别·目的·时长
        cam = sp.camera or {}
        camera = CameraExecutionContract(
            shot_size=str(cam.get("shot_size") or ""),
            purpose=str(cam.get("intent") or sp.story_function or ""),
            duration_sec=float(cam.get("duration_sec") or 0) or 0.0,
            shot_type=str(cam.get("shot_type") or ""),
            angle=str(cam.get("angle") or ""), movement=str(cam.get("movement") or ""))

        is_insert = (not in_slots and bool(prop_id)) or (
            str(cam.get("shot_type") or "") in ("INSERT", "插入") and not sp.dialogue_owner)

        return ShotExecutionContract(
            shot_id=sp.shot_id, order=sp.order, scene_id=sp.scene_id,
            in_frame_slots=in_slots, action_predicates=preds, prop_states=prop_states,
            blocking=blocking, camera=camera, dialogue_slot=dlg_slot,
            is_prop_insert=is_insert)

    # -- 推断本镜在场角色（总谱未显式列出 → 从可得信号推断） -------------

    def _chars_in_plan_shot(self, sp: ShotPlan) -> list[str]:
        chars: list[str] = []
        if sp.dialogue_owner:
            chars.append(sp.dialogue_owner)
        # action_beats 里显式声明的在场角色（若 Director 后续填了 actors 字段）
        for ab in sp.action_beats or []:
            for cid in (ab.get("actors") or []):
                if cid and cid not in chars:
                    chars.append(cid)
        # 显式 in_frame（总谱若带）
        for cid in (getattr(sp, "in_frame", None) or []):
            if cid and cid not in chars:
                chars.append(cid)
        return chars

    def _blocking_for(self, sp: ShotPlan, plan: DirectorMasterPlan,
                      in_slots: list[str]) -> SpatialBlocking:
        scene = next((s for s in plan.scene_layout
                      if s.get("scene_id") == sp.scene_id), {})
        raw = (scene.get("blocking") or {}) if isinstance(scene, dict) else {}
        marks = ["C", "L", "R", "BG"]
        positions: dict[str, dict[str, Any]] = {}
        for i, slot in enumerate(in_slots):
            positions[slot] = {
                "mark": marks[i % len(marks)],
                "facing": ("对手" if len(in_slots) > 1 else "镜头"),
                "relation": raw.get(slot) if isinstance(raw, dict) else "",
            }
        return SpatialBlocking(positions=positions)


# ---------------------------------------------------------------------------
# BlockingValidator：执行合同自检（不过不下发）
# ---------------------------------------------------------------------------

class BlockingValidator:
    """自检每镜可拍摄事实是否自洽；任一硬项不过 → validated=False，禁止下发/渲染。"""

    def validate(self, ec: DirectorExecutionContract) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        slots = set(ec.slot_registry)

        # 槽位死锁：一个槽位只能绑一个角色，一个角色只能占一个槽位
        seen_char: dict[str, str] = {}
        for s, a in ec.slot_registry.items():
            if not a.character_id:
                issues.append(_gi("*", f"槽位 {s} 未绑角色"))
            if a.character_id in seen_char:
                issues.append(_gi("*", f"角色 {a.character_id} 同时占 "
                                       f"{seen_char[a.character_id]}/{s}（槽位冲突）"))
            seen_char[a.character_id] = s
            if not a.face_ref:
                issues.append(_gi("*", f"槽位 {s} 无 face_ref（身份未锁）"))

        for sh in ec.shots:
            sid = sh.shot_id
            # 在场槽位必须存在
            for sl in sh.in_frame_slots:
                if sl not in slots:
                    issues.append(_gi(sid, f"在场槽位 {sl} 不在槽位表"))
            # 非插入镜必须至少 1 个在场角色（杜绝空镜混过）
            if not sh.in_frame_slots and not sh.is_prop_insert:
                issues.append(_gi(sid, "既无在场角色又非道具插入镜（不可拍）"))
            # 说话镜：说话槽位必须在场
            if sh.dialogue_slot and sh.dialogue_slot not in sh.in_frame_slots:
                issues.append(_gi(sid, f"说话槽位 {sh.dialogue_slot} 不在画面"))
            # 动作谓词：主体/对象槽位必须在场；对象是道具须有物理态
            prop_ids = {p.prop_id for p in sh.prop_states}
            for p in sh.action_predicates:
                if p.subject not in sh.in_frame_slots:
                    issues.append(_gi(sid, f"动作主体 {p.subject} 不在画面"))
                if p.obj_kind == "slot" and p.obj not in sh.in_frame_slots:
                    issues.append(_gi(sid, f"动作对象槽位 {p.obj} 不在画面"))
                if p.obj_kind == "prop" and p.obj not in prop_ids:
                    issues.append(_gi(sid, f"动作作用于道具 {p.obj} 但无物理态"))
            # 道具持有者必须在场
            for ps in sh.prop_states:
                if ps.held_by and ps.held_by not in sh.in_frame_slots:
                    issues.append(_gi(sid, f"道具 {ps.prop_id} 持有者 "
                                          f"{ps.held_by} 不在画面"))
            # 相机合同：景别 + 目的 + 正时长
            if not sh.camera.shot_size:
                issues.append(_gi(sid, "相机合同缺景别"))
            if not sh.camera.purpose:
                issues.append(_gi(sid, "相机合同缺目的（这镜为什么存在）"))
            if sh.camera.duration_sec <= 0:
                issues.append(_gi(sid, "相机合同时长 ≤ 0"))
        return issues

    def validate_and_lock(self, ec: DirectorExecutionContract
                          ) -> tuple[bool, list[dict[str, Any]]]:
        issues = self.validate(ec)
        ec.validated = not issues
        if ec.validated:
            ec.lock()
        return ec.validated, issues


def _gi(sid: str, msg: str) -> dict[str, Any]:
    return {"agent": "blocking_validator", "shot_id": sid, "message": msg}


# ---------------------------------------------------------------------------
# 硬门：无执行合同 → 禁止渲染
# ---------------------------------------------------------------------------

def require_execution_contract(ec: DirectorExecutionContract | None,
                               shot_id: str) -> ShotExecutionContract:
    """渲染前硬门：无合同 / 未自检 / 本镜无合同 → 抛错拒渲。返回本镜可拍摄事实。"""
    if ec is None:
        raise ExecutionContractError(f"{shot_id}: 无导演执行合同，禁止渲染（无合同不出片）")
    if not ec.validated:
        raise ExecutionContractError(
            f"{shot_id}: 执行合同未通过 BlockingValidator 自检，禁止渲染")
    if ec.contract_hash and ec.contract_hash != ec.compute_hash():
        raise ExecutionContractError(f"{shot_id}: 执行合同 hash 失配（被篡改），禁止渲染")
    sh = ec.shot(shot_id)
    if sh is None:
        raise ExecutionContractError(f"{shot_id}: 执行合同里没有这一镜，禁止渲染")
    return sh


__all__ = [
    "ActorSlot", "ActionPredicate", "PropPhysicalState", "SpatialBlocking",
    "CameraExecutionContract", "ShotExecutionContract", "DirectorExecutionContract",
    "ExecutionContractCompiler", "BlockingValidator", "require_execution_contract",
    "ExecutionContractError", "SCHEMA",
]
