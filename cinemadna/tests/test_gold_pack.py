"""CharacterGoldPack 测试 —— 金图包生成/选角度/硬规则/渲染锚帧（0 成本，假图后端）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from asset_brain.common.bundle import Bundle
from identity.gold_pack import (
    GoldPackBuilder, GoldPackError, require_gold_pack, gold_prompt, CharacterGoldPack,
)


class FakeImg:
    name = "fake"
    def generate(self, *, prompt, dest, item_id, width, height):
        Path(dest).write_bytes(b"\x89PNG\r\n" + b"0" * 900)
        return dest


class TestGoldPackBuild:
    def test_builds_multi_angle_pack(self, tmp_path):
        b = Bundle(tmp_path, "b1")
        pack = GoldPackBuilder(FakeImg(), bundle=b).build(
            character_id="char_linwan",
            character_desc="a Chinese nurse", costume_lock="white uniform")
        assert pack.gate_passed and pack.id_hash.startswith("sha256:")
        assert "front" in pack.angles and "three_quarter" in pack.angles
        assert pack.angles_are_reference_consistent is False   # 诚实：非严格同脸
        assert b.path_for("02_cast/gold/char_linwan/front.png").is_file()

    def test_gold_prompt_english_and_anti_text(self):
        p = gold_prompt("a Chinese nurse", "front")
        assert "no text" in p and "no chinese characters" in p
        assert "撕" not in p and "front view" in p

    def test_angle_for_selects_by_shot(self):
        pack = CharacterGoldPack(
            character_id="c", gold_pack_id="gp", character_desc="d", costume_lock="",
            angles={"front": "f.png", "three_quarter": "tq.png",
                    "expression_pressure": "p.png"}, gate_passed=True)
        assert pack.angle_for("CLOSEUP") == "f.png"
        assert pack.angle_for("MEDIUM") == "tq.png"
        assert pack.angle_for("CLOSEUP", pressure=True) == "p.png"


class TestHardRule:
    def test_no_gold_pack_blocks_render(self):
        with pytest.raises(GoldPackError):
            require_gold_pack(None, character_id="c")

    def test_empty_pack_blocks_render(self):
        empty = CharacterGoldPack(character_id="c", gold_pack_id="gp",
                                  character_desc="d", costume_lock="", angles={})
        with pytest.raises(GoldPackError):
            require_gold_pack(empty, character_id="c")


class TestRenderUsesGoldPack:
    def test_i2v_reference_is_gold_angle(self, tmp_path):
        from render.first_frame import (
            SceneGroundedImageToVideoBackend, FirstFrameBackend)
        from render.backend import RenderRequest, MediaSink, BackendCapabilities
        from render.budget import BudgetGate, confirm_yes

        b = Bundle(tmp_path, "b")
        pack = GoldPackBuilder(FakeImg(), bundle=b).build(
            character_id="linwan", character_desc="a Chinese nurse")
        # 只有参考条件级同脸金图(Redux/Elements)才拿来当每镜锚帧；这里模拟之
        pack.angles_are_reference_consistent = True

        submitted = {}
        def http(method, url, headers, body):
            if method == "POST":
                submitted["image"] = body.get("image")
                return 200, {"code": 0, "data": {"task_id": "j"}}
            return 200, {"code": 0, "data": {"task_status": "succeed",
                         "task_result": {"videos": [{"url": "http://x/v.mp4"}]}}}

        gate = BudgetGate(max_units=10, confirm=confirm_yes,
                          allowed_durations_sec=tuple(range(2, 13)),
                          cost_table={("kling-v1", "std", d): 1.0 for d in range(2, 13)})
        be = SceneGroundedImageToVideoBackend(
            first_frame_backend=FirstFrameBackend(FakeImg()),
            gold_packs={"linwan": pack}, require_gold=True,
            budget=gate, bundle_root=tmp_path, api_key="k", base="http://x", http=http)
        be.capabilities = BackendCapabilities(
            min_duration_sec=2.0, max_duration_sec=12.5, supports_reference_images=True,
            produces_media=True, is_async=True, accepts_any_resolution=True,
            duration_tolerance_sec=9.0)
        be.download = lambda url, dest: Path(dest).write_bytes(b"\x00" * 5000)
        req = RenderRequest(
            shot_id="EP001_SC001_SH001", task_id="t", story_id="s", bundle_id="b",
            contract_hash="h", model="kling-v1", tier="std", duration_sec=3, seed=1,
            prompt="动作", camera={"shot_size": "特写", "shot_type": "CLOSEUP"},
            emotion={"intensity": 0.8},
            reference_assets={"character_images": {"linwan": "x.png"}, "props": {}})
        be.render(req, sink=MediaSink(b))
        assert submitted["image"], "i2v 参考必须是金图锚帧"
        # 用了金图锚帧，不生成按镜首帧
        assert not b.path_for(
            "08_submissions/first_frames/shot__EP001_SC001_SH001.png").is_file()
