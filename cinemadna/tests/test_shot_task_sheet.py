"""分镜主任务单测试 —— 缺块/不一致=BLOCKED、编译、硬门、目录约定（0 成本）。"""

from __future__ import annotations

import pytest

from director.shot_task_sheet import (
    ShotMasterTaskSheet, ShotTaskSheetValidator, compile_sheet_to_shot,
    require_shot_task_sheet, shot_dir, ShotTaskSheetError,
)


def _sheet(**over) -> ShotMasterTaskSheet:
    base = dict(
        story_id="s1", scene_id="EP1_SC1", shot_id="EP1_SC1_SH001",
        narrative={"story_function": "pressure", "entry_state": "平静",
                   "exit_state": "紧张", "cause": "追债", "effect": "反击",
                   "audience_must_understand": "护士被逼到墙角"},
        character={"actors_present": ["A", "B"], "speaker": "A", "facing": "对手"},
        action={"action_predicates": [{"subject": "A", "verb": "上前逼近",
                "object": "B", "object_kind": "slot"}],
                "body_beats": ["看向对方", "上前"], "forbidden_actions": []},
        expression={"expression_arc": "隐忍→爆发→决绝", "intensity": "high",
                    "trigger": "对方羞辱"},
        dialogue={"speech": True, "lines": [{"slot_id": "A", "text": "这钱我不还了",
                  "emotion": "决绝", "stress": ["不还"]}]},
        props={"props": [{"prop_id": "iou", "holder_slot": "A", "plane": "hand",
               "contact": True, "state": "完整"}]},
        scene_art={"scene_id": "EP1_SC1", "layout_notes": "夜市", "blocking": "A左B右"},
        lighting={"mood": "pressure", "key_rim": True},
        camera={"shot_size": "MS", "angle": "平视", "movement": "push",
                "duration_sec_range": [3, 5], "motion_intensity": "mid"},
        sound={"dialogue_priority": 1, "sfx_cues": [], "bgm_mood": "tense"},
        acceptance={"must_pass": ["identity", "action_visible"],
                    "commercial_note": "冲突外显"})
    base.update(over)
    return ShotMasterTaskSheet(**base)


def _blocks(issues):
    return {i["block"] for i in issues}


class TestValidPasses:
    def test_full_sheet_validates_and_locks(self):
        sh = _sheet()
        ok, issues = ShotTaskSheetValidator().validate_and_lock(sh)
        assert ok, issues
        assert sh.validated and sh.sheet_hash.startswith("sha256:")

    def test_silent_shot_with_no_speech_ok(self):
        sh = _sheet(dialogue={"speech": False, "no_speech": True, "lines": []},
                    character={"actors_present": ["A"], "speaker": None},
                    action={"action_predicates": [{"subject": "A", "verb": "攥皱单据",
                            "object": "iou", "object_kind": "prop"}],
                            "body_beats": ["低头"], "forbidden_actions": []})
        ok, issues = ShotTaskSheetValidator().validate_and_lock(sh)
        assert ok, issues


class TestBlockedRejections:
    def test_missing_action_block_blocked(self):
        issues = ShotTaskSheetValidator().validate(_sheet(action={}))
        assert "action" in _blocks(issues)
        assert all(i["verdict"] == "BLOCKED" for i in issues)

    def test_missing_expression_block_blocked(self):
        issues = ShotTaskSheetValidator().validate(_sheet(expression={}))
        assert "expression" in _blocks(issues)

    def test_speaker_not_present_blocked(self):
        sh = _sheet(character={"actors_present": ["A"], "speaker": "B"})  # B 不在场
        issues = ShotTaskSheetValidator().validate(sh)
        assert any("说话人必须在场" in i["message"] for i in issues)

    def test_speech_false_without_marker_blocked(self):
        sh = _sheet(dialogue={"speech": False, "lines": []})   # 没标 no_speech
        issues = ShotTaskSheetValidator().validate(sh)
        assert any("no_speech" in i["message"] for i in issues)

    def test_untriggered_emotion_blocked(self):
        sh = _sheet(expression={"expression_arc": "怒", "intensity": "high",
                                "trigger": ""})
        issues = ShotTaskSheetValidator().validate(sh)
        assert any("无触发情绪" in i["message"] for i in issues)

    def test_camera_missing_duration_range_blocked(self):
        cam = {"shot_size": "MS", "movement": "push"}          # 缺 duration_sec_range
        issues = ShotTaskSheetValidator().validate(_sheet(camera=cam))
        assert "camera" in _blocks(issues)

    def test_no_shot_id_blocked(self):
        issues = ShotTaskSheetValidator().validate(_sheet(shot_id=""))
        assert any(i["block"] == "id" for i in issues)

    def test_action_subject_offscreen_blocked(self):
        sh = _sheet(action={"action_predicates": [{"subject": "C", "verb": "x"}],
                            "body_beats": [], "forbidden_actions": []})
        issues = ShotTaskSheetValidator().validate(sh)
        assert any("动作主体" in i["message"] for i in issues)


