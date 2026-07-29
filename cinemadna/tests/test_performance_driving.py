"""表演驱动库测试 —— 检索/回流 + E1首采/E2复用编排 + 诚实降级（0成本，注入后端）。"""

from __future__ import annotations

from pathlib import Path

from performance_driving.library import (
    PerformanceDrivingLibrary, DrivingTake, take_key, norm_emotion)
from performance_driving.runway import RunwayActOneBackend
from performance_driving.liveportrait import LivePortraitBackend
from performance_driving.service import PerformanceDrivingService


def _vid(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"MP4" * 500)
    return p


class TestLibrary:
    def test_import_and_find(self, tmp_path):
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        src = _vid(tmp_path / "drive.mp4")
        lib.import_driving_video(src, take_id="t1", emotion="决绝", intensity="high")
        t = lib.find("决绝", "high")
        assert t is not None and t.emotion == "爆发"   # 决绝→爆发归一
        assert (lib.root / t.driving_video).is_file()

    def test_emotion_alias_hits(self, tmp_path):
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        lib.import_driving_video(_vid(tmp_path / "d.mp4"), take_id="t", emotion="压抑",
                                intensity="mid")
        assert lib.has("隐忍", "mid")                   # 压抑/隐忍同键

    def test_dangling_reference_filtered(self, tmp_path):
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        lib.add(DrivingTake(take_id="x", emotion="爆发", intensity="high",
                            driving_video="takes/nope.mp4"))   # 文件不存在
        assert lib.find("爆发", "high") is None          # 悬空引用被过滤

    def test_persistence_reload(self, tmp_path):
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        lib.import_driving_video(_vid(tmp_path / "d.mp4"), take_id="t", emotion="施压",
                                intensity="high")
        lib2 = PerformanceDrivingLibrary(tmp_path / "lib")   # 重新加载
        assert lib2.has("施压", "high")


class TestRunway:
    def test_perform_with_injected_io(self, tmp_path):
        cap = {}
        be = RunwayActOneBackend(
            api_key="k",
            submit_fn=lambda a: cap.setdefault("args", a) or "task1",
            poll_fn=lambda t: (True, "http://x/out.mp4"),
            upload_fn=lambda p: f"uri://{Path(p).name}",
            download_fn=lambda u, d: Path(d).write_bytes(b"VID" * 500),
            sleep_fn=lambda s: None)
        ch = _vid(tmp_path / "char.png"); ref = _vid(tmp_path / "ref.mp4")
        dest = tmp_path / "out.mp4"
        be.perform(character_image=ch, reference=ref, dest=dest)
        assert dest.is_file() and be.available()
        assert cap["args"]["character"]["uri"] == "uri://char.png"


class TestService:
    def _lp(self, ok=True):
        lp = LivePortraitBackend()
        lp.available = lambda: True

        def animate(*, source, driving, dest):
            if ok:
                Path(dest).write_bytes(b"LP" * 500)
                return {"ok": True}
            return {"ok": False}

        lp.animate = animate
        return lp

    def test_reuse_from_library_local_zero_cost(self, tmp_path):
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        lib.import_driving_video(_vid(tmp_path / "d.mp4"), take_id="t", emotion="爆发",
                                intensity="high")
        svc = PerformanceDrivingService(lib, liveportrait=self._lp())
        r = svc.perform(character_image=_vid(tmp_path / "face.png"), emotion="爆发",
                        intensity="high", dest=tmp_path / "o.mp4")
        assert r["ok"] and r["route"] == "reuse_liveportrait" and r["cost"] == "local_$0"

    def test_acquire_via_runway_when_missing_then_backflow(self, tmp_path):
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        runway = RunwayActOneBackend(
            api_key="k", submit_fn=lambda a: "t", poll_fn=lambda t: (True, "u"),
            upload_fn=lambda p: "uri", download_fn=lambda u, d: Path(d).write_bytes(
                b"VID" * 500), sleep_fn=lambda s: None)
        svc = PerformanceDrivingService(lib, runway=runway, liveportrait=self._lp())
        r = svc.perform(character_image=_vid(tmp_path / "face.png"), emotion="哀求",
                        intensity="mid", dest=tmp_path / "o.mp4",
                        driving_ref=_vid(tmp_path / "ref.mp4"))
        assert r["route"] == "acquire_runway"
        assert lib.has("哀求", "mid")                    # 首采后回流存库

    def test_unavailable_is_honest(self, tmp_path):
        # 库缺 + 无驱动参考 → 诚实标记降级，不冒充
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        svc = PerformanceDrivingService(lib)
        r = svc.perform(character_image=_vid(tmp_path / "f.png"), emotion="爆发",
                        intensity="high", dest=tmp_path / "o.mp4")
        assert not r["ok"] and r["route"] == "unavailable"

    def test_library_missing_but_no_liveportrait_no_reuse(self, tmp_path):
        # 库有 take 但 E2 未装 + 无 E1 → 诚实不可用
        lib = PerformanceDrivingLibrary(tmp_path / "lib")
        lib.import_driving_video(_vid(tmp_path / "d.mp4"), take_id="t", emotion="爆发",
                                intensity="high")
        svc = PerformanceDrivingService(lib)             # 无 lp 无 runway
        r = svc.perform(character_image=_vid(tmp_path / "f.png"), emotion="爆发",
                        intensity="high", dest=tmp_path / "o.mp4")
        assert not r["ok"] and "E2 未装" in r["reason"]
