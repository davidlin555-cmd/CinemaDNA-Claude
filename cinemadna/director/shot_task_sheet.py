"""分镜主任务单体系 ShotMasterTaskSheet —— shot_id 为生产原子的确定性任务单（Step1/2）。

铁律：生产原子是**分镜**（非整集/非散文 Prompt）。每镜唯一 shot_id，导演层为每镜出一份
主任务单，内含独立必填**功能块 A–K**。**缺块/缺字段 = BLOCKED**，Agent 禁猜、禁默认值
填空、禁跨块脑补。任务单编译成已有 DirectorExecutionContract 的 ShotExecutionContract，
经 BlockingValidator 自检，`require_shot_task_sheet()` 作渲染前硬门（无有效单禁生成）。

功能块（一镜一份）：
  A narrative  叙事：story_function/entry_state/exit_state/cause/effect/audience_must_understand
  B character  角色：actors_present[slot]/speaker|null/facing/attention_target
  C action     动作：action_predicates/body_beats(有序)/forbidden_actions
  D expression 表情：expression_arc(起→中→止)/intensity/trigger(禁无触发情绪)
  E dialogue   对白：speech(bool)/lines；speech=false 必标 no_speech
  F props      道具：props[{prop_id,holder_slot,plane,contact,state,on_screen_text,legibility}]
  G scene_art  场景美术：scene_id/layout_notes/blocking
  H lighting   灯光：mood/key_rim（继承 scene 或覆盖）
  I camera     摄影：shot_size/angle/movement/duration_sec_range/motion_intensity
  J sound      声音意图：dialogue_priority/sfx_cues/bgm_mood
  K acceptance 验收：must_pass[]/commercial_note
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from director.execution import (
    ActionPredicate, CameraExecutionContract, PropPhysicalState,
    ShotExecutionContract, SpatialBlocking,
)

SCHEMA = "shot_master_task_sheet.v1"

#: A 叙事功能（英文任务单枚举）→ 已有中文 story_function（对齐 execution/master_plan）
STORY_FUNCTIONS_EN = ("hook", "pressure", "evidence", "reaction", "reverse", "cliff")
FN_EN_TO_ZH = {"hook": "钩子", "pressure": "施压", "evidence": "揭示",
               "reaction": "反应", "reverse": "反转", "cliff": "收束"}
_INTENSITY = ("low", "mid", "high")


class ShotTaskSheetError(RuntimeError):
    """无有效分镜任务单 → 硬门拒绝生成。"""


# ---------------------------------------------------------------------------
# 目录约定：.../story_id/scene_id/shot_id/{still,video,audio,qa}/
# ---------------------------------------------------------------------------

_SHOT_KINDS = ("still", "video", "audio", "qa")


def shot_dir(story_id: str, scene_id: str, shot_id: str, kind: str) -> str:
    """一切产物挂 shot_id 的素材目录（bundle 相对，归到 09_downloads 合法子目录下）。"""
    if kind not in _SHOT_KINDS:
        raise ShotTaskSheetError(f"未知素材类型 {kind!r}，须是 {_SHOT_KINDS}")
    return f"09_downloads/shots/{story_id}/{scene_id}/{shot_id}/{kind}"


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class ShotMasterTaskSheet:
    story_id: str
    scene_id: str
    shot_id: str
    narrative: dict[str, Any]        # A
    character: dict[str, Any]        # B
    action: dict[str, Any]           # C
    expression: dict[str, Any]       # D
    dialogue: dict[str, Any]         # E
    props: dict[str, Any]            # F
    scene_art: dict[str, Any]        # G
    lighting: dict[str, Any]         # H
    camera: dict[str, Any]           # I
    sound: dict[str, Any]            # J
    acceptance: dict[str, Any]       # K
    sheet_hash: str = ""
    validated: bool = False
    schema_version: str = SCHEMA
    #: 块名 → 写入它的 owner Agent（Block Ownership：谁写了每块，供越权/齐套校验）
    provenance: dict[str, str] = field(default_factory=dict)

    #: 块名 → 属性名（校验/报错用块名，返工日志能显示失败块名）
    BLOCKS = (("narrative", "A"), ("character", "B"), ("action", "C"),
              ("expression", "D"), ("dialogue", "E"), ("props", "F"),
              ("scene_art", "G"), ("lighting", "H"), ("camera", "I"),
              ("sound", "J"), ("acceptance", "K"))

    def _hashable(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "story_id": self.story_id,
                "scene_id": self.scene_id, "shot_id": self.shot_id,
                **{name: getattr(self, name) for name, _ in self.BLOCKS}}

    def compute_hash(self) -> str:
        blob = json.dumps(self._hashable(), ensure_ascii=False, sort_keys=True)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def lock(self) -> "ShotMasterTaskSheet":
        self.sheet_hash = self.compute_hash()
        return self

    def to_dict(self) -> dict[str, Any]:
        d = self._hashable()
        d.update({"sheet_hash": self.sheet_hash, "validated": self.validated})
        return d

    @property
    def speaks(self) -> bool:
        return bool(self.dialogue.get("speech"))


# ---------------------------------------------------------------------------
# Block Ownership：A–K 每块唯一可写 owner，其它只读；越权写 → 拒（禁跨块脑补）
# ---------------------------------------------------------------------------

#: 块名 → 唯一负责产出它的 owner Agent。导演/编译器只汇总签发，不代写专业块。
BLOCK_OWNERS: dict[str, str] = {
    "narrative": "showrunner",     # A 叙事意图
    "character": "staging",        # B 在场/朝向（场面调度）
    "action": "action",            # C 动作谓词
    "expression": "expression",    # D 表情弧（PerformanceAtom/表演）
    "dialogue": "dialogue",        # E 对白（台词语气）
    "props": "prop_info",          # F 道具
    "scene_art": "scene",          # G 场景美术
    "lighting": "lighting",        # H 灯光（新增 LightingAgent）
    "camera": "camera",            # I 摄影
    "sound": "sound",              # J 声音意图（可选 SoundIntentAgent）
    "acceptance": "acceptance",    # K 验收（DirectorQA 派生）
}


class OwnershipViolation(ShotTaskSheetError):
    """非 owner 试图写别人的块 → 拒（禁跨块脑补）。"""


def write_block(sheet: ShotMasterTaskSheet, block: str, owner: str,
                content: dict[str, Any]) -> ShotMasterTaskSheet:
    """**唯一合法写块入口**：只有该块注册 owner 能写；越权 → OwnershipViolation。
    写入即登记 provenance，供齐套/越权校验。"""
    expected = BLOCK_OWNERS.get(block)
    if expected is None:
        raise OwnershipViolation(f"未知功能块 {block!r}")
    if owner != expected:
        raise OwnershipViolation(
            f"{owner!r} 无权写 {block!r} 块（唯一 owner={expected!r}）——禁跨块脑补")
    setattr(sheet, block, dict(content))
    sheet.provenance[block] = owner
    return sheet


def validate_block_ownership(sheet: ShotMasterTaskSheet) -> list[dict[str, Any]]:
    """齐套 + 归属校验：每块必须非空且由**法定 owner**写入；空块/越权 → BLOCKED。
    （直接构造、未登记 provenance 的旧路径不在此强制——由主 validate() 保证非空。）"""
    issues: list[dict[str, Any]] = []
    for block, owner in BLOCK_OWNERS.items():
        if not getattr(sheet, block):
            issues.append(_b(block, f"块 {block} 为空（BLOCKED，禁默认值填空）"))
            continue
        who = sheet.provenance.get(block)
        if who is not None and who != owner:
            issues.append(_b(block, f"块 {block} 由 {who!r} 写入，非法定 owner {owner!r}"))
    return issues


# ---------------------------------------------------------------------------
# 校验：缺块/缺字段/不一致 → BLOCKED（带块名）
# ---------------------------------------------------------------------------

class ShotTaskSheetValidator:
    """缺必填块/字段、speaker 不在场、hash 篡改等 → BLOCKED，禁生成。禁默认值填空。"""

    def validate(self, sheet: ShotMasterTaskSheet) -> list[dict[str, Any]]:
        I: list[dict[str, Any]] = []

        # 0. ID 强制：无 shot_id/scene_id/story_id → 禁生成
        for k in ("story_id", "scene_id", "shot_id"):
            if not getattr(sheet, k):
                I.append(_b("id", f"缺 {k}（无 shot_id 禁止任何生成）"))

        # 缺块本身 = BLOCKED（空 dict 视为缺）
        for name, letter in ShotMasterTaskSheet.BLOCKS:
            if not getattr(sheet, name):
                I.append(_b(name, f"缺功能块 {letter}.{name}（禁空着让 Agent 猜）"))
        if I:
            return I                                      # 块都没齐，先拦

        a, b, c, d, e = (sheet.narrative, sheet.character, sheet.action,
                         sheet.expression, sheet.dialogue)
        slots = set(b.get("actors_present") or [])

        # A 叙事必填字段
        if a.get("story_function") not in STORY_FUNCTIONS_EN:
            I.append(_b("narrative", f"story_function 非法/缺：{a.get('story_function')!r}"
                                     f"（须 {STORY_FUNCTIONS_EN}）"))
        for f in ("entry_state", "exit_state", "audience_must_understand"):
            if not str(a.get(f) or "").strip():
                I.append(_b("narrative", f"缺 {f}"))

        # B 角色：actors_present 或显式 no_actors（纯道具插入镜）；speaker 须在场
        if not slots and not b.get("no_actors"):
            I.append(_b("character", "actors_present 为空且未标 no_actors（禁猜在场角色）"))
        spk = b.get("speaker")
        if spk is not None and spk not in slots:
            I.append(_b("character", f"speaker {spk!r} 不在 actors_present（说话人必须在场）"))

        # C 动作：谓词主体/对象须在场
        preds = c.get("action_predicates")
        if preds is None:
            I.append(_b("action", "缺 action_predicates（禁用默认表演代替导演块）"))
        for p in preds or []:
            if p.get("subject") not in slots:
                I.append(_b("action", f"动作主体 {p.get('subject')!r} 不在场"))
            obj, kind = p.get("object"), p.get("object_kind", "")
            if kind == "slot" and obj not in slots:
                I.append(_b("action", f"动作对象槽位 {obj!r} 不在场"))

        # D 表情：强度合法 + 禁无触发情绪
        if d.get("intensity") not in _INTENSITY:
            I.append(_b("expression", f"intensity 非法/缺：{d.get('intensity')!r}"))
        if not str(d.get("expression_arc") or "").strip():
            I.append(_b("expression", "缺 expression_arc（起→中→止）"))
        if not str(d.get("trigger") or "").strip():
            I.append(_b("expression", "缺 trigger（禁止无触发情绪）"))

        # E 对白：speech 一致性；speech=false 必标 no_speech；lines slot 须在场
        speech = e.get("speech")
        if speech not in (True, False):
            I.append(_b("dialogue", "缺 speech(true|false)"))
        lines = e.get("lines") or []
        if speech is True:
            if spk is None:
                I.append(_b("dialogue", "speech=true 但 speaker 为空"))
            if not lines:
                I.append(_b("dialogue", "speech=true 但无 lines"))
            for ln in lines:
                if ln.get("slot_id") not in slots:
                    I.append(_b("dialogue", f"台词归属 {ln.get('slot_id')!r} 不在场"))
        elif speech is False:
            if not e.get("no_speech"):
                I.append(_b("dialogue", "speech=false 必须显式标 no_speech"))
            if lines:
                I.append(_b("dialogue", "speech=false 却有 lines（无声镜禁生成说话嘴型）"))

        # F 道具：持有槽位须在场；要求可读时必须标 legibility
        for p in sheet.props.get("props") or []:
            h = p.get("holder_slot")
            if h is not None and h not in slots:
                I.append(_b("props", f"道具 {p.get('prop_id')} 持有者 {h!r} 不在场"))

        # H 灯光：mood 必填；key_rim 须布尔（禁猜灯光）
        lg = sheet.lighting
        if not str(lg.get("mood") or "").strip():
            I.append(_b("lighting", "缺 mood（灯光基调）"))
        if lg.get("key_rim") not in (True, False):
            I.append(_b("lighting", "缺 key_rim(true|false)（是否轮廓分离光）"))

        # I 摄影：景别/运动/时长范围必填
        cam = sheet.camera
        if not cam.get("shot_size"):
            I.append(_b("camera", "缺 shot_size"))
        if not cam.get("movement"):
            I.append(_b("camera", "缺 movement"))
        rng = cam.get("duration_sec_range")
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] <= rng[1]):
            I.append(_b("camera", "duration_sec_range 缺/非法（须 [min,max]）"))

        # J 声音意图：对白优先级 + BGM 情绪必填
        snd = sheet.sound
        if snd.get("dialogue_priority") not in (0, 1):
            I.append(_b("sound", "缺 dialogue_priority(0|1)"))
        if not str(snd.get("bgm_mood") or "").strip():
            I.append(_b("sound", "缺 bgm_mood"))

        # K 验收：must_pass 必填
        if not sheet.acceptance.get("must_pass"):
            I.append(_b("acceptance", "缺 must_pass（验收项）"))

        return I

    def validate_and_lock(self, sheet: ShotMasterTaskSheet
                          ) -> tuple[bool, list[dict[str, Any]]]:
        issues = self.validate(sheet)
        sheet.validated = not issues
        if sheet.validated:
            sheet.lock()
        return sheet.validated, issues


def _b(block: str, msg: str) -> dict[str, Any]:
    return {"agent": "shot_task_sheet", "block": block, "message": msg,
            "verdict": "BLOCKED"}


# ---------------------------------------------------------------------------
# 编译：ShotMasterTaskSheet → ShotExecutionContract（对齐已有执行合同）
# ---------------------------------------------------------------------------

def compile_sheet_to_shot(sheet: ShotMasterTaskSheet, *, order: int = 0
                          ) -> ShotExecutionContract:
    """把主任务单编译成 ShotExecutionContract（A-K → 执行事实）。须先校验通过。"""
    b, c = sheet.character, sheet.action
    slots = list(b.get("actors_present") or [])
    preds = [ActionPredicate(subject=p["subject"], verb=str(p.get("verb", "")),
                             obj=p.get("object"), obj_kind=p.get("object_kind", "none"))
             for p in (c.get("action_predicates") or []) if p.get("subject")]
    prop_states = [PropPhysicalState(
        prop_id=p["prop_id"], held_by=p.get("holder_slot"),
        contact=bool(p.get("contact", False)), plane=str(p.get("plane", "hand")))
        for p in (sheet.props.get("props") or []) if p.get("prop_id")]
    facing = b.get("facing") or ("对手" if len(slots) > 1 else "镜头")
    blocking = SpatialBlocking(positions={
        sl: {"mark": ["C", "L", "R", "BG"][i % 4], "facing": facing,
             "relation": (sheet.scene_art.get("blocking") or "")}
        for i, sl in enumerate(slots)})
    cam = sheet.camera
    rng = cam.get("duration_sec_range") or [3, 5]
    camera = CameraExecutionContract(
        shot_size=str(cam.get("shot_size", "")),
        purpose=str(sheet.narrative.get("audience_must_understand")
                    or sheet.narrative.get("story_function", "")),
        duration_sec=float((rng[0] + rng[1]) / 2),
        shot_type=str(cam.get("shot_type", "")), angle=str(cam.get("angle", "")),
        movement=str(cam.get("movement", "")))
    return ShotExecutionContract(
        shot_id=sheet.shot_id, order=order, scene_id=sheet.scene_id,
        in_frame_slots=slots, action_predicates=preds, prop_states=prop_states,
        blocking=blocking, camera=camera,
        dialogue_slot=(b.get("speaker") if sheet.speaks else None),
        is_prop_insert=(not slots and bool(prop_states)))


# ---------------------------------------------------------------------------
# 硬门：无有效分镜任务单 → 禁止进入中期生成
# ---------------------------------------------------------------------------

def require_shot_task_sheet(sheet: ShotMasterTaskSheet | None,
                            shot_id: str) -> ShotMasterTaskSheet:
    """渲染前硬门：无单/未校验/hash 篡改/shot_id 不符 → 抛错拒生成。"""
    if sheet is None:
        raise ShotTaskSheetError(f"{shot_id}: 无分镜主任务单，禁止生成（无单不出片）")
    if not sheet.validated:
        raise ShotTaskSheetError(f"{shot_id}: 任务单未通过校验（缺块/不一致），禁止生成")
    if sheet.sheet_hash and sheet.sheet_hash != sheet.compute_hash():
        raise ShotTaskSheetError(f"{shot_id}: 任务单 hash 失配（被篡改），禁止生成")
    if sheet.shot_id != shot_id:
        raise ShotTaskSheetError(
            f"shot_id 不符：单是 {sheet.shot_id}，请求 {shot_id}")
    return sheet


__all__ = [
    "ShotMasterTaskSheet", "ShotTaskSheetValidator", "compile_sheet_to_shot",
    "require_shot_task_sheet", "shot_dir", "ShotTaskSheetError",
    "BLOCK_OWNERS", "OwnershipViolation", "write_block", "validate_block_ownership",
    "STORY_FUNCTIONS_EN", "FN_EN_TO_ZH", "SCHEMA",
]
