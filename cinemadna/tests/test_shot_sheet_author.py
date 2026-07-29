"""ShotSheetAuthor 测试 —— 各 owner 写全 A–K + 缺字段接地派生 + 齐套校验（0 成本）。"""

from __future__ import annotations

from director.master_plan import ShotPlan
from director.shot_sheet_author import ShotSheetAuthor
from director.shot_task_sheet import BLOCK_OWNERS


def _talking_shot() -> ShotPlan:
    return ShotPlan(
        shot_id="EP1_SC1_SH002", order=2, scene_id="EP1_SC1", story_function="施压",
        action_beats=[{"body_action": "上前逼近", "actors": ["linwan", "zhao"]},
                      {"body_action": "掏出欠条", "actors": ["linwan"]}],
        performance_intent="决绝", dialogue_owner="linwan",
        line="这钱我今晚一分不能少你别想赖", camera={"shot_size": "中景",
        "angle": "平视", "movement": "推近", "duration_sec": 3.5},
        prop_usage={"prop_id": "iou_欠条", "contact": True, "state": "完整"},
        entry_state="紧绷", exit_state="爆发", internal_shift="隐忍→紧绷→爆发",
        emotion="决绝")


def _silent_insert() -> ShotPlan:
    return ShotPlan(
        shot_id="EP1_SC1_SH003", order=3, scene_id="EP1_SC1", story_function="揭示",
        action_beats=[{"body_action": "欠条特写", "actors": []}],
        performance_intent="", dialogue_owner=None, line="",
        camera={"shot_size": "大特写", "movement": "固定", "duration_sec": 2.0},
        prop_usage={"prop_id": "iou_欠条", "contact": True, "state": "完整"},
        entry_state="证据未明", exit_state="证据坐实", emotion="冷峻")


_CTX = {"story_id": "s1", "location": "夜市", "blocking": "A左B右",
        "cause": "追债上门", "effect": "反击", "mood": "pressure"}
_SLOTS = {"linwan": "A", "zhao": "B"}


class TestAuthorsAllBlocks:
    def test_all_eleven_blocks_filled_by_correct_owner(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        for block, owner in BLOCK_OWNERS.items():
            assert getattr(sheet, block), f"{block} 空"
            assert sheet.provenance[block] == owner, f"{block} owner 错"

    def test_validates_and_locks(self):
        sheet, issues = ShotSheetAuthor().author_and_lock(
            _talking_shot(), char_to_slot=_SLOTS, scene_ctx=_CTX, intensity=0.85)
        assert sheet.validated, issues
        assert sheet.sheet_hash.startswith("sha256:")


class TestFilledMissingFieldsGrounded:
    def test_audience_must_understand_grounded_in_line(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        amu = sheet.narrative["audience_must_understand"]
        assert "今晚一分不能少" in amu and amu != ""     # 来自本镜真实台词，非默认

    def test_stress_derived_from_line_keywords(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        stress = sheet.dialogue["lines"][0]["stress"]
        assert "今晚" in stress and "一分不能少" in stress and "别想" in stress

    def test_expression_arc_and_trigger_present(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        assert sheet.expression["expression_arc"] == "隐忍→紧绷→爆发"
        assert sheet.expression["trigger"] == "追债上门"   # 来自场因果
        assert sheet.expression["intensity"] == "high"

    def test_forbidden_actions_and_motion_intensity(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        assert sheet.action["forbidden_actions"]           # 非空
        assert sheet.camera["motion_intensity"] == "mid"   # 推近→mid
        assert sheet.camera["shot_size"] == "MS"

    def test_prop_legibility_required_for_bill(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        p = sheet.props["props"][0]
        assert p["legibility"] == "required" and p["holder_slot"] == "A"

    def test_must_pass_includes_lip_sync_for_speaking(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        mp = sheet.acceptance["must_pass"]
        assert "identity" in mp and "lip_sync" in mp and "prop_legible" in mp

    def test_predicates_have_prop_and_slot_objects(self):
        sheet = ShotSheetAuthor().author(_talking_shot(), char_to_slot=_SLOTS,
                                         scene_ctx=_CTX, intensity=0.85)
        preds = sheet.action["action_predicates"]
        kinds = {(p["verb"], p["object_kind"]) for p in preds}
        assert ("上前逼近", "slot") in kinds and ("掏出欠条", "prop") in kinds


class TestSilentInsert:
    def test_silent_shot_no_speech_and_prop_insert(self):
        sheet, issues = ShotSheetAuthor().author_and_lock(
            _silent_insert(), char_to_slot=_SLOTS, scene_ctx=_CTX, intensity=0.5)
        assert sheet.validated, issues
        assert sheet.dialogue["speech"] is False and sheet.dialogue["no_speech"] is True
        assert "说话口型" in sheet.action["forbidden_actions"]  # 无声镜禁张嘴
        assert "no_dead_air" in sheet.acceptance["must_pass"]
        assert sheet.character.get("no_actors") is True        # 纯道具插入镜
