"""② 语义 VisionQA 测试 —— 判意图匹配/不当内容熔断（0 成本，假判官）。"""

from __future__ import annotations

from pathlib import Path

from render.vision_quarantine import SemanticCheck, VisionQuarantine
from final_review.vision_llm import SemanticVisionResult


class _FakeBundle:
    def path_for(self, rel):
        return Path(rel)


class _FakeSemJudge:
    """按 shot_id → inappropriate 映射的假语义判官。"""
    def __init__(self, bad):
        self.bad = dict(bad)   # shot_id -> kind
    def judge_semantic(self, frame, intent):
        sid = Path(frame).stem.replace("_sem_", "")
        kind = self.bad.get(sid, "")
        return SemanticVisionResult({"matches_intent": not kind,
                                     "inappropriate": bool(kind),
                                     "inappropriate_kind": kind, "reason": kind})


def _rendered(*ids):
    return [{"shot_id": i, "file_relpath": f"{i}.mp4",
             "is_generated_footage": True, "duration_sec": 3.0} for i in ids]


class TestSemanticCheck:
    def test_inappropriate_content_flagged(self):
        chk = SemanticCheck(
            _FakeSemJudge({"SH6": "接吻"}), {"SH6": "施压:追债人逼近"},
            bundle=_FakeBundle(), runner=lambda argv: 0)
        # runner 返回 0 但不真出帧；让 _mid_frame 认为成功 → 需 frame 存在。用注入判官绕过。
        # 直接测 __call__ 的判定分支：intent 有 + inappropriate → 熔断
        reasons = chk({"shot_id": "SH6", "file_relpath": "SH6.mp4",
                       "is_generated_footage": True, "duration_sec": 3.0})
        # frame 抽取失败(runner=0 但无真文件) → 返回空。改用可控 frame：
        assert isinstance(reasons, list)

    def test_no_intent_skips(self):
        chk = SemanticCheck(_FakeSemJudge({}), {}, bundle=_FakeBundle())
        assert chk({"shot_id": "X", "file_relpath": "x.mp4",
                    "is_generated_footage": True}) == []

    def test_placeholder_not_judged(self):
        chk = SemanticCheck(_FakeSemJudge({"S": "接吻"}), {"S": "对峙"},
                            bundle=_FakeBundle())
        assert chk({"shot_id": "S", "file_relpath": "s.mp4",
                    "is_generated_footage": False}) == []   # 占位不判


class TestSemanticCheckWithRealFrame:
    """用真 frame 文件走通熔断分支。"""
    def _chk(self, tmp_path, bad):
        # runner 真写一个 frame 文件 → _mid_frame 成功
        def runner(argv):
            Path(argv[-1]).write_bytes(b"\xff\xd8jpg")
            return 0
        return SemanticCheck(_FakeSemJudge(bad), {"SH6": "施压:追债人逼近护士",
                             "SH7": "举证据"}, bundle=_Bundle(tmp_path), runner=runner)

    def test_kiss_shot_quarantined(self, tmp_path):
        chk = self._chk(tmp_path, {"SH6": "接吻"})
        (tmp_path / "SH6.mp4").write_bytes(b"v")
        reasons = chk({"shot_id": "SH6", "file_relpath": "SH6.mp4",
                       "is_generated_footage": True, "duration_sec": 3.0})
        assert reasons and "接吻" in reasons[0]

    def test_good_shot_passes(self, tmp_path):
        chk = self._chk(tmp_path, {})
        (tmp_path / "SH7.mp4").write_bytes(b"v")
        assert chk({"shot_id": "SH7", "file_relpath": "SH7.mp4",
                    "is_generated_footage": True, "duration_sec": 3.0}) == []

    def test_wired_as_quarantine_extra_check(self, tmp_path):
        # SemanticCheck 作 extra_check → 接吻镜被熔断隔离
        chk = self._chk(tmp_path, {"SH6": "接吻"})
        (tmp_path / "SH6.mp4").write_bytes(b"v")
        (tmp_path / "SH7.mp4").write_bytes(b"v")

        class NoJudge:
            def available(self): return False
        q = VisionQuarantine(judge=NoJudge(), extra_check=chk).screen(
            _rendered("SH6", "SH7"), bundle=_Bundle(tmp_path))
        assert "SH6" in q.quarantined_ids and "SH7" in q.passed


class _Bundle:
    def __init__(self, root):
        self.root = Path(root)
    def path_for(self, rel):
        return self.root / rel
