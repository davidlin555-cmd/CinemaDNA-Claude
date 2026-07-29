"""渲染后端接口 + Registry + 导出 单元测试 (Phase 4)

重点验证「无缝切换到真实模型」这条路是不是真的通：
- Registry 按模型名派单，未注册的回落默认后端
- 能力声明能挡住做不了的活
- 重试 / 致命错误的区分
- 产物校验能挡住串单与垃圾返回
- 真实后端模板（提交→轮询→下载）能跑
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from director.shot_contract import SHOT_CLOSEUP, SHOT_MEDIUM, new_shot_contract
from performance.service import PerformanceDNAService
from render.assembly import (
    assemble_rough_cut,
    build_animatic_html,
    build_edl_text,
    build_vtt,
    concat_with_ffmpeg,
    export_cut,
)
from render.backend import (
    BackendCapabilities,
    BaseAsyncRenderBackend,
    FatalRenderError,
    MediaSink,
    MockRenderBackend,
    PlaceholderMediaBackend,
    RenderBackend,
    RenderRequest,
    RetryableRenderError,
    build_rendered_shot,
    ffmpeg_available,
    validate_rendered_shot,
)
from render.registry import BackendRegistry, mock_registry, placeholder_registry
from render.router import route_shot
from render.service import RenderBrainService

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

SCRIPT = {
    "script_id": "s1", "title": "T",
    "characters": [{"character_id": "char_a", "name": "林晚",
                    "personality_keywords": ["压抑"], "role_type": "主角"}],
    "props": [], "episodes": [{"episode_id": "EP001", "scenes": [
        {"scene_id": "EP001_SC001", "episode_id": "EP001", "beat_type": "CONFLICT",
         "spatial_needs": "可走动", "camera_intent": "压迫感",
         "characters": ["char_a"], "props": [], "prop_states": {}}]}],
}

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="需要 ffmpeg")


def make_contract(shot_id="SH1", order=1, shot_type=SHOT_MEDIUM, duration=5.0):
    return new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001", order=order,
        shot_type=shot_type,
        quad=Quad(story_id="drama_0001", bundle_id="bundle_1",
                  task_id=f"task_shot_{order:04d}", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视",
                "intent": "对峙"},
        duration_sec=duration,
        emotion={"beat": "CONFLICT", "intensity": 0.82, "mood": "压抑"},
        scene_asset={"asset_id": "scene_x", "asset_hash": HASH_A,
                     "gate_passed": True, "tags": ["出租屋", "深夜"]},
        character_assets=[{"character_id": "char_a", "master_pack_id": "cmp_a",
                           "asset_hash": HASH_B, "gate_passed": True}],
        prop_assets=[],
        dialogue=[{"character_id": "char_a", "line": "我没有退路了。"}],
        notes="她被当众收回工牌",
    )


def signed(*contracts):
    cs = list(contracts)
    PerformanceDNAService().run(cs, shooting_script=SCRIPT)
    return cs


def a_request(contract=None) -> RenderRequest:
    c = contract or signed(make_contract())[0]
    return RenderBrainService.build_request(c, route_shot(c))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_falls_back_to_default(self):
        reg = mock_registry()
        assert reg.get("cloud-face-v1").name == "mock"
        assert reg.has_real_backend("cloud-face-v1") is False

    def test_registered_backend_wins(self):
        class Fake(MockRenderBackend):
            name = "fake-kling"

        reg = mock_registry().register("cloud-face-v1", Fake())
        assert reg.get("cloud-face-v1").name == "fake-kling"
        assert reg.get("local-video-v1").name == "mock"
        assert reg.has_real_backend("cloud-face-v1") is True

    def test_rejects_non_backend(self):
        with pytest.raises(TypeError):
            mock_registry().register("x", object())

    def test_describe_shape(self):
        reg = mock_registry().register("cloud-face-v1", MockRenderBackend())
        d = reg.describe()
        assert d["default_backend"] == "mock"
        assert d["registered"]["cloud-face-v1"]["produces_media"] is False

    def test_routing_actually_selects_per_model_backend(self):
        """路由说用哪个模型，Registry 就得派对应的后端 —— 这是切换点。"""
        class FaceBackend(MockRenderBackend):
            name = "face-backend"

        class LocalBackend(MockRenderBackend):
            name = "local-backend"

        face, local = FaceBackend(), LocalBackend()
        reg = (BackendRegistry(default=MockRenderBackend())
               .register("cloud-face-v1", face)
               .register("local-video-v1", local))
        cs = signed(make_contract("SH1", 1, SHOT_CLOSEUP), make_contract("SH2", 2))
        result = RenderBrainService(registry=reg).render_all(cs)

        assert result.rendered[0]["backend"] == "face-backend"   # 特写走人脸模型
        assert result.rendered[1]["backend"] == "local-backend"  # 中景走本地
        assert face.calls == ["SH1"] and local.calls == ["SH2"]


# ---------------------------------------------------------------------------
# 能力声明
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_duration_out_of_range_is_caught(self):
        caps = BackendCapabilities(max_duration_sec=3.0)
        assert caps.check(a_request())      # 5 秒 > 3 秒上限

    def test_service_rejects_shot_backend_cannot_do(self):
        class Tiny(MockRenderBackend):
            name = "tiny"
            capabilities = BackendCapabilities(max_duration_sec=2.0)

        cs = signed(make_contract())
        result = RenderBrainService(backend=Tiny()).render_all(cs)
        assert result.rendered == []
        assert "做不了" in result.failed[0]["reason"]

    def test_resolution_and_fps_checked(self):
        caps = BackendCapabilities(resolutions=("720x1280",), fps_options=(30,))
        issues = caps.check(a_request())
        assert len(issues) == 2


# ---------------------------------------------------------------------------
# 重试与错误分类
# ---------------------------------------------------------------------------


class TestRetryAndErrors:
    def test_retryable_error_is_retried(self):
        backend = MockRenderBackend(flaky_shots={"SH1"})
        cs = signed(make_contract())
        result = RenderBrainService(backend=backend, max_attempts=2).render_all(cs)
        assert len(result.rendered) == 1
        assert result.retries == 1
        assert backend.calls == ["SH1", "SH1"]        # 真的重试了

    def test_fatal_error_is_not_retried(self):
        backend = MockRenderBackend(fail_shots={"SH1"})
        cs = signed(make_contract())
        result = RenderBrainService(backend=backend, max_attempts=3).render_all(cs)
        assert result.failed and result.retries == 0
        assert backend.calls == ["SH1"]               # 只调了一次

    def test_retry_exhausted_becomes_failure(self):
        backend = MockRenderBackend(flaky_shots={"SH1"})
        cs = signed(make_contract())
        result = RenderBrainService(backend=backend, max_attempts=1).render_all(cs)
        assert result.failed and "重试" in result.failed[0]["reason"]


# ---------------------------------------------------------------------------
# 产物校验
# ---------------------------------------------------------------------------


class TestRenderedShotValidation:
    def _rendered(self, req):
        return build_rendered_shot(
            req, backend="t", file_relpath=f"09_downloads/{req.task_id}/x.mp4",
            media_type="video/mp4", is_real_media=True, metrics={},
        )

    def test_valid_passes(self):
        req = a_request()
        validate_rendered_shot(self._rendered(req), req)

    def test_shot_id_mismatch_rejected(self):
        """后端串单（返回了别的镜头）必须被抓住。"""
        req = a_request()
        bad = self._rendered(req)
        bad["shot_id"] = "SH_OTHER"
        with pytest.raises(FatalRenderError) as ei:
            validate_rendered_shot(bad, req)
        assert "串单" in str(ei.value)

    def test_duration_mismatch_rejected(self):
        req = a_request()
        bad = self._rendered(req)
        bad["duration_sec"] = 9.0
        with pytest.raises(FatalRenderError):
            validate_rendered_shot(bad, req)

    def test_path_outside_task_dir_rejected(self):
        req = a_request()
        bad = self._rendered(req)
        bad["file_relpath"] = "09_downloads/other_task/x.mp4"
        with pytest.raises(FatalRenderError) as ei:
            validate_rendered_shot(bad, req)
        assert "task_id 隔离" in str(ei.value)

    def test_contract_hash_mismatch_rejected(self):
        req = a_request()
        bad = self._rendered(req)
        bad["contract_hash"] = "sha256:" + "c" * 64
        with pytest.raises(FatalRenderError):
            validate_rendered_shot(bad, req)

    def test_service_catches_lying_backend(self, tmp_path):
        """后端声称出片却没落文件 → 服务层当场拆穿。"""
        class Liar(MockRenderBackend):
            name = "liar"
            capabilities = BackendCapabilities(produces_media=True)

            def render(self, request, *, sink):
                return build_rendered_shot(
                    request, backend=self.name,
                    file_relpath=sink.reserve(request.task_id, "ghost.mp4"),
                    media_type="video/mp4", is_real_media=True, metrics={},
                )

        bundle = Bundle(tmp_path, "b1").ensure()
        cs = signed(make_contract())
        result = RenderBrainService(backend=Liar(), bundle=bundle).render_all(cs)
        assert result.failed and "不存在" in result.failed[0]["reason"]


# ---------------------------------------------------------------------------
# MediaSink
# ---------------------------------------------------------------------------


class TestMediaSink:
    def test_reserve_enforces_task_isolation(self, tmp_path):
        sink = MediaSink(Bundle(tmp_path, "b1").ensure())
        assert sink.reserve("task_shot_0001", "a.mp4") == \
            "09_downloads/task_shot_0001/a.mp4"

    def test_reserve_rejects_path_traversal(self, tmp_path):
        sink = MediaSink(Bundle(tmp_path, "b1").ensure())
        with pytest.raises(FatalRenderError):
            sink.reserve("t1", "../../escape.mp4")

    def test_abs_path_without_bundle_fails(self):
        with pytest.raises(FatalRenderError):
            MediaSink(None).abs_path("09_downloads/t/a.mp4")


# ---------------------------------------------------------------------------
# 真实后端模板
# ---------------------------------------------------------------------------


class FakeCloudBackend(BaseAsyncRenderBackend):
    """模拟真实云端模型：提交 → 轮询两次 → 下载。"""

    name = "fake-cloud"
    capabilities = BackendCapabilities(
        max_duration_sec=10.0, supports_reference_images=True,
        produces_media=True, is_async=True,
    )

    def __init__(self, *, polls_needed=2, never_done=False):
        self.polls_needed = polls_needed
        self.never_done = never_done
        self.polls = 0
        self.submitted: list[str] = []

    def submit(self, request):
        self.submitted.append(request.shot_id)
        return f"job_{request.shot_id}"

    def poll(self, job_id):
        self.polls += 1
        if self.never_done:
            return False, None
        return (self.polls >= self.polls_needed), (
            f"https://cdn.example/{job_id}.mp4" if self.polls >= self.polls_needed
            else None
        )

    def download(self, url, dest: Path):
        dest.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")


class TestBaseAsyncBackend:
    def test_full_submit_poll_download_flow(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        backend = FakeCloudBackend()
        cs = signed(make_contract())
        result = RenderBrainService(backend=backend, bundle=bundle).render_all(cs)

        assert len(result.rendered) == 1
        r = result.rendered[0]
        assert r["backend"] == "fake-cloud"
        assert r["is_real_media"] is True
        assert r["is_generated_footage"] is True     # 真实模型出的画面
        assert r["job_id"] == "job_SH1"
        assert bundle.exists(r["file_relpath"])
        assert backend.submitted == ["SH1"]

    def test_poll_timeout_is_retryable(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        backend = FakeCloudBackend(never_done=True)
        backend.max_poll_attempts = 3
        cs = signed(make_contract())
        result = RenderBrainService(backend=backend, bundle=bundle,
                                    max_attempts=1).render_all(cs)
        assert result.failed and "重试" in result.failed[0]["reason"]

    def test_capability_check_runs_before_submit(self, tmp_path):
        """做不了的活不该白提交一次。"""
        bundle = Bundle(tmp_path, "b1").ensure()
        backend = FakeCloudBackend()
        cs = signed(make_contract(duration=11.0))
        result = RenderBrainService(backend=backend, bundle=bundle).render_all(cs)
        assert result.failed and backend.submitted == []

    def test_conforms_to_protocol(self):
        assert isinstance(FakeCloudBackend(), RenderBackend)


# ---------------------------------------------------------------------------
# 占位媒体后端（真出 MP4）
# ---------------------------------------------------------------------------


@needs_ffmpeg
class TestPlaceholderMediaBackend:
    def test_produces_playable_mp4(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        cs = signed(make_contract(duration=2.0))
        result = RenderBrainService(registry=placeholder_registry(),
                                    bundle=bundle).render_all(cs)
        r = result.rendered[0]
        assert r["media_type"] == "video/mp4"
        assert r["is_real_media"] is True
        # 但它不是模型生成的画面 —— 两件事必须分开标注
        assert r["is_generated_footage"] is False
        assert r["placeholder"] is True
        path = bundle.path_for(r["file_relpath"])
        assert path.exists() and path.stat().st_size > 1000

    def test_requires_bundle(self):
        backend = PlaceholderMediaBackend()
        with pytest.raises(FatalRenderError):
            backend.render(a_request(), sink=MediaSink(None))


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


class TestExports:
    def _cut(self, bundle=None, n=2):
        cs = signed(*[make_contract(f"SH{i}", i) for i in range(1, n + 1)])
        RenderBrainService(bundle=bundle).render_all(cs)
        return assemble_rough_cut(cs, story_id="drama_0001", bundle_id="bundle_1",
                                  title="测试")

    def test_vtt_format(self):
        vtt = build_vtt(self._cut())
        assert vtt.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:05.000" in vtt
        assert "我没有退路了。" in vtt

    def test_edl_text_has_header_and_rows(self):
        text = build_edl_text(self._cut())
        assert "总时长 10.0s" in text
        assert text.count("SH1") >= 1

    def test_animatic_html_is_self_contained(self):
        html = build_animatic_html(self._cut())
        assert html.startswith("<!doctype html>")
        assert "SH1" in html and "我没有退路了。" in html
        # 自包含：不许外链任何资源
        assert "http://" not in html and "https://" not in html
        assert "<script src" not in html

    def test_export_cut_writes_all_files(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        cut = self._cut(bundle)
        exports = export_cut(cut, bundle, video=False)
        assert bundle.exists(exports["edl"])
        assert bundle.exists(exports["subtitles"])
        assert bundle.exists(exports["animatic"])
        assert cut["exports"] is exports

    def test_concat_refuses_without_real_media(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        res = concat_with_ffmpeg(self._cut(bundle), bundle)
        assert res["ok"] is False
        assert "非真实媒体" in res["reason"]

    @needs_ffmpeg
    def test_concat_produces_real_mp4(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        cs = signed(*[make_contract(f"SH{i}", i, duration=2.0) for i in (1, 2)])
        RenderBrainService(registry=placeholder_registry(),
                           bundle=bundle).render_all(cs)
        cut = assemble_rough_cut(cs, story_id="d1", bundle_id="b1", title="T")
        assert cut["media_ready"] is True
        assert cut["is_generated_footage"] is False

        res = concat_with_ffmpeg(cut, bundle)
        assert res["ok"] is True
        assert res["duration_sec"] == 4.0
        assert bundle.path_for(res["relpath"]).stat().st_size > 1000
