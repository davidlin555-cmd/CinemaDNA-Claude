"""多模态视觉判断 + 唇形 (Phase J) 测试 —— 真跑像素层 / 深判插槽 / 唇形诚实状态。"""

from __future__ import annotations

from pathlib import Path

from audio.lipsync import (
    PassthroughLipSync,
    Wav2LipBackend,
    resolve_lipsync,
)
from final_review.vision_judge import VisionJudge


class TestVisionJudge:
    def _judge(self, motion_yavgs, ymax, ymin):
        def runner(argv):
            if "tblend=all_mode=difference" in " ".join(argv):
                return "".join(f"YAVG={v}\n" for v in motion_yavgs)
            return "".join(f"YMAX={a}\nYMIN={b}\n" for a, b in zip(ymax, ymin))
        return VisionJudge(runner=runner).judge

    def test_real_footage_passes(self, tmp_path):
        r = self._judge([0.4, 0.35, 0.42], [242, 240], [8, 10])(tmp_path / "v.mp4")
        assert r.judged and r.fake is False and not r.checks["static"]

    def test_static_flagged_as_fake(self, tmp_path):
        r = self._judge([0.0, 0.0], [55, 55], [55, 55])(tmp_path / "v.mp4")
        assert r.fake is True
        assert r.checks["static"] and r.checks["blank"]
        assert r.reasons and "运动" in r.reasons[0]

    def test_deep_judge_slot_honest(self, tmp_path):
        # 未接 LLM → deep_judged=False（不假装判过）
        r = self._judge([0.4], [200], [10])(tmp_path / "v.mp4")
        assert r.deep_judged is False

    def test_deep_judge_backend_runs(self, tmp_path):
        class FakeLLM:
            def judge_frame(self, frame):
                return {"ai_fake": True, "reason": "手指数量异常"}
        def runner(argv):
            if "tblend" in " ".join(argv): return "YAVG=0.4\n"
            if "-frames:v" in argv:
                # 假装抽帧
                Path(argv[-1]).write_bytes(b"\xff\xd8\xff" + b"0" * 500)
                return ""
            return "YMAX=200\nYMIN=10\n"
        vj = VisionJudge(runner=runner, llm_backend=FakeLLM())
        r = vj.judge(tmp_path / "v.mp4")
        assert r.deep_judged is True and r.fake is True     # 深判命中假感
        assert "手指" in r.deep.get("reason", "")


class TestLipSync:
    def test_passthrough_is_honest(self, tmp_path):
        be = PassthroughLipSync()
        assert be.real is False and be.name == "passthrough"
        v = tmp_path / "face.mp4"; v.write_bytes(b"\x00" * 2000)
        res = be.sync(face_video=v, audio=tmp_path / "a.mp3", dest=tmp_path / "o.mp4")
        assert res["lip_synced"] is False

    def test_wav2lip_unavailable_without_cmd(self):
        assert Wav2LipBackend(cmd="").available() is False

    def test_wav2lip_runs_when_configured(self, tmp_path):
        called = {}
        def runner(argv):
            called["argv"] = argv
            Path(argv[-1]).write_bytes(b"\x00" * 5000)   # 假装出片
        be = Wav2LipBackend(cmd="fakew2l --face {face} --audio {audio} --out {out}",
                            runner=runner)
        # available 检测命令首词；用一个存在的可执行名绕过（这里直接测 sync 逻辑）
        object.__setattr__(be, "available", lambda: True)
        res = be.sync(face_video=tmp_path / "f.mp4", audio=tmp_path / "a.mp3",
                      dest=tmp_path / "o.mp4")
        assert res["lip_synced"] is True and res["backend"] == "wav2lip"
        assert "--face" in called["argv"]

    def test_resolve_falls_back_to_passthrough(self, monkeypatch):
        # 隔离真实安装：指向不存在的 LatentSync + 清 WAV2LIP_CMD → 应退直通
        monkeypatch.setenv("LATENTSYNC_ROOT", "/no/such/latentsync")
        monkeypatch.delenv("WAV2LIP_CMD", raising=False)
        be = resolve_lipsync()
        assert be.real is False


# ---------------------------------------------------------------------------
# run_audio_mux 回归：ambience 必须是预设名字符串，不能把 bool 传进混流器
# ---------------------------------------------------------------------------


class TestAudioMuxIntegration:
    def test_mux_produces_file_and_records_lipsync(self, tmp_path):
        from orchestrator.pipeline import PipelineOrchestrator
        from orchestrator.story import StoryStage
        from audio.mixer import AudioMixer

        def fake_runner(argv, cwd):
            # 假装 ffmpeg 出片；顺带断言没有把 bool 当 ambience（不会崩）
            out = argv[-1]
            (cwd / out).write_bytes(b"\x00" * 5000)

        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724",
                                   audio_mixer=AudioMixer(runner=fake_runner))
        s = orc.create_story(title="A")
        b = orc.bundle_for(s.story_id)
        (b.path_for("10_outputs/rough_cut.mp4")).parent.mkdir(parents=True, exist_ok=True)
        b.path_for("10_outputs/rough_cut.mp4").write_bytes(b"\x00" * 5000)
        a = b.path_for("05_performance/audio/SH1/00_a.mp3")
        a.parent.mkdir(parents=True, exist_ok=True)
        a.write_bytes(b"\x00" * 2000)
        orc._contracts[s.story_id] = [{
            "shot_id": "SH1", "order": 1, "duration_sec": 5.0,
            "emotion": {"beat": "CONFLICT", "intensity": 0.7},
            "dialogue": [{"character_id": "a", "line": "还钱"}],
            "voice_contract": {"bindings": [{"speaker": "a", "line": "还钱",
                "audio_file": "05_performance/audio/SH1/00_a.mp3", "duration_sec": 2.0}]}}]

        res = orc.run_audio_mux(s.story_id)      # 之前 bool ambience 会崩
        assert res["ok"] is True
        assert orc._lip_sync[s.story_id]["lip_synced"] is False   # 诚实
