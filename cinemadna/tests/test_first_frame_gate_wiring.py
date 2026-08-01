"""首帧素材闸**接线**测试 —— 证明主角镜漏锁 PuLID 时真的拦渲(不烧 Kling)+落溯源报告。

用假 backend(带 foundry)+ 桩记录,绕开真图像生成,专测 run_first_frame_gate 的判定/
升级/重生成/落盘/返回值链路(0 成本)。
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.pipeline import PipelineOrchestrator


class _FakeFoundry:
    def __init__(self, anchors):
        self._a = dict(anchors)
    def has_anchor(self, cid):
        return cid in self._a
    def anchor_for(self, cid):
        return self._a.get(cid)


class _FakeBackend:
    model_name = "kling-test"
    def __init__(self, anchors):
        self.foundry = _FakeFoundry(anchors)
        self._ref_used = {}


def _orc_with_story(tmp_path):
    orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260728")
    orc.use_insightface = False           # 只做 tier 检查(无真判官也能抓漏锁)
    s = orc.create_story(title="t")
    return orc, s.story_id


class TestFirstFrameGateWiring:
    def test_missing_pulid_tier_blocks_render(self, tmp_path, monkeypatch):
        orc, sid = _orc_with_story(tmp_path)
        be = _FakeBackend({"linwan": Path("linwan_anchor.png")})
        # 桩:两镜都是主角单人镜,但一个漏成 scene_first_frame(=今天的 bug)
        recs = [
            {"shot_id": "S1", "character_id": "linwan", "single_char": True,
             "has_anchor": True, "tier": "pulid_anchor", "first_frame": "s1.png"},
            {"shot_id": "S2", "character_id": "linwan", "single_char": True,
             "has_anchor": True, "tier": "scene_first_frame", "first_frame": "s2.png"},
        ]
        monkeypatch.setattr(orc, "_first_frame_gate_records",
                            lambda be_, c_, b_: recs)
        monkeypatch.setattr(orc, "contracts_for", lambda _sid: [{"shot_id": "S1"},
                                                                {"shot_id": "S2"}])
        out = orc.run_first_frame_gate(sid, backend=be, max_regen=0)
        assert out["ready"] is False
        assert "S2" in [f["shot_id"] for f in out["report"]["failed"]]
        # 溯源报告落盘
        rep = orc._bundles[sid].path_for(f"11_review/first_frame_gate/{sid}.json")
        assert Path(rep).is_file()

    def test_all_locked_passes(self, tmp_path, monkeypatch):
        orc, sid = _orc_with_story(tmp_path)
        be = _FakeBackend({"linwan": Path("a.png")})
        recs = [{"shot_id": "S1", "character_id": "linwan", "single_char": True,
                 "has_anchor": True, "tier": "pulid_anchor", "first_frame": "s1.png"}]
        monkeypatch.setattr(orc, "_first_frame_gate_records",
                            lambda *a: recs)
        monkeypatch.setattr(orc, "contracts_for", lambda _sid: [{"shot_id": "S1"}])
        out = orc.run_first_frame_gate(sid, backend=be, max_regen=0)
        assert out["ready"] is True

    def test_no_foundry_skips_gracefully(self, tmp_path):
        orc, sid = _orc_with_story(tmp_path)

        class NoFoundry:
            foundry = None
        out = orc.run_first_frame_gate(sid, backend=NoFoundry())
        assert out["ready"] is True and out.get("skipped") == "no_foundry"
