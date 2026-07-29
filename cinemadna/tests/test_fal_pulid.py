"""FAL PuLID 后端测试 —— 上传/生成/下载/预算（0 成本，注入 IO）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from identity.fal_pulid import FalPuLIDBackend, FalPuLIDError


def _ref(tmp_path) -> Path:
    p = tmp_path / "anchor.png"
    p.write_bytes(b"\x89PNG\r\n" + b"0" * 900)
    return p


class TestGenerate:
    def test_generates_with_injected_io(self, tmp_path):
        captured = {}

        def run(args):
            captured["args"] = args
            return {"images": [{"url": "http://x/out.png"}]}

        be = FalPuLIDBackend(
            api_key="k", run_fn=run,
            upload_fn=lambda p: "http://x/ref.png",
            download_fn=lambda url, dest: Path(dest).write_bytes(b"IMG" * 500))
        dest = tmp_path / "out.png"
        be.generate(reference_face=_ref(tmp_path), prompt="a nurse close-up",
                    dest=dest, width=768, height=1360)
        assert dest.is_file()
        assert captured["args"]["reference_image_url"] == "http://x/ref.png"
        assert captured["args"]["prompt"] == "a nurse close-up"
        assert captured["args"]["image_size"] == {"width": 768, "height": 1360}

    def test_missing_reface_raises(self, tmp_path):
        be = FalPuLIDBackend(api_key="k", run_fn=lambda a: {},
                             upload_fn=lambda p: "u", download_fn=lambda u, d: None)
        with pytest.raises(FalPuLIDError):
            be.generate(reference_face=tmp_path / "nope.png", prompt="x",
                        dest=tmp_path / "o.png")

    def test_no_image_returned_raises(self, tmp_path):
        be = FalPuLIDBackend(api_key="k", run_fn=lambda a: {"images": []},
                             upload_fn=lambda p: "u", download_fn=lambda u, d: None)
        with pytest.raises(FalPuLIDError):
            be.generate(reference_face=_ref(tmp_path), prompt="x",
                        dest=tmp_path / "o.png")

    def test_budget_gate_authorizes_and_records(self, tmp_path):
        from render.budget import BudgetGate, confirm_yes
        gate = BudgetGate(max_units=10, confirm=confirm_yes,
                          allowed_durations_sec=(), cost_table={})
        be = FalPuLIDBackend(
            api_key="k", budget=gate,
            run_fn=lambda a: {"images": [{"url": "http://x/o.png"}]},
            upload_fn=lambda p: "u",
            download_fn=lambda url, dest: Path(dest).write_bytes(b"IMG" * 500))
        be.generate(reference_face=_ref(tmp_path), prompt="x",
                    dest=tmp_path / "o.png", item_id="anchor_linwan")
        assert gate.spent_units > 0                     # 记账了
