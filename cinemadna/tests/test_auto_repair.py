"""自动修复层 (Phase D) 测试 —— 映射表 / 人工白名单 / 驱动器自愈 / 多故事并行

验证「除 3 个人工点外，其它 Gate 失败都自动打回重做」这条工厂铁律：
  - 修复码映射（PreRender flag / CV code → 负责智能体动作）
  - 人工闸门白名单（只允许 3 个理由挂人工，杜绝第 4 个）
  - 驱动器把故事自动推到静止（人工点 / 成片待发布 / 终态），无需人点"继续"
  - 一部故事卡住不拖垮其它并行故事
"""

from __future__ import annotations

import pytest

from asset_brain.identity_dna import fusion_mock
from asset_brain.identity_dna.service import IdentityDNAService
from orchestrator import repair as rp
from orchestrator.pipeline import OrchestratorError, PipelineOrchestrator

THEME = "县城女护士深夜被高利贷追债，翻出攥皱的缴费单"


def factory(tmp_path):
    return PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")


# ---------------------------------------------------------------------------
# 映射表
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_prerender_flag_maps_to_agent_code(self):
        report = {
            "flags": {"scene_contract_ready": "BLOCKED",
                      "prop_contract_ready": "READY"},
            "cross_validation": {"issues": []},
        }
        assert rp.codes_from_prerender(report) == {"SCENE_FIX"}

    def test_cross_validation_codes_map(self):
        report = {"flags": {}, "cross_validation": {"issues": [
            {"code": "NO_REAL_PROP"}, {"code": "IDENTITY_DRIFT"}]}}
        codes = rp.codes_from_prerender(report)
        assert codes == {"PROP_FIX", "CONTINUITY"}

    def test_pick_primary_prefers_upstream(self):
        # 身份 > 场景 > 道具：多码并发时先修最上游
        assert rp.pick_primary({"PROP_FIX", "IDENTITY_MISSING"}) == "IDENTITY_MISSING"

    def test_every_code_has_action(self):
        for code in set(rp.CV_CODE_TO_REPAIR.values()) | {
                c for c in rp.FLAG_TO_REPAIR.values() if c not in rp.HUMAN_GATES}:
            assert code in rp.POLICY

    def test_redline_never_auto_bypasses(self):
        """红线动作重试上限为 0，升级到人脸审核（永不自动放行）。"""
        act = rp.POLICY["IDENTITY_REDLINE"]
        assert act.max_attempts == 0
        assert act.escalation == rp.IDENTITY_REVIEW


# ---------------------------------------------------------------------------
# 人工闸门白名单：只允许 3 个理由
# ---------------------------------------------------------------------------


class TestHumanGateWhitelist:
    def test_only_three_human_gates(self):
        assert rp.HUMAN_GATES == {
            "script_gate3", "identity_review", "final_review_critical"}

    def test_illegal_human_hold_rejected(self, tmp_path):
        orc = factory(tmp_path)
        s = orc.create_story(title="X")
        with pytest.raises(OrchestratorError):
            orc._human_hold(s, "some_other_gate")

    def test_legal_human_hold_ok(self, tmp_path):
        orc = factory(tmp_path)
        s = orc.create_story(title="X")
        orc._human_hold(s, rp.IDENTITY_REVIEW, gate_ids=["g1"])
        assert orc.get_story(s.story_id)["hold"]["reason"] == "identity_review"


# ---------------------------------------------------------------------------
# 驱动器：工厂自愈
# ---------------------------------------------------------------------------


class TestDriver:
    def test_run_until_quiescent_drives_mock_to_final_cut(self, tmp_path):
        """一部 mock 剧一路自动推进，最终静止在成片人工审核（点 3），不裸停 BLOCKED。"""
        orc = factory(tmp_path)
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)      # 剧本自动过 → SCRIPT_DONE
        out = orc.run_until_quiescent()

        story = orc.get_story(s.story_id)
        assert story["stage"] == "FINAL_REVIEW"
        assert story["status"] == "WAITING_HUMAN"
        assert story["hold"]["reason"] == "final_review_critical"
        assert out["rounds"] >= 1

    def test_redline_escalates_to_face_review_via_driver(self, tmp_path):
        """红线克隆：驱动器自动重修（永不绕）→ 升级人脸审核；克隆脸绝不入库。"""
        orc = factory(tmp_path)
        s = orc.create_story(title="C")
        orc.run_scriptbrain(s.story_id, THEME)
        # 覆盖 scene_export 里的人物为红线克隆
        exp = orc.scene_export_for(s.story_id)
        exp["characters"] = [{
            "character_id": "char_clone", "name": "克隆脸",
            "generation_plan_override": {
                "fusion_sources": fusion_mock.simulate_single_real_clone_sources()},
        }]
        orc.run_until_quiescent()

        story = orc.get_story(s.story_id)
        assert story["status"] == "WAITING_HUMAN"
        assert story["hold"]["reason"] == "identity_review"
        assert "char_clone" not in orc.store.character_registry

    def test_one_stuck_story_does_not_stop_others(self, tmp_path):
        """一部卡人工审核，另一部继续自动生产 —— 人工闸门不阻塞其它故事。"""
        orc = factory(tmp_path)
        a = orc.create_story(title="A")
        b = orc.create_story(title="B")
        orc.run_scriptbrain(a.story_id, THEME)
        orc.run_scriptbrain(b.story_id, THEME)
        orc.run_until_quiescent()

        # 两部都各自推进到了成片人工审核（都在自愈、互不阻塞）
        for sid in (a.story_id, b.story_id):
            st = orc.get_story(sid)
            assert st["status"] == "WAITING_HUMAN"
            assert st["stage"] == "FINAL_REVIEW"

    def test_real_face_holds_for_face_review_during_drive(self, tmp_path, monkeypatch):
        """真实人脸生成 → 驱动器在人脸审核处停下（点 2），不自动放行入库。"""
        original = IdentityDNAService.mock_multi_face_fusion

        def patched(self, workorder):
            pack = original(self, workorder)
            pack["is_real_image"] = True
            return pack

        monkeypatch.setattr(IdentityDNAService, "mock_multi_face_fusion", patched)
        orc = factory(tmp_path)
        s = orc.create_story(title="F")
        orc.run_scriptbrain(s.story_id, THEME)
        orc.run_until_quiescent()

        story = orc.get_story(s.story_id)
        assert story["status"] == "WAITING_HUMAN"
        assert story["hold"]["reason"] == "identity_review"
        assert story["stage"] == "ASSET_MATCHING"   # 停在人脸审核，未继续
