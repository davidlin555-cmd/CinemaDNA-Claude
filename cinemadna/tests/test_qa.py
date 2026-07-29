"""镜头级 QA + Repair Planner 单元测试 (Phase 4)"""

from __future__ import annotations

import copy

import pytest

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from director.shot_contract import SHOT_CLOSEUP, SHOT_MEDIUM, new_shot_contract
from performance.service import PerformanceDNAService
from qa.agents import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    TARGET_ASSET_MATCHING,
    TARGET_DIRECTING,
    TARGET_PERFORMANCE,
    TARGET_RENDERING,
    continuity_qa,
    performance_qa,
    vision_qa,
)
from qa.repair import VERDICT_HUMAN, VERDICT_PASS, VERDICT_REPAIR, plan_repair
from qa.service import ShotQAService
from render.service import RenderBrainService

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

SCRIPT = {
    "script_id": "s1", "title": "T",
    "characters": [
        {"character_id": "char_a", "name": "林晚",
         "personality_keywords": ["压抑"], "role_type": "主角"},
    ],
    "props": [{"prop_id": "prop_x", "name": "单据", "states_needed": ["完整"]}],
    "episodes": [{"episode_id": "EP001", "scenes": [{
        "scene_id": "EP001_SC001", "episode_id": "EP001", "beat_type": "CONFLICT",
        "spatial_needs": "可走动", "camera_intent": "压迫感",
        "characters": ["char_a"], "props": ["prop_x"],
        "prop_states": {"prop_x": "完整"},
    }]}],
}


def make_contract(shot_id="SH1", order=1, shot_type=SHOT_MEDIUM, dialogue=None,
                  intensity=0.82, prop_state="完整"):
    return new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001", order=order,
        shot_type=shot_type,
        quad=Quad(story_id="drama_0001", bundle_id="bundle_1",
                  task_id=f"task_shot_{order:04d}", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视",
                "intent": "对峙"},
        duration_sec=5.0,
        emotion={"beat": "CONFLICT", "intensity": intensity, "mood": "压抑"},
        scene_asset={"asset_id": "scene_x", "asset_hash": HASH_A,
                     "gate_passed": True, "tags": ["出租屋"]},
        character_assets=[{"character_id": "char_a", "master_pack_id": "cmp_a",
                           "asset_hash": HASH_B, "gate_passed": True}],
        prop_assets=[{"prop_id": "prop_x", "state": prop_state, "asset_id": "p1",
                      "asset_hash": HASH_A, "gate_passed": True}],
        dialogue=dialogue or [],
    )


def rendered(contracts, bundle=None):
    PerformanceDNAService().run(contracts, shooting_script=SCRIPT)
    RenderBrainService(bundle=bundle).render_all(contracts)
    return contracts


# ---------------------------------------------------------------------------
# Vision QA
# ---------------------------------------------------------------------------


class TestVisionQA:
    def test_clean_shot_passes_with_info_note(self):
        cs = rendered([make_contract()])
        rep = vision_qa(cs)
        assert rep["passed"] is True
        # mock 素材必须被记一笔 info，但不算阻塞
        codes = {i["code"] for i in rep["issues"]}
        assert codes == {"NOT_REAL_FOOTAGE"}
        assert all(i["severity"] == SEVERITY_INFO for i in rep["issues"])

    def test_missing_render_result_flagged(self):
        rep = vision_qa([make_contract()])
        assert rep["passed"] is False
        assert rep["issues"][0]["code"] == "NO_RENDER_RESULT"
        assert rep["issues"][0]["target"] == TARGET_RENDERING

    def test_duration_mismatch_flagged(self):
        cs = rendered([make_contract()])
        cs[0]["render_result"]["duration_sec"] = 9.0
        rep = vision_qa(cs)
        assert any(i["code"] == "DURATION_MISMATCH" for i in rep["issues"])

    def test_reference_swap_flagged(self):
        """参考被掉包 = 一致性的根被挖掉，必须查出来。"""
        cs = rendered([make_contract()])
        cs[0]["render_result"]["reference_hashes"]["scene"] = HASH_B
        rep = vision_qa(cs)
        assert any(i["code"] == "REFERENCE_MISMATCH" and i["severity"] == SEVERITY_HIGH
                   for i in rep["issues"])

    def test_missing_media_file_flagged(self, tmp_path):
        bundle = Bundle(tmp_path, "bundle_1").ensure()
        cs = rendered([make_contract()], bundle=bundle)
        r = cs[0]["render_result"]
        r["is_real_media"] = True
        r["media_type"] = "video/mp4"
        bundle.path_for(r["file_relpath"]).unlink()      # 文件没了
        rep = vision_qa(cs, bundle=bundle)
        assert any(i["code"] == "MEDIA_FILE_MISSING" for i in rep["issues"])

    def test_non_media_file_claiming_to_be_footage_flagged(self, tmp_path):
        """文件在，但那是一份 JSON —— 不能算素材。"""
        bundle = Bundle(tmp_path, "bundle_1").ensure()
        cs = rendered([make_contract()], bundle=bundle)
        cs[0]["render_result"]["is_real_media"] = True   # 谎称出片
        rep = vision_qa(cs, bundle=bundle)
        assert any(i["code"] == "MEDIA_TYPE_INVALID" for i in rep["issues"])

    def test_low_motion_quality_is_marked_non_structural(self):
        cs = rendered([make_contract()])
        cs[0]["render_result"]["metrics"]["motion_quality"] = 0.3
        rep = vision_qa(cs)
        issue = next(i for i in rep["issues"] if i["code"] == "LOW_MOTION_QUALITY")
        assert issue["structural"] is False   # 打分是 mock，如实标注


