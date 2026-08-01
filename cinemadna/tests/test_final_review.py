"""成片级 Final Review + QA Council 测试 (Phase 6)

覆盖：5 维检查、复用现有报告、两 tier（确定性硬门 / PENDING 判断层）、
QA Council 裁决（REJECTED/HUMAN/PASS_STRUCTURAL/PASS_FULL）、口型铁律、
以及整条管线接入后的状态机行为。
"""

from __future__ import annotations

import pytest

from director.shot_contract import (
    MOUTH_PHONE_SCREEN,
    MOUTH_REACTION,
    MOUTH_SILENT_CLOSED,
    MOUTH_SPEAKING_LIPSYNC,
    SHOT_CLOSEUP,
    SHOT_INSERT,
    SHOT_MEDIUM,
    SHOT_REACTION,
    check_mouth_policy,
    new_shot_contract,
)
from final_review.dimensions import (
    dim_audiovisual,
    dim_commercial,
    dim_direction,
    dim_narrative,
)
from final_review.service import (
    VERDICT_HUMAN,
    VERDICT_PASS_FULL,
    VERDICT_PASS_STRUCTURAL,
    VERDICT_REJECTED,
    FinalReviewService,
)
from asset_brain.common.quad import Quad

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def contract(shot_id="SH1", order=1, shot_type=SHOT_MEDIUM, beat="CONFLICT",
             dialogue=None, mouth=None, rendered=True, generated=False,
             real_media=True, identity=0.9, resolution="1080x1920", fps=24):
    c = new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001", order=order,
        shot_type=shot_type,
        quad=Quad(story_id="drama_0001", bundle_id="b1",
                  task_id=f"t{order}", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "x"},
        duration_sec=5.0, emotion={"beat": beat, "intensity": 0.7, "mood": "压抑"},
        scene_asset={"asset_id": "sc", "asset_hash": HASH_A, "gate_passed": True, "tags": []},
        character_assets=[{"character_id": "char_a", "master_pack_id": "cmp_a",
                           "asset_hash": HASH_B, "gate_passed": True}],
        prop_assets=[], dialogue=dialogue or [], mouth_policy=mouth,
    )
    if rendered:
        c["render_result"] = {
            "is_real_media": real_media, "is_generated_footage": generated,
            "resolution": resolution, "fps": fps,
            "metrics": {"identity_consistency": identity, "scene_consistency": 0.9},
        }
    return c


def _scene(sid, beat):
    return {"scene_id": sid, "episode_id": "EP001", "beat_type": beat,
            "beat_summary": "x", "location_description": "出租屋", "time_of_day": "夜",
            "mood": "压抑", "required_atoms": ["室内布局", "光线"],
            "spatial_needs": "可走动", "characters": ["char_a"], "props": [],
            "prop_states": {}}


SCRIPT = {
    "script_id": "s1", "title": "T",
    "episodes": [{"episode_id": "EP001", "scenes": [
        _scene("EP001_SC001", "HOOK"),
        _scene("EP001_SC002", "CONFLICT"),
        _scene("EP001_SC003", "CLIFFHANGER"),
    ]}],
    "characters": [{"character_id": "char_a", "name": "林晚",
                    "personality_keywords": ["压抑"], "role_type": "主角",
                    "appears_in": ["EP001_SC001", "EP001_SC002", "EP001_SC003"]}],
    "props": [],
}


# ---------------------------------------------------------------------------
# 口型铁律
# ---------------------------------------------------------------------------


class TestMouthPolicy:
    def test_default_assignment(self):
        assert contract(shot_type=SHOT_INSERT)["mouth_policy"] == MOUTH_PHONE_SCREEN
        assert contract(shot_type=SHOT_REACTION)["mouth_policy"] == MOUTH_REACTION
        assert contract(dialogue=[{"character_id": "char_a", "line": "hi"}]
                        )["mouth_policy"] == MOUTH_SPEAKING_LIPSYNC
        assert contract()["mouth_policy"] == MOUTH_SILENT_CLOSED  # 无台词、中景

    def test_dialogue_but_silent_closed_is_violation(self):
        c = contract(dialogue=[{"character_id": "char_a", "line": "我没退路了"}],
                     mouth=MOUTH_SILENT_CLOSED)
        assert check_mouth_policy(c)   # 有台词却嘴闭合

    def test_no_dialogue_but_lipsync_is_violation(self):
        c = contract(mouth=MOUTH_SPEAKING_LIPSYNC)  # 无台词却做口型
        assert any("无声嘴动" in m for m in check_mouth_policy(c))

    def test_dialogue_with_phone_or_back_is_ok(self):
        c = contract(dialogue=[{"character_id": "char_a", "line": "x"}],
                     mouth=MOUTH_PHONE_SCREEN)
        assert check_mouth_policy(c) == []      # 声音在、口型不强求

    def test_reaction_shot_ok(self):
        assert check_mouth_policy(contract(shot_type=SHOT_REACTION)) == []