class TestCompile:
    def test_compiles_to_execution_facts(self):
        sh = _sheet()
        ShotTaskSheetValidator().validate_and_lock(sh)
        ec = compile_sheet_to_shot(sh, order=1)
        assert ec.in_frame_slots == ["A", "B"] and ec.dialogue_slot == "A"
        assert ec.action_predicates[0].verb == "上前逼近"
        assert ec.prop_states[0].prop_id == "iou" and ec.prop_states[0].held_by == "A"
        assert 3 <= ec.camera.duration_sec <= 5


class TestHardGate:
    def test_no_sheet_blocks(self):
        with pytest.raises(ShotTaskSheetError):
            require_shot_task_sheet(None, "EP1_SC1_SH001")

    def test_unvalidated_blocks(self):
        with pytest.raises(ShotTaskSheetError):
            require_shot_task_sheet(_sheet(), "EP1_SC1_SH001")   # 未 validate

    def test_tampered_hash_blocks(self):
        sh = _sheet()
        ShotTaskSheetValidator().validate_and_lock(sh)
        sh.camera["shot_size"] = "ECU"                          # 锁后篡改
        with pytest.raises(ShotTaskSheetError):
            require_shot_task_sheet(sh, "EP1_SC1_SH001")

    def test_shot_id_mismatch_blocks(self):
        sh = _sheet()
        ShotTaskSheetValidator().validate_and_lock(sh)
        with pytest.raises(ShotTaskSheetError):
            require_shot_task_sheet(sh, "OTHER_SHOT")

    def test_valid_sheet_passes_gate(self):
        sh = _sheet()
        ShotTaskSheetValidator().validate_and_lock(sh)
        assert require_shot_task_sheet(sh, "EP1_SC1_SH001") is sh


class TestRenderGateWiring:
    """渲染入口硬门：登记任务单后，缺/无效单 → 拦渲；未登记 → 豁免。"""

    def _orc(self, tmp_path):
        from orchestrator.pipeline import PipelineOrchestrator
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260726")
        s = orc.create_story(title="t")
        orc._contracts[s.story_id] = [{"shot_id": "EP1_SC1_SH001"}]
        return orc, s.story_id

    def test_unregistered_is_exempt(self, tmp_path):
        orc, sid = self._orc(tmp_path)
        assert orc._require_shot_sheets_for_render(sid) is True   # 未启用 → 放行

    def test_registered_but_missing_sheet_blocks(self, tmp_path):
        orc, sid = self._orc(tmp_path)
        orc.register_shot_sheets(sid, {"OTHER": _sheet()})        # 无本镜单
        assert orc._require_shot_sheets_for_render(sid) is False

    def test_registered_valid_sheet_passes(self, tmp_path):
        orc, sid = self._orc(tmp_path)
        sh = _sheet()
        ShotTaskSheetValidator().validate_and_lock(sh)
        orc.register_shot_sheets(sid, {"EP1_SC1_SH001": sh})
        assert orc._require_shot_sheets_for_render(sid) is True

    def test_registered_unvalidated_sheet_blocks(self, tmp_path):
        orc, sid = self._orc(tmp_path)
        orc.register_shot_sheets(sid, {"EP1_SC1_SH001": _sheet()})  # 未 validate
        assert orc._require_shot_sheets_for_render(sid) is False


class TestDirectoryConvention:
    def test_shot_dir_carries_all_ids(self):
        p = shot_dir("s1", "EP1_SC1", "EP1_SC1_SH001", "video")
        assert "s1" in p and "EP1_SC1" in p and "EP1_SC1_SH001" in p and p.endswith("video")

    def test_bad_kind_rejected(self):
        with pytest.raises(ShotTaskSheetError):
            shot_dir("s1", "sc", "sh", "hologram")