# ---------------------------------------------------------------------------
# Performance QA
# ---------------------------------------------------------------------------


class TestPerformanceQA:
    def test_clean_passes(self):
        cs = rendered([make_contract(dialogue=[{"character_id": "char_a",
                                               "line": "我没有退路了。"}])])
        assert performance_qa(cs)["passed"] is True

    def test_missing_beats_flagged(self):
        c = make_contract()
        c["performance"] = {}
        rep = performance_qa([c])
        assert rep["issues"][0]["code"] == "NO_ACTING_BEATS"
        assert rep["issues"][0]["target"] == TARGET_PERFORMANCE

    def test_dialogue_without_beat_flagged(self):
        cs = rendered([make_contract(dialogue=[{"character_id": "char_a",
                                               "line": "我没有退路了。"}])])
        cs[0]["performance"]["acting_beats"] = [
            b for b in cs[0]["performance"]["acting_beats"] if b["type"] != "LINE"
        ] or [{"beat_index": 0, "type": "ACTION", "character_id": "char_a",
               "micro_expression": {"primary": "x", "physiological": "y"}}]
        rep = performance_qa(cs)
        assert any(i["code"] == "DIALOGUE_WITHOUT_BEAT" for i in rep["issues"])

    def test_closeup_without_expression_flagged(self):
        cs = rendered([make_contract(shot_type=SHOT_CLOSEUP)])
        for b in cs[0]["performance"]["acting_beats"]:
            b["micro_expression"] = {}
        rep = performance_qa(cs)
        assert any(i["code"] == "CLOSEUP_WITHOUT_EXPRESSION" for i in rep["issues"])

    def test_high_emotion_without_physiology_flagged(self):
        cs = rendered([make_contract(intensity=0.95)])
        for b in cs[0]["performance"]["acting_beats"]:
            b["micro_expression"].pop("physiological", None)
        rep = performance_qa(cs)
        assert any(i["code"] == "HIGH_EMOTION_WITHOUT_PHYSIOLOGY"
                   for i in rep["issues"])

    def test_actor_in_frame_without_beat_flagged(self):
        cs = rendered([make_contract()])
        for b in cs[0]["performance"]["acting_beats"]:
            b["character_id"] = "char_ghost"
        rep = performance_qa(cs)
        assert any(i["code"] == "ACTOR_WITHOUT_BEAT" for i in rep["issues"])


# ---------------------------------------------------------------------------
# Continuity QA
# ---------------------------------------------------------------------------


class TestContinuityQA:
    def test_clean_passes(self):
        cs = rendered([make_contract(), make_contract("SH2", 2)])
        assert continuity_qa(cs)["passed"] is True

    def test_ledger_issues_are_inherited(self):
        cs = rendered([make_contract()])
        rep = continuity_qa(cs, continuity_report={
            "identity_issues": ["人物 char_a 用了不同 Master Pack"],
            "scene_issues": [], "prop_issues": [],
        })
        assert rep["passed"] is False
        assert rep["issues"][0]["target"] == TARGET_DIRECTING

    def test_rendered_identity_drift_is_critical(self):
        """产物层面的身份漂移是 critical —— 机器修不了，必须人工。"""
        cs = rendered([make_contract(), make_contract("SH2", 2)])
        cs[1]["render_result"]["reference_hashes"]["characters"]["char_a"] = HASH_A
        rep = continuity_qa(cs)
        issue = next(i for i in rep["issues"] if i["code"] == "RENDERED_IDENTITY_DRIFT")
        assert issue["severity"] == SEVERITY_CRITICAL
        assert issue["target"] == TARGET_ASSET_MATCHING

    def test_prop_state_split_within_scene_flagged(self):
        cs = rendered([make_contract(), make_contract("SH2", 2, prop_state="被撕毁")])
        rep = continuity_qa(cs)
        assert any(i["code"] == "PROP_STATE_SPLIT" for i in rep["issues"])


