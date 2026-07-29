"""OptimizerDNA (Phase G) 测试 —— 复盘分析 / 可逆调参 / 自我验收回滚 / 编排接入。

核心铁律：优化本身也要被验收——变差就整档回滚，不能越优化越差。
"""

from __future__ import annotations

from optimizer.analyzer import analyze, is_better, target_metrics
from optimizer.profile import CAP_MAX, CAP_MIN, TuningProfile
from optimizer.service import FactoryOptimizer
from orchestrator.pipeline import PipelineOrchestrator

THEME = "县城女护士深夜被高利贷追债，翻出攥皱的缴费单"


class TestProfile:
    def test_cap_bounded(self):
        p = TuningProfile()
        p.apply(cap_override={"AUDIO_FIX": 99}, watch=[], baseline={})
        assert p.cap_for("AUDIO_FIX", 2) == CAP_MAX     # 夹到硬上界
        p.apply(cap_override={"SCENE_FIX": 0}, watch=[], baseline={})
        assert p.cap_for("SCENE_FIX", 2) == CAP_MIN     # 夹到硬下界

    def test_default_when_unset(self):
        assert TuningProfile().cap_for("PROP_FIX", 2) == 2

    def test_rollback(self):
        p = TuningProfile()
        p.apply(cap_override={"SCENE_FIX": 1}, watch=["SCENE_FIX"], baseline={"x": 1})
        assert p.version == 1 and p.repair_cap_override == {"SCENE_FIX": 1}
        assert p.rollback() is True
        assert p.repair_cap_override == {} and p.version == 0
        assert p.rollback() is False                    # 没有更早的版本


class TestAnalyzer:
    def test_low_recovery_recommends_lower_cap(self):
        tel = {"stories_total": 5, "stories_failed": 2, "repairs": {
            "SCENE_FIX": {"attempts": 6, "recovered": 1, "failed": 5}}}
        recs = analyze(tel)
        cap = [r for r in recs if r.kind == "repair_cap" and r.target == "SCENE_FIX"]
        assert cap and cap[0].auto is True
        assert cap[0].cap_delta["SCENE_FIX"] == 1       # 2 → 1

    def test_hotspot_flagged_not_auto(self):
        tel = {"stories_total": 3, "repairs": {
            "PROP_FIX": {"attempts": 8, "recovered": 6, "failed": 2},
            "AUDIO_FIX": {"attempts": 1, "recovered": 1, "failed": 0}}}
        recs = analyze(tel)
        hot = [r for r in recs if r.kind == "hotspot"]
        assert hot and hot[0].target == "PROP_FIX" and hot[0].auto is False

    def test_human_reject_recommendation(self):
        tel = {"stories_total": 5, "repairs": {}, "human": {
            "identity_review": {"approved": 1, "rejected": 4}}}
        recs = analyze(tel)
        hp = [r for r in recs if r.kind == "human_pref"]
        assert hp and hp[0].target == "identity_review"

    def test_metrics_and_is_better(self):
        before = {"avg_repair_attempts": 3.0, "fail_rate": 0.4}
        assert is_better(before, {"avg_repair_attempts": 2.0, "fail_rate": 0.2})
        assert not is_better(before, {"avg_repair_attempts": 3.5, "fail_rate": 0.4})


class TestSelfValidation:
    def test_apply_then_keep_when_better(self):
        opt = FactoryOptimizer()
        bad = {"stories_total": 4, "stories_failed": 2, "repairs": {
            "SCENE_FIX": {"attempts": 4, "recovered": 0, "failed": 4}}}
        r1 = opt.run(bad)
        assert r1.action == "applied"
        assert opt.profile.repair_cap_override.get("SCENE_FIX") == 1
        # 下一轮指标变好 → 保留
        good = {"stories_total": 4, "stories_failed": 0, "repairs": {
            "SCENE_FIX": {"attempts": 1, "recovered": 1, "failed": 0}}}
        r2 = opt.run(good)
        assert r2.action in ("kept", "applied")
        assert opt.profile.version >= 1

    def test_rollback_when_worse(self):
        opt = FactoryOptimizer()
        base = {"stories_total": 4, "stories_failed": 1, "repairs": {
            "SCENE_FIX": {"attempts": 4, "recovered": 1, "failed": 3}}}
        opt.run(base)                        # 应用一版，记基线
        v = opt.profile.version
        worse = {"stories_total": 4, "stories_failed": 3, "repairs": {
            "SCENE_FIX": {"attempts": 8, "recovered": 1, "failed": 7}}}
        r = opt.run(worse)                   # 指标变差 → 回滚
        assert r.action == "rolled_back"
        assert opt.profile.version < v


