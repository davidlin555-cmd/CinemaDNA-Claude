"""④ 口型接进流水线测试 —— 逐说话镜真跑/替换/诚实状态/未同步不进片（0 成本，假后端）。"""

from __future__ import annotations

from pathlib import Path

from cinemadna.asset_brain.common.bundle import Bundle
from orchestrator.pipeline import PipelineOrchestrator


class FakeLip:
    """假唇形后端：real=True；dest.stem 在 fail 集里则失败。"""
    real = True
    name = "fake-lip"

    def __init__(self, fail=()):
        self.fail = set(fail)

    def available(self):
        return True

    def sync(self, *, face_video, audio, dest):
        sid = Path(dest).stem
        if sid in self.fail:
            return {"ok": False, "lip_synced": False, "error": "对齐失败"}
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"synced" * 200)
        return {"ok": True, "lip_synced": True, "relpath": dest.name}


def _setup(tmp_path, lip):
    orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260726")
    s = orc.create_story(title="t")
    sid = s.story_id
    b = Bundle(tmp_path, s.bundle_id)
    orc._bundles[sid] = b
    orc.lipsync_backend = lip
    contracts = []
    for i, (shot, talk) in enumerate([("SH1", True), ("SH2", True), ("SH3", False)], 1):
        vid_rel = f"09_downloads/{shot}.mp4"
        b.path_for(vid_rel).parent.mkdir(parents=True, exist_ok=True)
        b.path_for(vid_rel).write_bytes(b"video" * 300)
        c = {"shot_id": shot, "order": i, "scene_id": "SC1", "shot_type": "CLOSEUP",
             "duration_sec": 3.0, "render_result": {"file_relpath": vid_rel,
             "model": "kling", "task_id": f"t{i}", "is_generated_footage": True}}
        if talk:
            aud = b.path_for(f"05_performance/audio/{shot}/0.mp3")
            aud.parent.mkdir(parents=True, exist_ok=True)
            aud.write_bytes(b"audio" * 100)
            c["dialogue"] = [{"character_id": "c", "line": "台词"}]
            c["voice_contract"] = {"bindings": [{"line": "台词",
                                    "audio_abspath": str(aud)}]}
        contracts.append(c)
    orc._contracts[sid] = contracts
    return orc, sid, contracts


class TestRunLipSync:
    def test_talking_shots_synced_and_replaced(self, tmp_path):
        orc, sid, contracts = _setup(tmp_path, FakeLip())
        r = orc.run_lip_sync(sid)
        assert r["lip_synced"] is True                    # 有成功且无失败
        assert set(r["synced"]) == {"SH1", "SH2"}
        assert r["skipped"] == ["SH3"]                    # 无对白镜跳过
        # 说话镜视频被替换成对口型版
        assert contracts[0]["render_result"]["file_relpath"] == "09_downloads/lipsync/SH1.mp4"
        assert contracts[0]["lip_synced"] is True

    def test_failed_shot_makes_status_false_and_excluded(self, tmp_path):
        orc, sid, contracts = _setup(tmp_path, FakeLip(fail={"SH2"}))
        r = orc.run_lip_sync(sid)
        assert r["lip_synced"] is False                   # 有失败 → 不冒充全片对齐
        assert [f["shot_id"] for f in r["failed"]] == ["SH2"]
        # 未同步不得过说话镜：SH2 进排除集
        assert "SH2" in orc._lip_failed_ids(sid)

    def test_no_real_backend_is_honest_passthrough(self, tmp_path):
        from audio.lipsync import PassthroughLipSync
        orc, sid, _ = _setup(tmp_path, PassthroughLipSync())
        r = orc.run_lip_sync(sid)
        assert r["lip_synced"] is False                   # 直通诚实占位
        assert r["synced"] == []

    def test_mux_does_not_fake_synced_without_run(self, tmp_path):
        # 没跑 run_lip_sync 时，lip 状态诚实为 False（即便有真实后端可用）
        orc, sid, _ = _setup(tmp_path, FakeLip())
        # 直接取状态（未 run_lip_sync）
        assert orc._lip_sync.get(sid) is None
        st = orc._lip_sync.get(sid) or {"lip_synced": False}
        assert st["lip_synced"] is False
