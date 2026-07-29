"""③ Vision QA 熔断测试 —— 坏镜隔离/单镜重跑/禁止进 Post（0 成本，假判后端）。"""

from __future__ import annotations

from pathlib import Path

from render.vision_quarantine import VisionQuarantine, QuarantineResult


class FakeRes:
    def __init__(self, hard_broken=False):
        # 硬崩坏才熔断：这里用 hand_face_broken 模拟"坏镜"
        self.fake = hard_broken
        self.reasons = []
        self.checks = {"static": False, "blank": False}
        self.deep = {"hand_face_broken": hard_broken, "face_swap": False,
                     "ai_fake": False, "garbled_text": False, "stiff": False}


class FakeJudge:
    """按 shot_id → 硬崩坏 映射的假视觉判。"""
    def __init__(self, bad_ids):
        self.bad = set(bad_ids)
    def available(self):
        return True
    def judge(self, video: Path):
        return FakeRes(Path(video).stem in self.bad)


def _rendered(*ids):
    return [{"shot_id": i, "file_relpath": f"{i}.mp4",
             "is_generated_footage": True} for i in ids]


class TestQuarantine:
    def test_bad_shot_is_quarantined(self):
        q = VisionQuarantine(judge=FakeJudge({"S2"}))
        res = q.screen(_rendered("S1", "S2", "S3"), bundle=_FakeBundle())
        assert res.passed == ["S1", "S3"]
        assert res.quarantined_ids == ["S2"]
        assert not res.all_passed

    def test_all_good_passes(self):
        q = VisionQuarantine(judge=FakeJudge(set()))
        res = q.screen(_rendered("S1", "S2"), bundle=_FakeBundle())
        assert res.all_passed and res.passed == ["S1", "S2"]

    def test_extra_check_can_quarantine(self):
        # 叠加身份/功能/乱码判据（返回 reasons → 坏镜）
        q = VisionQuarantine(judge=FakeJudge(set()),
                             extra_check=lambda r: ["身份漂移"] if r["shot_id"] == "S1" else [])
        res = q.screen(_rendered("S1", "S2"), bundle=_FakeBundle())
        assert res.quarantined_ids == ["S1"] and res.passed == ["S2"]

    def test_plastic_feel_does_not_quarantine(self):
        # 塑料感(ai_fake)/僵硬(stiff)是整片质量天花板，**不逐镜熔断**
        class PlasticRes:
            fake = True
            checks = {"static": False, "blank": False}
            deep = {"ai_fake": True, "stiff": True, "hand_face_broken": False,
                    "face_swap": False, "garbled_text": False}
        class PlasticJudge:
            def available(self): return True
            def judge(self, v): return PlasticRes()
        q = VisionQuarantine(judge=PlasticJudge())
        res = q.screen(_rendered("S1", "S2"), bundle=_FakeBundle())
        assert res.all_passed                          # 塑料感不踢，整片保住

    def test_placeholder_footage_not_judged(self):
        # 非真实素材（占位/mock）不做像素判，避免误杀 CI
        q = VisionQuarantine(judge=FakeJudge({"S1"}))
        rendered = [{"shot_id": "S1", "file_relpath": "S1.mp4",
                     "is_generated_footage": False}]
        res = q.screen(rendered, bundle=_FakeBundle())
        assert res.all_passed


class TestReroll:
    def test_reroll_recovers_bad_shot(self):
        judge = FakeJudge({"S2"})
        q = VisionQuarantine(judge=judge)
        # 重跑器：把 S2 换成一个"好"记录（换 shot 文件名，绕过 bad 集）
        def reroll(sid, attempt):
            judge.bad.discard(sid)                 # 换种子后这次过了
            return {"shot_id": sid, "file_relpath": f"{sid}.mp4",
                    "is_generated_footage": True}
        res = q.screen_with_reroll(_rendered("S1", "S2"), bundle=_FakeBundle(),
                                   reroll=reroll, max_rerolls=1)
        assert "S2" in res.rerolled and "S2" in res.passed and res.all_passed

    def test_reroll_exhausted_stays_quarantined(self):
        q = VisionQuarantine(judge=FakeJudge({"S2"}))
        res = q.screen_with_reroll(
            _rendered("S1", "S2"), bundle=_FakeBundle(),
            reroll=lambda sid, a: {"shot_id": sid, "file_relpath": f"{sid}.mp4",
                                   "is_generated_footage": True},
            max_rerolls=2)
        assert res.quarantined_ids == ["S2"]       # 重跑仍坏 → 保持隔离


class TestAssemblyExclusion:
    def test_assembly_excludes_quarantined(self):
        from render.assembly import assemble_rough_cut

        def _rr(fn):
            return {"file_relpath": fn, "media_type": "video", "is_real_media": True,
                    "is_generated_footage": True, "model": "kling", "backend": "kling",
                    "asset_hash": "sha256:x", "prompt": "", "task_id": "t"}

        def _c(sid, order, fn):
            return {"shot_id": sid, "order": order, "scene_id": "SC1",
                    "shot_type": "MEDIUM", "duration_sec": 3.0,
                    "camera": {"shot_size": "中景"}, "render_result": _rr(fn)}

        contracts = [_c("S1", 1, "a.mp4"), _c("S2", 2, "b.mp4")]
        cut = assemble_rough_cut(contracts, story_id="s", bundle_id="b",
                                 exclude_shot_ids={"S2"})
        ids = [e["shot_id"] for e in cut["timeline"]]
        assert ids == ["S1"] and "S2" not in ids     # 坏镜 S2 不进 Post


class _FakeBundle:
    def path_for(self, rel):
        return Path(rel)
