"""逐级把关溯源报告测试 —— 每步 verdict 汇成一条可追溯记录(0 成本)。"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pipeline import PipelineOrchestrator


def _orc(tmp_path):
    orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260728")
    s = orc.create_story(title="t")
    return orc, s.story_id, s


class TestGateChain:
    def test_all_clear_chain(self, tmp_path):
        orc, sid, s = _orc(tmp_path)
        s.log_event("SCRIPT_GATE3_DECIDED", passed=True)
        s.log_event("DIRECTOR_QA", verdict="PASS")
        s.log_event("EXEC_CONTRACT", validated=True)
        s.log_event("NARRATIVE_QA", verdict="PASS")
        s.log_event("PRERENDER_GATE", status="READY", blockers=0)
        s.log_event("FIRST_FRAME_GATE", verdict="PASS")
        rep = orc.gate_chain_report(sid)
        assert rep["all_clear"] is True
        assert rep["passed"] == 6 and rep["first_block"] is None
        # 溯源落盘
        assert Path(orc._bundles[sid].path_for(
            f"11_review/gate_chain/{sid}.json")).is_file()

    def test_blocks_at_first_bad_step(self, tmp_path):
        orc, sid, s = _orc(tmp_path)
        s.log_event("SCRIPT_GATE3_DECIDED", passed=True)
        s.log_event("DIRECTOR_QA", verdict="REPAIR")     # 导演分镜没过
        rep = orc.gate_chain_report(sid)
        assert rep["first_block"] == "导演分镜"
        assert rep["all_clear"] is False
        steps = {st["step"]: st for st in rep["steps"]}
        assert steps["剧本"]["ok"] is True
        assert steps["导演分镜"]["ok"] is False
        assert steps["首帧素材"]["status"] == "PENDING"   # 没跑到

    def test_first_frame_block_surfaces(self, tmp_path):
        orc, sid, s = _orc(tmp_path)
        s.log_event("SCRIPT_GATE3_DECIDED", passed=True)
        s.log_event("DIRECTOR_QA", verdict="PASS")
        s.log_event("EXEC_CONTRACT", validated=True)
        s.log_event("PRERENDER_GATE", blockers=0)
        s.log_event("FIRST_FRAME_GATE", verdict="BLOCK", failed=3)
        rep = orc.gate_chain_report(sid)
        assert rep["first_block"] == "首帧素材"

    def test_latest_event_wins(self, tmp_path):
        # 自修后重跑：同步骤取最新 verdict(修好了)
        orc, sid, s = _orc(tmp_path)
        s.log_event("DIRECTOR_QA", verdict="REPAIR")
        s.log_event("DIRECTOR_QA", verdict="PASS")       # 重跑通过
        rep = orc.gate_chain_report(sid)
        steps = {st["step"]: st for st in rep["steps"]}
        assert steps["导演分镜"]["ok"] is True
