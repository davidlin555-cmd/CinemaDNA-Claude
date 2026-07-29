"""统一专业审核层 (Phase F) 测试 —— 一致接口 + 各模块适配器 + 编排聚合。"""

from __future__ import annotations

from orchestrator.pipeline import PipelineOrchestrator
from review import registry as rv
from review.result import (
    MODULE_ORDER,
    VERDICT_CONFIRM,
    VERDICT_PASS,
    VERDICT_PENDING,
    VERDICT_REPAIR,
)

THEME = "县城女护士深夜被高利贷追债，翻出攥皱的缴费单"


class TestAdapters:
    def test_script_pass_is_confirm(self):
        r = rv.from_script({"needs_human_review": False, "gate3_issues": []})
        assert r.verdict == VERDICT_CONFIRM
        assert r.human_gate == "script_gate3"    # 剧本终确认

    def test_script_fail_is_repair(self):
        r = rv.from_script({"needs_human_review": True, "gate3_issues": ["缺钩子"]})
        assert r.verdict == VERDICT_REPAIR
        assert r.issues

    def test_missing_report_is_pending(self):
        assert rv.from_audio(None).verdict == VERDICT_PENDING
        assert rv.from_final(None).verdict == VERDICT_PENDING

    def test_asset_hard_block_is_repair(self):
        r = rv.from_asset_gates("identity", [
            {"passed": False, "hard_blocks": ["SINGLE_REAL_PERSON"], "issues": []}])
        assert r.verdict == VERDICT_REPAIR
        assert r.repair_code == "IDENTITY_MISSING"
        assert any(i["severity"] == "critical" for i in r.issues)

    def test_identity_real_face_needs_confirm(self):
        r = rv.from_asset_gates("identity", [
            {"passed": True, "hard_blocks": [], "issues": [],
             "human_review_required": True}])
        assert r.verdict == VERDICT_CONFIRM
        assert r.human_gate == "identity_review"

    def test_scene_pass(self):
        r = rv.from_asset_gates("scene", [
            {"passed": True, "hard_blocks": [], "issues": []}])
        assert r.verdict == VERDICT_PASS

    def test_audio_repair_carries_code(self):
        r = rv.from_audio({"passed": False, "issues": [
            {"code": "VOICE_GENDER_UNSPECIFIED", "message": "x"}]})
        assert r.verdict == VERDICT_REPAIR
        assert r.repair_code == "AUDIO_FIX"

    def test_final_pass_is_confirm(self):
        r = rv.from_final({"verdict": "PASS_STRUCTURAL", "blocking_issues": []})
        assert r.verdict == VERDICT_CONFIRM
        assert r.human_gate == "final_review_critical"


class TestOrchestratorAggregation:
    def test_module_reviews_shape(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)
        reviews = orc.module_reviews(s.story_id)

        assert [r["module"] for r in reviews] == list(MODULE_ORDER)
        by_mod = {r["module"]: r for r in reviews}
        # 剧本已跑 → 非 PENDING；后续模块尚未走到 → PENDING
        assert by_mod["script"]["verdict"] != VERDICT_PENDING
        assert by_mod["render"]["verdict"] == VERDICT_PENDING
        # 每条都带统一字段
        for r in reviews:
            assert set(r) >= {"module", "label", "verdict", "passed",
                              "repair_code", "human_gate", "issues"}

    def test_reviews_after_full_drive(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)
        orc.run_until_quiescent()
        by_mod = {r["module"]: r for r in orc.module_reviews(s.story_id)}
        # 走到成片总审：脸/场景/道具/分镜/表演/渲染/声音都应有结论（非 PENDING）
        for m in ("scene", "identity", "prop", "director", "performance",
                  "render", "audio", "final"):
            assert by_mod[m]["verdict"] != VERDICT_PENDING, m