# ---------------------------------------------------------------------------
# 各维度
# ---------------------------------------------------------------------------


class TestDimensions:
    def _ctx(self, **kw):
        base = {"contracts": [], "shooting_script": SCRIPT, "continuity_report": {},
                "consistency_report": {}, "shot_qa": {}, "rough_cut": {},
                "safety_violations": []}
        base.update(kw)
        return base

    def test_narrative_missing_beats_flagged(self):
        bad = {**SCRIPT, "episodes": [{"episode_id": "EP001", "scenes": [
            {"scene_id": "s", "beat_type": "HOOK"}]}]}
        d = dim_narrative(self._ctx(shooting_script=bad))
        assert any(i["code"] == "MISSING_BEATS" for i in d["issues"])

    def test_narrative_has_pending_judgment(self):
        d = dim_narrative(self._ctx())
        assert d["pending"]     # 对白自然度等判断层未接入

    def test_direction_monotone_shots_flagged(self):
        cs = [contract(f"SH{i}", i, SHOT_MEDIUM) for i in range(1, 4)]
        d = dim_direction(self._ctx(contracts=cs, rough_cut={"total_duration_sec": 15}))
        assert any(i["code"] == "MONOTONE_SHOTS" for i in d["issues"])

    def test_direction_variety_passes(self):
        cs = [contract("SH1", 1, SHOT_CLOSEUP, beat="HOOK"),
              contract("SH2", 2, SHOT_MEDIUM),
              contract("SH3", 3, SHOT_REACTION, beat="CLIFFHANGER")]
        d = dim_direction(self._ctx(contracts=cs, rough_cut={"total_duration_sec": 40}))
        assert not any(i["code"] == "MONOTONE_SHOTS" for i in d["issues"])

    def test_consistency_reuses_continuity_report(self):
        from final_review.dimensions import dim_performance_consistency
        d = dim_performance_consistency(self._ctx(
            continuity_report={"identity_issues": ["女主换脸"],
                               "scene_issues": [], "prop_issues": []}))
        assert any(i["code"] == "IDENTITY_DRIFT" and i["severity"] == "critical"
                   for i in d["issues"])

    def test_audiovisual_mouth_and_spec(self):
        cs = [contract("SH1", 1, dialogue=[{"character_id": "char_a", "line": "x"}],
                       mouth=MOUTH_SILENT_CLOSED, resolution="1080x1920"),
              contract("SH2", 2, resolution="720x1280")]  # 分辨率不一致
        d = dim_audiovisual(self._ctx(contracts=cs))
        codes = {i["code"] for i in d["issues"]}
        assert "MOUTH_POLICY_VIOLATION" in codes
        assert "SPEC_MISMATCH" in codes

    def test_commercial_mock_no_media_is_hard_block(self):
        cs = [contract("SH1", 1, beat="HOOK", real_media=False, generated=False)]
        d = dim_commercial(self._ctx(contracts=cs))
        assert any(i["code"] == "NO_MEDIA" and i["severity"] == "high"
                   for i in d["issues"])

    def test_commercial_placeholder_is_pending_not_block(self):
        cs = [contract("SH1", 1, beat="HOOK", real_media=True, generated=False)]
        d = dim_commercial(self._ctx(contracts=cs))
        assert not any(i["code"] == "NO_MEDIA" for i in d["issues"])
        assert any("真实生成画面" in p["message"] for p in d["pending"])

    def test_commercial_safety_violation_is_critical(self):
        d = dim_commercial(self._ctx(
            contracts=[contract("SH1", 1, beat="HOOK", generated=True)],
            safety_violations=["wo_x: 红线 single_real_person_detected"]))
        assert any(i["code"] == "SAFETY_VIOLATION" and i["severity"] == "critical"
                   for i in d["issues"])


# ---------------------------------------------------------------------------
# QA Council 裁决
# ---------------------------------------------------------------------------


