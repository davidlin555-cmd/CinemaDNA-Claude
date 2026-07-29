"""ShotSheetAuthor —— 各 owner 把 A–K 块写进 ShotMasterTaskSheet（每块唯一 owner）。

把 DirectorMasterPlan 的 ShotPlan（LLM 作者化的真实节拍）翻译成一份完整主任务单：
每个 owner 只写自己的块（经 write_block 强制归属），缺字段**从本镜真实数据派生**
（body_action/line/prop/actors/internal_shift/emotion）——**不是默认值填空**；本镜真的
缺该数据时就留空 → validator BLOCKED，把缺口暴露出来而不是假填。

导演/编译器只汇总签发 + 齐套校验（validate + validate_block_ownership），不代写专业块。
"""

from __future__ import annotations

from typing import Any

from director.master_plan import ShotPlan
from director.agents.lighting import LightingAgent
from director.agents.sound import SoundIntentAgent
from director.shot_task_sheet import (
    ShotMasterTaskSheet, write_block, ShotTaskSheetValidator,
    validate_block_ownership,
)

_FN_ZH_TO_EN = {"钩子": "hook", "铺垫": "hook", "施压": "pressure", "决策": "pressure",
                "揭示": "evidence", "反应": "reaction", "反转": "reverse",
                "反击": "reverse", "收束": "cliff"}
_SIZE_ZH_TO_EN = {"全景": "WS", "远景": "WS", "中景": "MS", "中近景": "MCU",
                  "近景": "CU", "特写": "CU", "大特写": "ECU"}
_MOTION_BY_MOVE = {"固定": "low", "缓推": "mid", "推近": "mid", "缓拉": "mid",
                   "手持": "high", "快切": "high", "跟拍": "high"}
_DIRECTED = ("逼近", "前逼", "指向", "指着", "打断", "推", "拉", "对峙", "质问")
_PROP_VERB = ("掏出", "举", "撕", "攥", "亮出", "递", "拿", "翻", "摔", "拍")
_STRESS_KW = ("必须", "今晚", "十二点", "一分不能少", "不还", "警告", "最后", "别想",
              "立刻", "证据", "录了")
_LEGIBLE_PROPS = ("欠条", "单据", "缴费", "账单", "借条", "手机", "短信", "屏")


def _intensity_band(intensity: float) -> str:
    return "low" if intensity < 0.45 else ("mid" if intensity < 0.75 else "high")


