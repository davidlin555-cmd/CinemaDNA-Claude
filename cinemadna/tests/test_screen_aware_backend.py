"""屏内道具感知后端测试 —— 不真跑 Kling、不花钱（假 http + 假合成 runner）。"""

from __future__ import annotations

from pathlib import Path

from cinemadna.asset_brain.common.bundle import Bundle
from render.backend import MediaSink, RenderRequest
from render.budget import BudgetGate, confirm_yes
from render.screen_aware_backend import (
    ScreenAwareImageToVideoBackend,
    build_composite_argv,
)


def _req(bundle: Bundle, props: dict) -> RenderRequest:
    return RenderRequest(
        shot_id="SH1", task_id="t1", story_id="s", bundle_id=bundle.bundle_id,
        contract_hash="sha256:x", model="kling-v1", tier="std",
        duration_sec=5.0, seed=1, prompt="p",
        reference_assets={"character_images": {"c1": "02_cast/faces/c1.png"},
                          "props": props})


def _fake_http_factory(dest_bytes=b"\x00" * 5000):
    # 提交→返回 task_id；轮询→succeed + url；下载在 backend.download 里走真实 urllib，
    # 所以这里改用 sleep_fn + 注入 download。我们只测 http 提交/轮询链路。
    def http(method, url, headers, body):
        if method == "POST":
            return 200, {"code": 0, "data": {"task_id": "job1"}}
        return 200, {"code": 0, "data": {"task_status": "succeed",
                     "task_result": {"videos": [{"url": "http://x/v.mp4"}]}}}
    return http


class TestBuildCompositeArgv:
    def test_one_screen_layout(self, tmp_path):
        argv = build_composite_argv(base=tmp_path / "b.mp4",
                                    screens=[tmp_path / "ui.png"],
                                    dest=tmp_path / "o.mp4", duration=5.0)
        fc = argv[argv.index("-filter_complex") + 1]
        assert "overlay=" in fc and "enable='between(t," in fc
        assert argv[-1].endswith("o.mp4")

    def test_two_screens_split_timeline(self, tmp_path):
        argv = build_composite_argv(base=tmp_path / "b.mp4",
                                    screens=[tmp_path / "a.png", tmp_path / "c.png"],
                                    dest=tmp_path / "o.mp4", duration=6.0)
        fc = argv[argv.index("-filter_complex") + 1]
        assert fc.count("overlay=") == 2      # 两张屏内道具 → 两段插入


class TestScreenAwareRender:
    def _backend(self, out: Path, composited: list, screen_present: bool):
        gate = BudgetGate(max_units=10.0, confirm=confirm_yes)

        def fake_composite(argv):
            composited.append(argv)
            # 假装 ffmpeg 出片：写 dest（argv 末尾）
            Path(argv[-1]).write_bytes(b"\x00" * 6000)

        be = ScreenAwareImageToVideoBackend(
            budget=gate, bundle_root=out, api_key="k", base="http://x",
            http=_fake_http_factory(), composite_runner=fake_composite)
        # download 走真实 urllib —— 注入假下载，避免联网
        be.download = lambda url, dest: Path(dest).write_bytes(b"\x00" * 5000)
        return be, gate

    @staticmethod
    def _face(bundle: Bundle) -> None:
        f = bundle.path_for("02_cast/faces/c1.png")   # image2video 需真实人脸参考
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x89PNG\r\n" + b"0" * 800)

    def test_composites_when_screen_prop_present(self, tmp_path):
        bundle = Bundle(tmp_path, "b1")
        # 资产阶段已渲的屏内道具 PNG
        png = bundle.path_for("04_props/phone1/未读.png")
        png.parent.mkdir(parents=True, exist_ok=True)
        png.write_bytes(b"\x89PNG\r\n" + b"0" * 800)
        bundle.write_json("04_props/phone1/prop.json",
                          {"prop_id": "phone1", "screen_kind": "phone_chat"})
        self._face(bundle)

        composited: list = []
        be, gate = self._backend(tmp_path, composited, True)
        sink = MediaSink(bundle)
        rendered = be.render(_req(bundle, {"phone1": "asset_phone1"}), sink=sink)

        assert composited, "有屏内道具时必须触发合成"
        assert rendered.get("screen_composited") == ["未读.png"]
        assert rendered["is_real_media"] is True

    def test_no_composite_when_no_screen_prop(self, tmp_path):
        bundle = Bundle(tmp_path, "b2")
        self._face(bundle)
        composited: list = []
        be, _ = self._backend(tmp_path, composited, False)
        sink = MediaSink(bundle)
        rendered = be.render(_req(bundle, {}), sink=sink)

        assert not composited, "无屏内道具不应合成"
        assert "screen_composited" not in rendered