class TestOrchestratorIntegration:
    def test_telemetry_and_optimizer(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)
        orc.run_until_quiescent()

        tel = orc.telemetry()
        assert tel["stories_total"] == 1
        assert "repairs" in tel and "human" in tel

        rep = orc.run_optimizer()
        assert "recommendations" in rep and "tuning" in rep
        assert rep["action"] in ("analyzed", "applied", "kept", "rolled_back")

    def test_tuning_cap_affects_repair(self, tmp_path):
        """调参档把某码上限降到 1 → 该码只试一次就升级（真正改变行为）。"""
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        orc.tuning.apply(cap_override={"AUDIO_FIX": 1}, watch=[], baseline={})
        assert orc.tuning.cap_for("AUDIO_FIX", 2) == 1


# ---------------------------------------------------------------------------
# Phase G2：模块/闸门级热点报告 + 人工偏好学习（真实调参）+ 前后对比
# ---------------------------------------------------------------------------

from optimizer.analyzer import hotspot_report as _hotspot


class TestHotspotReport:
    def test_aggregates_by_module_and_gate(self):
        tel = {"stories_total": 6, "stories_failed": 1, "repairs": {
            "SCENE_FIX": {"attempts": 5, "recovered": 3, "failed": 2},
            "AUDIO_FIX": {"attempts": 8, "recovered": 6, "failed": 2},
            "FINAL_REPAIR": {"attempts": 2, "recovered": 0, "failed": 2}},
            "human": {"identity_review": {"approved": 1, "rejected": 4},
                      "final_review_critical": {"approved": 3, "rejected": 1}}}
        rep = _hotspot(tel)
        # 按模块：audio 最忙（8 次）
        assert rep["worst_module"] == "audio"
        mods = {m["module"] for m in rep["top_modules"]}
        assert {"scene", "audio", "final"} <= mods
        # 按闸门：identity_review 驳回率最高
        assert rep["worst_gate"] == "identity_review"
        assert rep["top_gates"][0]["reject_rate"] == 0.8


class TestHumanPrefLearning:
    def test_high_reject_bumps_feeding_caps(self):
        tel = {"stories_total": 5, "stories_failed": 0, "repairs": {},
               "human": {"identity_review": {"approved": 1, "rejected": 4}}}
        recs = analyze(tel)
        hp = [r for r in recs if r.kind == "human_pref"]
        assert hp and hp[0].auto is True                 # 真实可自动应用
        # 喂 identity_review 的码上限被+1（IDENTITY_MISSING 默认1→2, ASSET_REGEN 2→3）
        assert hp[0].cap_delta.get("IDENTITY_MISSING") == 2
        assert hp[0].cap_delta.get("ASSET_REGEN") == 3

    def test_optimizer_applies_human_pref(self):
        opt = FactoryOptimizer()
        tel = {"stories_total": 5, "stories_failed": 0, "repairs": {},
               "human": {"identity_review": {"approved": 1, "rejected": 4}}}
        r = opt.run(tel)
        assert r.action == "applied"
        # 前置多修一次落进调参档（可回滚）
        assert opt.profile.cap_for("IDENTITY_MISSING", 1) == 2

    def test_protected_code_not_failfast_lowered(self):
        # ASSET_REGEN 恢复率低，但它喂 identity_review → 不做 fail-fast 降档
        tel = {"stories_total": 4, "stories_failed": 2, "repairs": {
            "ASSET_REGEN": {"attempts": 6, "recovered": 1, "failed": 5}}}
        caps = [r for r in analyze(tel)
                if r.kind == "repair_cap" and "ASSET_REGEN" in r.cap_delta]
        assert not caps                                  # 未被降档


class TestRejectRateValidation:
    def test_reject_rate_in_metrics(self):
        from optimizer.analyzer import target_metrics
        m = target_metrics({"stories_total": 4, "repairs": {}, "human": {
            "identity_review": {"approved": 2, "rejected": 6}}})
        assert m["human_reject_rate"] == 0.75

    def test_rollback_when_reject_rate_worsens(self):
        opt = FactoryOptimizer()
        good = {"stories_total": 5, "stories_failed": 0, "repairs": {},
                "human": {"identity_review": {"approved": 4, "rejected": 4}}}
        opt.run(good)                       # 应用一版，基线 reject=0.5
        v = opt.profile.version
        worse = {"stories_total": 5, "stories_failed": 0, "repairs": {},
                 "human": {"identity_review": {"approved": 1, "rejected": 7}}}  # 0.875
        r = opt.run(worse)
        assert r.action == "rolled_back"    # 驳回率变差 → 回滚
        assert opt.profile.version < v


class TestBeforeAfter:
    def test_report_has_before_after_and_hotspots(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)
        orc.run_until_quiescent()
        rep = orc.run_optimizer()
        assert "before" in rep and "after" in rep and "hotspots" in rep
        assert "top_modules" in rep["hotspots"] and "top_gates" in rep["hotspots"]