class ShotSheetAuthor:
    """把一条 ShotPlan 交给各 owner 写成完整 ShotMasterTaskSheet。"""

    def __init__(self) -> None:
        self.lighting = LightingAgent()
        self.sound = SoundIntentAgent()

    def author(self, sp: ShotPlan, *, char_to_slot: dict[str, str],
               scene_ctx: dict[str, Any] | None = None,
               intensity: float = 0.6) -> ShotMasterTaskSheet:
        scene_ctx = scene_ctx or {}
        fn_en = _FN_ZH_TO_EN.get(sp.story_function, "hook")
        # 在场角色（槽位）——来自 staging 已填的 action_beats[].actors
        actors = []
        for ab in sp.action_beats or []:
            for cid in (ab.get("actors") or []):
                sl = char_to_slot.get(cid)
                if sl and sl not in actors:
                    actors.append(sl)
        speaker = char_to_slot.get(sp.dialogue_owner) if sp.dialogue_owner else None
        other = next((s for s in actors if s != speaker), None)
        speech = bool(sp.line)
        band = _intensity_band(intensity)

        sheet = ShotMasterTaskSheet(
            story_id=scene_ctx.get("story_id", ""), scene_id=sp.scene_id,
            shot_id=sp.shot_id, narrative={}, character={}, action={}, expression={},
            dialogue={}, props={}, scene_art={}, lighting={}, camera={}, sound={},
            acceptance={})

        # A 叙事（owner=showrunner）
        write_block(sheet, "narrative", "showrunner", {
            "story_function": fn_en,
            "entry_state": sp.entry_state, "exit_state": sp.exit_state,
            "cause": scene_ctx.get("cause", ""), "effect": scene_ctx.get("effect", ""),
            "audience_must_understand": self._audience(sp, fn_en, speaker, other)})

        # B 角色（owner=staging）
        write_block(sheet, "character", "staging", {
            "actors_present": actors,
            "speaker": speaker,
            "facing": ("对手" if other else "镜头"),
            "attention_target": (other or (self._prop_id(sp) and "prop") or "镜头"),
            **({"no_actors": True} if not actors else {})})

        # C 动作（owner=action）
        write_block(sheet, "action", "action", {
            "action_predicates": self._predicates(sp, speaker, other, actors),
            "body_beats": [x for x in (sp.internal_shift or "").split("→") if x][:4]
                          or [a.get("body_action", "") for a in sp.action_beats or []][:2],
            "forbidden_actions": self._forbidden(fn_en, speech)})

        # D 表情（owner=expression）
        write_block(sheet, "expression", "expression", {
            "expression_arc": self._expression_arc(sp),
            "intensity": band,
            "trigger": self._trigger(sp, scene_ctx)})

        # E 对白（owner=dialogue）
        if speech:
            write_block(sheet, "dialogue", "dialogue", {
                "speech": True,
                "lines": [{"slot_id": speaker, "text": sp.line,
                           "emotion": sp.emotion or "",
                           "stress": [k for k in _STRESS_KW if k in sp.line]}]})
        else:
            write_block(sheet, "dialogue", "dialogue",
                        {"speech": False, "no_speech": True, "lines": []})

        # F 道具（owner=prop_info）
        write_block(sheet, "props", "prop_info", {"props": self._props(sp, speaker)})

        # G 场景美术（owner=scene）
        write_block(sheet, "scene_art", "scene", {
            "scene_id": sp.scene_id,
            "layout_notes": scene_ctx.get("location", ""),
            "blocking": scene_ctx.get("blocking", "")})

        # H 灯光（owner=lighting）——专用 Agent
        self.lighting.produce(sheet, story_function=fn_en, intensity=intensity,
                              scene_lighting={"mood": scene_ctx.get("mood")})

        # I 摄影（owner=camera）
        cam = sp.camera or {}
        d = float(cam.get("duration_sec") or 3.0)
        write_block(sheet, "camera", "camera", {
            "shot_size": _SIZE_ZH_TO_EN.get(cam.get("shot_size", ""), "MS"),
            "angle": cam.get("angle", ""), "movement": cam.get("movement", "static"),
            "duration_sec_range": [round(max(1.0, d - 1), 1), round(d + 1, 1)],
            "motion_intensity": _MOTION_BY_MOVE.get(cam.get("movement", ""), "low")})

        # J 声音意图（owner=sound）——专用 Agent
        self.sound.produce(
            sheet, story_function=fn_en, speech=speech,
            action_verbs=[a.get("body_action", "") for a in sp.action_beats or []],
            prop_ids=[self._prop_id(sp)] if self._prop_id(sp) else [])

        # K 验收（owner=acceptance）
        write_block(sheet, "acceptance", "acceptance", {
            "must_pass": self._must_pass(sheet, fn_en, speech),
            "commercial_note": self._commercial(fn_en)})

        return sheet

    # -- 汇总签发：validate + ownership 齐套 + 锁 ------------------------

    def author_and_lock(self, sp: ShotPlan, **kw
                        ) -> tuple[ShotMasterTaskSheet, list[dict[str, Any]]]:
        sheet = self.author(sp, **kw)
        issues = (ShotTaskSheetValidator().validate(sheet)
                  + validate_block_ownership(sheet))
        sheet.validated = not issues
        if sheet.validated:
            sheet.lock()
        return sheet, issues

    # -- 各块的接地派生（用本镜真实数据，不猜不默认） --------------------

    def _prop_id(self, sp: ShotPlan) -> str:
        return (sp.prop_usage or {}).get("prop_id") or ""

    def _audience(self, sp, fn_en, speaker, other) -> str:
        who = speaker or (other or "主角")
        if sp.line:
            return f"{who} 说「{sp.line[:14]}」——{fn_en}"
        act = (sp.action_beats or [{}])[0].get("body_action", "")
        if act:
            return f"{who} {act}（{fn_en}）"
        return f"{fn_en}：{sp.entry_state}→{sp.exit_state}"

    def _predicates(self, sp, speaker, other, actors) -> list[dict[str, Any]]:
        # 主体只能是在场槽位；无在场角色（纯道具插入镜）→ 无 actor 谓词（禁默认造槽位）
        subj = speaker or (actors[0] if actors else None)
        if subj is None:
            return []
        prop = self._prop_id(sp)
        out = []
        for ab in sp.action_beats or []:
            verb = str(ab.get("body_action") or "").strip()
            if not verb:
                continue
            if prop and any(v in verb for v in _PROP_VERB):
                out.append({"subject": subj, "verb": verb, "object": prop,
                            "object_kind": "prop"})
            elif other and any(v in verb for v in _DIRECTED):
                out.append({"subject": subj, "verb": verb, "object": other,
                            "object_kind": "slot"})
            else:
                out.append({"subject": subj, "verb": verb, "object": None,
                            "object_kind": "none"})
        return out

    def _forbidden(self, fn_en, speech) -> list[str]:
        f = []
        if fn_en == "reaction":
            f.append("主动发起冲突动作")           # 反应镜只承接不发起
        if not speech:
            f.append("说话口型")                    # 无声镜禁张嘴说话
        if fn_en == "hook":
            f.append("提前泄底")
        return f or ["无"]

    def _expression_arc(self, sp) -> str:
        if sp.internal_shift and "→" in sp.internal_shift:
            return sp.internal_shift
        emo = sp.emotion or "情绪"
        return f"{emo}(起)→{emo}强化(中)→外显(止)"

    def _trigger(self, sp, scene_ctx) -> str:
        return (scene_ctx.get("cause") or sp.entry_state
                or "对方台词/上一镜信息")

    def _props(self, sp, holder) -> list[dict[str, Any]]:
        pid = self._prop_id(sp)
        if not pid:
            return []
        contact = bool((sp.prop_usage or {}).get("contact"))
        legible = "required" if any(k in pid for k in _LEGIBLE_PROPS) else "off"
        return [{"prop_id": pid, "holder_slot": holder, "plane": "hand" if contact else "table",
                 "contact": contact, "state": (sp.prop_usage or {}).get("state", "默认"),
                 "on_screen_text": legible == "required", "legibility": legible}]

    def _must_pass(self, sheet, fn_en, speech) -> list[str]:
        mp = ["identity"]
        if sheet.action.get("action_predicates"):
            mp.append("action_visible")
        if any(p.get("legibility") == "required" for p in sheet.props.get("props") or []):
            mp.append("prop_legible")
        mp.append("lip_sync" if speech else "no_dead_air")
        if fn_en in ("hook", "reverse", "cliff"):
            mp.append("hook")
        return mp

    def _commercial(self, fn_en) -> str:
        return {"hook": "3秒钩子抓人", "pressure": "冲突升级施压",
                "evidence": "关键证据呈现", "reaction": "情绪反应承接",
                "reverse": "反转留人", "cliff": "悬念收尾留钩"}.get(fn_en, "推进剧情")


__all__ = ["ShotSheetAuthor"]
