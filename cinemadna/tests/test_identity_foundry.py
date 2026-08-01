"""IdentityFoundry + PuLID 首帧接线测试 —— 铸锚脸/锁脸/单人镜用PuLID双人不用（0成本）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from asset_brain.common.bundle import Bundle
from identity.foundry import IdentityFoundry, IdentityFoundryError, anchor_prompt


class FakeImg:
    name = "fake-flux"
    def __init__(self): self.calls = []
    def generate(self, *, prompt, dest, item_id, width, height, seed=None):
        self.calls.append({"seed": seed, "item_id": item_id})
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x89PNG" + b"0" * 900)
        return dest


class FakePuLID:
    def __init__(self): self.calls = []
    def generate(self, *, reference_face, prompt, dest, item_id, width, height,
                 id_weight=1.0):
        self.calls.append({"ref": str(reference_face), "prompt": prompt})
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"PULID" + b"0" * 900)
        return dest


class TestFoundry:
    def test_mint_anchor_deterministic_seed(self, tmp_path):
        b = Bundle(tmp_path, "b1")
        img = FakeImg()
        f = IdentityFoundry(image_backend=img, pulid_backend=FakePuLID(), bundle=b)
        a1 = f.mint_anchor("linwan", "a Chinese nurse")
        assert a1.is_file() and f.has_anchor("linwan")
        assert b.path_for("02_cast/anchors/linwan.png").is_file()
        seed1 = img.calls[0]["seed"]
        # 再铸别的角色 → 不同种子；同角色可复现同种子
        IdentityFoundry(image_backend=img, pulid_backend=FakePuLID(),
                        bundle=Bundle(tmp_path, "b2")).mint_anchor("linwan", "x")
        assert img.calls[-1]["seed"] == seed1          # 同角色同种子（可复现）

    def test_anchor_prompt_novel_portrait_anti_text(self):
        p = anchor_prompt("a Chinese nurse")
        assert "portrait" in p and "no text" in p and "no chinese characters" in p

    def test_first_frame_uses_pulid_with_anchor(self, tmp_path):
        b = Bundle(tmp_path, "b")
        pulid = FakePuLID()
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=pulid, bundle=b)
        f.mint_anchor("linwan", "a nurse")
        dest = tmp_path / "ff.png"
        f.first_frame("linwan", prompt="close-up night street", dest=dest)
        assert dest.is_file()
        assert pulid.calls[0]["ref"].endswith("linwan.png")     # 用了锚脸
        assert "close-up" in pulid.calls[0]["prompt"]

    def test_no_anchor_raises(self, tmp_path):
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=FakePuLID(),
                            bundle=Bundle(tmp_path, "b"))
        with pytest.raises(IdentityFoundryError):
            f.first_frame("nobody", prompt="x", dest=tmp_path / "o.png")


class TestFirstFrameWiring:
    def _backend(self, tmp_path, foundry):
        from render.first_frame import (
            SceneGroundedImageToVideoBackend, FirstFrameBackend)
        from render.budget import BudgetGate, confirm_yes
        gate = BudgetGate(max_units=10, confirm=confirm_yes,
                          allowed_durations_sec=tuple(range(1, 13)),
                          cost_table={("kling-v1", "std", d): 1.0 for d in range(1, 13)})
        return SceneGroundedImageToVideoBackend(
            first_frame_backend=FirstFrameBackend(FakeImg()),
            character_desc_by_id={"linwan": "a nurse", "zhao": "a debt collector"},
            foundry=foundry, budget=gate, bundle_root=tmp_path,
            api_key="k", base="http://x")

    def _req(self, tmp_path, chars):
        from render.backend import RenderRequest
        return RenderRequest(
            shot_id="EP1_SC1_SH1", task_id="t", story_id="s", bundle_id="b",
            contract_hash="h", model="kling-v1", tier="std", duration_sec=3.0,
            seed=1, prompt="x", camera={"shot_size": "近景"},
            reference_assets={"character_images": {c: "x.png" for c in chars},
                              "props": {}})

    def test_single_char_shot_uses_pulid(self, tmp_path):
        b = Bundle(tmp_path, "b")
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=FakePuLID(), bundle=b)
        f.mint_anchor("linwan", "a nurse")
        be = self._backend(tmp_path, f)
        be._reference_image_b64(self._req(tmp_path, ["linwan"]))
        assert be._ref_used["EP1_SC1_SH1"][0] == "pulid_anchor"     # 单人镜走PuLID
        assert b.path_for("08_submissions/first_frames/pulid_shot__EP1_SC1_SH1.png").is_file()

    def test_two_person_shot_skips_pulid(self, tmp_path):
        b = Bundle(tmp_path, "b")
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=FakePuLID(), bundle=b)
        f.mint_anchor("linwan", "a nurse")
        be = self._backend(tmp_path, f)
        be._reference_image_b64(self._req(tmp_path, ["linwan", "zhao"]))
        # 双人镜不走PuLID（避免对手被画成主角脸）→ 走②按镜首帧
        assert be._ref_used["EP1_SC1_SH1"][0] == "scene_first_frame"

    def _req_action(self, tmp_path):
        from render.backend import RenderRequest
        return RenderRequest(
            shot_id="EP1_SC1_SH2", task_id="t", story_id="s", bundle_id="b",
            contract_hash="h", model="kling-v1", tier="std", duration_sec=3.0,
            seed=1, prompt="x", camera={"shot_size": "近景"},
            performance={"acting_beats": [{"body": "掏出证据"}]},
            reference_assets={"character_images": {"linwan": "x.png"}, "props": {}})

    def test_action_shot_produces_tail_frame(self, tmp_path):
        b = Bundle(tmp_path, "b")
        pulid = FakePuLID()
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=pulid, bundle=b)
        f.mint_anchor("linwan", "a nurse")
        be = self._backend(tmp_path, f)
        req = self._req_action(tmp_path)
        be._reference_image_b64(req)                    # 起帧
        tail = be._tail_image_b64(req)                  # 尾帧（动作完成态）
        assert tail is not None
        assert b.path_for(
            "08_submissions/first_frames/pulid_tail_shot__EP1_SC1_SH2.png").is_file()
        # 尾帧提示词是动作完成态
        assert any("completed" in c["prompt"] for c in pulid.calls)

    def test_no_action_shot_still_gets_locked_tail(self, tmp_path):
        # [[kling-motion-drift-tail]]: 无动作镜也出锁定尾帧(同脸 settled 态)——只给首帧
        # Kling 动画中脸会漂走,尾帧把末端也锁住。故主角单人镜一律出尾帧。
        b = Bundle(tmp_path, "b")
        pulid = FakePuLID()
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=pulid, bundle=b)
        f.mint_anchor("linwan", "a nurse")
        be = self._backend(tmp_path, f)
        tail = be._tail_image_b64(self._req(tmp_path, ["linwan"]))   # 无 action_beats
        assert tail is not None
        assert b.path_for(
            "08_submissions/first_frames/pulid_tail_shot__EP1_SC1_SH1.png").is_file()

    def test_two_person_shot_no_tail(self, tmp_path):
        # 双人镜不走 foundry → 无 PuLID 尾帧(避免对手被画成主角脸)
        b = Bundle(tmp_path, "b")
        f = IdentityFoundry(image_backend=FakeImg(), pulid_backend=FakePuLID(), bundle=b)
        f.mint_anchor("linwan", "a nurse")
        be = self._backend(tmp_path, f)
        assert be._tail_image_b64(self._req(tmp_path, ["linwan", "zhao"])) is None