class TestQACouncil:
    def _ctx(self, contracts, **kw):
        base = {"contracts": contracts, "shooting_script": SCRIPT,
                "continuity_report": {}, "consistency_report": {}, "shot_qa": {},
                "rough_cut": {"total_duration_sec": 40, "missing_shots": []},
                "safety_violations": []}
        base.update(kw)
        return base

    def _clean_generated(self):
        return [contract("SH1", 1, SHOT_CLOSEUP, "HOOK", generated=True),
                contract("SH2", 2, SHOT_MEDIUM, "CONFLICT", generated=True),
                contract("SH3", 3, SHOT_REACTION, "CLIFFHANGER", generated=True)]

    def test_mock_media_rejected(self):
        cs = [contract(f"SH{i}", i, generated=False, real_media=False)
              for i in range(1, 4)]
        cs[0]["emotion"]["beat"] = "HOOK"; cs[-1]["emotion"]["beat"] = "CLIFFHANGER"
        r = FinalReviewService().run(self._ctx(cs), story_id="d1")
        assert r.verdict == VERDICT_REJECTED
        assert r.commercial_ready is False
        assert r.repair_plan["target_stage"]

    def test_placeholder_is_pass_structural_not_full(self):
        """占位素材：结构硬门全过，但判断层 PENDING → 结构达标、非商业可发布。"""
        cs = self._clean_generated()
        for c in cs:
            c["render_result"]["is_generated_footage"] = False  # 占位
        r = FinalReviewService().run(self._ctx(cs), story_id="d1")
        assert r.verdict == VERDICT_PASS_STRUCTURAL
        assert r.structural_passed is True
        assert r.commercial_ready is False
        assert r.pending      # 判断层未接入

    def test_generated_still_pass_structural_until_judgment_tier(self):
        """真实生成画面也只到 PASS_STRUCTURAL——多模态判断层没接入前不给 PASS_FULL。"""
        r = FinalReviewService().run(self._ctx(self._clean_generated()), story_id="d1")
        assert r.verdict == VERDICT_PASS_STRUCTURAL
        assert r.commercial_ready is False

    def test_critical_goes_to_human(self):
        cs = self._clean_generated()
        r = FinalReviewService().run(
            self._ctx(cs, continuity_report={
                "identity_issues": ["女主换脸"], "scene_issues": [], "prop_issues": []}),
            story_id="d1")
        assert r.verdict == VERDICT_HUMAN

    def test_report_shape(self):
        r = FinalReviewService().run(self._ctx(self._clean_generated()), story_id="d1")
        rep = r.report()
        assert rep["schema_version"] == "cinemadna.final_review.v1"
        assert set(d["dimension"] for d in rep["dimensions"]) == {
            "A_narrative", "B_direction", "C_performance_consistency",
            "D_audiovisual", "E_commercial"}
        assert rep["commercial_ready"] is False


# ---------------------------------------------------------------------------
# 整条管线接入
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def _run_to_post(self, orc, sid):
        orc.run_scriptbrain(sid, "县城女护士被高利贷追债，最后逆袭翻身")
        orc.dispatch_assets(sid)
        orc.run_director(sid); orc.run_performance(sid)
        orc.run_render(sid); orc.run_qa(sid)
        orc.assemble_rough_cut(sid)
        orc.advance(sid, "POST")

    def test_final_review_stage_and_block_on_mock(self, tmp_path):
        from orchestrator import PipelineOrchestrator
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        self._run_to_post(orc, s.story_id)
        res = orc.run_final_review(s.story_id)

        assert res["verdict"] == VERDICT_REJECTED     # mock 无真实素材
        story = orc.get_story(s.story_id)
        assert story["stage"] == "FINAL_REVIEW"
        # 成片总审驳回 → 自修复态（FINAL_REPAIR 按维度精准打回），不停厂
        assert story["status"] == "AUTO_REPAIRING"
        assert story["repair_directive"]["code"] == "FINAL_REPAIR"
        # 报告落 Bundle
        assert (orc.bundle_for(s.story_id).root
                / f"11_review/final_review/{s.story_id}.json").exists()

    def test_final_review_reuses_existing_reports(self, tmp_path):
        """确认 Final Review 是复用现有报告，不是重算。"""
        from orchestrator import PipelineOrchestrator
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        self._run_to_post(orc, s.story_id)
        ctx = orc._final_review_ctx(s.story_id)
        # 这些都来自之前阶段已产出的报告
        assert ctx["continuity_report"]       # Director 的连续性报告
        assert ctx["consistency_report"]      # Render 的一致性报告
        assert ctx["shot_qa"]                 # 镜头级 QA 结果
        assert ctx["rough_cut"]               # 粗剪
        assert ctx["contracts"]               # 合约（含 render_result）