# ---------------------------------------------------------------------------
# Repair Planner
# ---------------------------------------------------------------------------


class TestRepairPlanner:
    def _issue(self, code, severity, target, shot="SH1"):
        return {"shot_id": shot, "code": code, "message": code,
                "severity": severity, "target": target, "agent": "t",
                "structural": True, "detail": {}}

    def test_no_issues_passes(self):
        plan = plan_repair([], story_id="d1", total_shots=8)
        assert plan["verdict"] == VERDICT_PASS
        assert plan["target_stage"] is None

    def test_info_only_still_passes(self):
        plan = plan_repair(
            [self._issue("NOT_REAL_FOOTAGE", SEVERITY_INFO, TARGET_RENDERING)],
            story_id="d1", total_shots=8,
        )
        assert plan["verdict"] == VERDICT_PASS

    def test_picks_earliest_stage_among_issues(self):
        """同时有表演问题和身份问题 → 只能从更靠前的阶段重来。"""
        plan = plan_repair(
            [
                self._issue("A", SEVERITY_HIGH, TARGET_RENDERING),
                self._issue("B", SEVERITY_HIGH, TARGET_PERFORMANCE),
                self._issue("C", SEVERITY_HIGH, TARGET_DIRECTING),
            ],
            story_id="d1", total_shots=8,
        )
        assert plan["verdict"] == VERDICT_REPAIR
        assert plan["target_stage"] == TARGET_DIRECTING

    def test_minimal_scope_only_affected_shots(self):
        plan = plan_repair(
            [self._issue("A", SEVERITY_HIGH, TARGET_RENDERING, shot="SH3")],
            story_id="d1", total_shots=8,
        )
        assert plan["affected_shots"] == ["SH3"]
        assert plan["shots_to_redo"] == 1      # 不是 8：其余镜头保留复用
        assert plan["scope"] == "SHOTS"

    def test_story_wide_issue_redoes_everything(self):
        plan = plan_repair(
            [self._issue("A", SEVERITY_HIGH, TARGET_DIRECTING, shot="*")],
            story_id="d1", total_shots=8,
        )
        assert plan["scope"] == "STORY" and plan["shots_to_redo"] == 8

    def test_critical_goes_to_human(self):
        plan = plan_repair(
            [self._issue("X", SEVERITY_CRITICAL, TARGET_ASSET_MATCHING)],
            story_id="d1", total_shots=8,
        )
        assert plan["verdict"] == VERDICT_HUMAN
        assert "人工" in plan["reason"]

    def test_root_causes_are_grouped(self):
        plan = plan_repair(
            [self._issue("A", SEVERITY_HIGH, TARGET_RENDERING, shot=f"SH{i}")
             for i in range(3)],
            story_id="d1", total_shots=8,
        )
        assert len(plan["root_causes"]) == 1
        assert plan["root_causes"][0]["count"] == 3


# ---------------------------------------------------------------------------
# QA Service
# ---------------------------------------------------------------------------


class TestQAService:
    def test_clean_run_passes(self):
        cs = rendered([make_contract(), make_contract("SH2", 2)])
        result = ShotQAService().run(cs, story_id="drama_0001")
        assert result.passed is True
        assert result.verdict == VERDICT_PASS
        assert result.global_report["info_count"] == 2      # 两条 mock 素材提示
        assert set(result.artifacts()) == {
            "11_review/qa/drama_0001_vision.json",
            "11_review/qa/drama_0001_performance.json",
            "11_review/qa/drama_0001_continuity.json",
            "11_review/qa/drama_0001_global.json",
            "11_review/qa/drama_0001_repair_plan.json",
        }

    def test_aggregates_all_three_agents(self):
        cs = rendered([make_contract()])
        cs[0]["render_result"]["duration_sec"] = 1.0        # vision
        cs[0]["performance"]["acting_beats"] = []           # performance
        result = ShotQAService().run(
            cs, story_id="d1",
            continuity_report={"identity_issues": ["漂移"], "scene_issues": [],
                               "prop_issues": []},
        )
        assert result.verdict == VERDICT_REPAIR
        assert result.global_report["by_agent"] == {
            "vision": 1, "performance": 1, "continuity": 1
        }
        # 三个模块都出问题 → 从最靠前的 DIRECTING 重来
        assert result.repair_plan["target_stage"] == TARGET_DIRECTING
