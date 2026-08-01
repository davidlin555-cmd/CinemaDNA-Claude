"""LatentSync 唇形后端测试 —— available/参数/成败/优先级（0 成本，假 runner）。"""

from __future__ import annotations

from pathlib import Path

from audio.lipsync import LatentSyncBackend, PassthroughLipSync, resolve_lipsync


def _ready_root(tmp_path: Path) -> Path:
    """造出"就绪"的 LatentSync 目录（假 venv python + 假模型 + 配置）。"""
    (tmp_path / ".venv/Scripts").mkdir(parents=True)
    (tmp_path / ".venv/Scripts/python.exe").write_bytes(b"x")
    (tmp_path / "checkpoints").mkdir()
    (tmp_path / "checkpoints/latentsync_unet.pt").write_bytes(b"x" * 2000)
    (tmp_path / "configs/unet").mkdir(parents=True)
    (tmp_path / "configs/unet/stage2_512.yaml").write_text("x")
    return tmp_path


class TestAvailability:
    def test_not_available_when_missing(self, tmp_path):
        be = LatentSyncBackend(root=tmp_path)          # 空目录
        assert not be.available()

    def test_available_when_ready(self, tmp_path):
        be = LatentSyncBackend(root=_ready_root(tmp_path))
        assert be.available() and be.real is True

    def test_sync_refuses_when_not_ready(self, tmp_path):
        be = LatentSyncBackend(root=tmp_path)
        r = be.sync(face_video=Path("a.mp4"), audio=Path("a.wav"),
                    dest=tmp_path / "o.mp4")
        assert r["ok"] is False and r["lip_synced"] is False


class TestSync:
    def test_builds_correct_argv_and_marks_synced(self, tmp_path):
        root = _ready_root(tmp_path)
        face = tmp_path / "face.mp4"; face.write_bytes(b"v" * 2000)
        audio = tmp_path / "a.wav"; audio.write_bytes(b"a" * 2000)
        dest = tmp_path / "out.mp4"
        captured = {}

        def runner(argv, cwd):
            captured["argv"] = argv
            captured["cwd"] = cwd
            dest.write_bytes(b"o" * 5000)              # 模拟产出对口型视频
            return 0

        be = LatentSyncBackend(root=root, runner=runner)
        r = be.sync(face_video=face, audio=audio, dest=dest)
        assert r["ok"] and r["lip_synced"] and r["backend"] == "latentsync"
        a = captured["argv"]
        assert "scripts.inference" in a and "--enable_deepcache" in a
        assert str(face.resolve()) in a and str(audio.resolve()) in a
        assert captured["cwd"] == root                 # 在 LatentSync 目录里跑

    def test_nonzero_rc_is_honest_failure(self, tmp_path):
        root = _ready_root(tmp_path)
        be = LatentSyncBackend(root=root, runner=lambda argv, cwd: 1)
        r = be.sync(face_video=Path("f.mp4"), audio=Path("a.wav"),
                    dest=tmp_path / "o.mp4")
        assert not r["ok"] and not r["lip_synced"]     # 失败不冒充同步


class TestResolvePriority:
    def test_prefers_latentsync_when_ready(self, tmp_path, monkeypatch):
        root = _ready_root(tmp_path)
        monkeypatch.setenv("LATENTSYNC_ROOT", str(root))
        be = resolve_lipsync()
        assert getattr(be, "name", "") == "latentsync"

    def test_falls_back_to_passthrough_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LATENTSYNC_ROOT", str(tmp_path / "nope"))
        monkeypatch.delenv("WAV2LIP_CMD", raising=False)
        be = resolve_lipsync()
        assert isinstance(be, PassthroughLipSync)
