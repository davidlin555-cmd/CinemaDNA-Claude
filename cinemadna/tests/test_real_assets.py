"""真实资产 (Phase A) 测试 —— 图像后端、IdentityDNA 真实人脸、Kling image2video

全程 mock HTTP：不发真实请求、不花钱。验证预算闸前置、红线对纯合成脸放行、
真实人脸串到 image2video、无参考图时拒绝"假装 image2video"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from asset_brain.common.store import AssetBrainStore
from asset_brain.facade import (
    OUTCOME_BACKFLOWED,
    OUTCOME_PENDING_HUMAN_REVIEW,
    AssetBrainFacade,
)
from asset_brain.identity_dna.gate import (
    BLOCK_INSUFFICIENT_FUSION_SOURCES,
    BLOCK_SINGLE_REAL_PERSON,
    detect_single_real_clone,
    run_identity_gate,
)
from assets_real.image_backend import ImageGenError, TogetherImageBackend
from render.budget import BudgetGate, BudgetNotArmedError, confirm_yes

CHAR = {"character_id": "char_linwan", "name": "林晚", "age_range": "25-28",
        "gender": "female", "ethnicity_preference": "东亚/中国",
        "personality_keywords": ["压抑", "坚韧"], "role_type": "主角"}


def ctx(task="task_identitydna_001"):
    return Quad(story_id="drama_0005", bundle_id="bundle_20260724_001",
                task_id=task, asset_hash=None)


class FakeImageHttp:
    """模拟 Together 图像 API：返回一张够大的 base64 假 PNG（>500 字节）。"""
    import base64 as _b64
    PNG = _b64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1200).decode()

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, body))
        return 200, {"data": [{"b64_json": self.PNG, "seed": 42}]}


def image_backend(http=None, budget=None):
    return TogetherImageBackend(
        budget=budget or BudgetGate(max_units=1.0, confirm=confirm_yes),
        api_key="test-key", http=http or FakeImageHttp())


# ---------------------------------------------------------------------------
# 图像后端
# ---------------------------------------------------------------------------


class TestImageBackend:
    def test_requires_budget_gate(self):
        with pytest.raises(ImageGenError):
            TogetherImageBackend(budget=None, api_key="k")  # type: ignore

    def test_disarmed_budget_blocks(self, tmp_path):
        http = FakeImageHttp()
        b = TogetherImageBackend(budget=BudgetGate(), api_key="k", http=http)
        with pytest.raises(BudgetNotArmedError):
            b.generate(prompt="p", dest=tmp_path / "f.png", item_id="c")
        assert http.calls == []          # 没发请求

    def test_generate_writes_png_and_records(self, tmp_path):
        g = BudgetGate(max_units=1.0, confirm=confirm_yes)
        b = image_backend(budget=g)
        r = b.generate(prompt="一位东亚女性", dest=tmp_path / "face.png",
                       item_id="char_linwan")
        assert (tmp_path / "face.png").exists()
        assert r.bytes > 0 and r.is_real_media is True
        assert g.spent_units > 0          # 成功后记账


# ---------------------------------------------------------------------------
# 红线：纯合成脸放行，真人裸克隆仍拦
# ---------------------------------------------------------------------------


class TestSyntheticFacePassesRedline:
    def test_single_synthetic_source_is_ok(self):
        """纯合成脸（1 个合成来源、非真人）→ 不触发"融合源不足"。"""
        from asset_brain.identity_dna.fusion_mock import synthetic_face_source
        pack = {"fusion_sources": synthetic_face_source("wo1", "02_cast/x/face.png"),
                "public_figure_matches": []}
        assert detect_single_real_clone(pack) == []

    def test_single_real_source_still_blocked(self):
        """1 个真人来源 → 仍判"融合源不足"（防裸克隆）。"""
        pack = {"fusion_sources": [{"source_id": "s", "license": "authorized",
                "is_real_person": True, "weight": 1.0, "similarity_to_result": 0.9}]}
        blocks = detect_single_real_clone(pack)
        assert BLOCK_INSUFFICIENT_FUSION_SOURCES in blocks
        assert BLOCK_SINGLE_REAL_PERSON in blocks

    def test_synthetic_gate_full_pass(self):
        from asset_brain.identity_dna.fusion_mock import synthetic_face_source
        pack = {"workorder_id": "wo1", "naturalness": 0.9, "ethnicity_match": 0.9,
                "family_consistency": 1.0, "age_line": {},
                "fusion_sources": synthetic_face_source("wo1", "x.png"),
                "public_figure_matches": []}
        req = {"must_multi_face_fusion": True, "forbid_single_real_clone": True,
               "need_age_line": False, "need_family": False}
        report = run_identity_gate(gate_id="g", workorder_id="wo1",
                                   identity_pack=pack, requirement=req)
        assert report.passed is True and report.hard_blocks == []


# ---------------------------------------------------------------------------
# IdentityDNA 真实人脸端到端（mock HTTP）
# ---------------------------------------------------------------------------


class TestIdentityRealFace:
    def test_real_face_holds_for_review_then_backflows(self, tmp_path):
        bundle = Bundle(tmp_path, "bundle_20260724_001").ensure()
        fac = AssetBrainFacade(bundle=bundle, image_backend=image_backend())
        res = fac.request_identity(CHAR, ctx())

        # 生成真实人脸 → 必过人脸审核（肖像/版权），先挂起等人工，不自动回流
        assert res["outcome"] == OUTCOME_PENDING_HUMAN_REVIEW
        assert "char_linwan" not in fac.store.character_registry
        # 真实人脸图已落地（等人审）
        assert bundle.path_for("02_cast/char_linwan/face.png").exists()

        # 人工批准 → 回流入库
        fac.approve_gate(res["gate_id"], {"approved": True, "decided_by": "human"})
        pack = fac.store.character_registry["char_linwan"]
        assert pack["is_real_image"] is True
        assert pack["base_face_image"] == "02_cast/char_linwan/face.png"
        assert pack["generation_method"] == "synthetic_text2image"

    def test_without_backend_stays_mock(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        fac = AssetBrainFacade(bundle=bundle)           # 无 image_backend
        fac.request_identity(CHAR, ctx())
        pack = fac.store.character_registry["char_linwan"]
        assert pack["is_real_image"] is False
        assert pack["base_face_image"] is None


# ---------------------------------------------------------------------------
# SceneDNA 真实场景出图（mock HTTP）
# ---------------------------------------------------------------------------


SCENE_REQ = {
    "scene_id": "EP001_SC001",
    "location_description": "县城老旧出租屋",
    "time_of_day": "深夜",
    "mood": "压抑、疲惫",
    "camera_intent": "中近景，压迫感",
    "required_atoms": ["室内布局", "光线", "家具陈设"],
    "spatial_needs": "可支持人物走动与特写",
}

# 物理道具（非屏幕内容）→ 走 FLUX 文生图；屏幕道具见 test_screen_props.py
PROP_REQ = {
    "prop_id": "prop_old_letter",
    "name": "旧信纸",
    "description": "泛黄的手写旧信纸",
    "states_needed": ["完整", "被攥皱"],
    "story_function": "建立情感线索",
    "continuity_critical": True,
}


class TestExtensionCorrection:
    """FLUX 常返 JPEG，落成 .png 会"扩展名撒谎"；助手按真实字节纠正后缀。"""

    def test_jpeg_bytes_corrected_to_jpg(self, tmp_path):
        import base64 as _b64
        from assets_real.asset_image import generate_real_image
        bundle = Bundle(tmp_path, "b1").ensure()
        jpeg = _b64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 2000).decode()

        class JpegHttp:
            def __call__(self, method, url, headers, body):
                return 200, {"data": [{"b64_json": jpeg}]}

        res = generate_real_image(
            image_backend(http=JpegHttp()), bundle,
            relpath="03_scene/x/scene.png", prompt="p", item_id="scene_x")
        assert res["ok"] is True
        assert res["relpath"] == "03_scene/x/scene.jpg"      # 后缀被纠正
        assert res["abspath"].endswith(".jpg")
        assert bundle.path_for("03_scene/x/scene.jpg").exists()
        assert not bundle.path_for("03_scene/x/scene.png").exists()

    def test_png_bytes_keep_png(self, tmp_path):
        from assets_real.asset_image import generate_real_image
        bundle = Bundle(tmp_path, "b1").ensure()
        res = generate_real_image(
            image_backend(), bundle,
            relpath="03_scene/x/scene.png", prompt="p", item_id="scene_x")
        assert res["relpath"] == "03_scene/x/scene.png"      # 本就是 PNG，不动


class TestSceneRealImage:
    def test_scene_backflows_with_real_image(self, tmp_path):
        bundle = Bundle(tmp_path, "bundle_20260724_001").ensure()
        fac = AssetBrainFacade(bundle=bundle, image_backend=image_backend())
        res = fac.request_scene(SCENE_REQ, ctx("task_scenedna_001"))

        assert res["outcome"] == OUTCOME_BACKFLOWED
        recs = [r for r in fac.store.scene_atoms.values() if r.get("is_real_image")]
        assert recs, "应有一条带真实场景图的场景记录"
        rec = recs[0]
        assert rec["scene_image"] == "03_scene/EP001_SC001/scene.png"
        assert bundle.path_for("03_scene/EP001_SC001/scene.png").exists()
        # 绝对路径供跨剧回流复用
        assert Path(rec["scene_image_abspath"]).is_absolute()
        assert Path(rec["scene_image_abspath"]).is_file()

    def test_scene_without_backend_stays_mock(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        fac = AssetBrainFacade(bundle=bundle)           # 无 image_backend
        fac.request_scene(SCENE_REQ, ctx("task_scenedna_002"))
        rec = next(iter(fac.store.scene_atoms.values()))
        assert bool(rec.get("is_real_image")) is False
        assert rec.get("scene_image") is None


class TestPropRealImage:
    def test_prop_backflows_with_real_image_per_state(self, tmp_path):
        bundle = Bundle(tmp_path, "bundle_20260724_001").ensure()
        fac = AssetBrainFacade(bundle=bundle, image_backend=image_backend(
            budget=BudgetGate(max_units=5.0, confirm=confirm_yes)))
        timeline = [
            {"shot_id": "EP001_SC001_SH001", "state": "完整"},
            {"shot_id": "EP001_SC001_SH005", "state": "被攥皱"},
        ]
        res = fac.request_prop(PROP_REQ, ctx("task_propdna_001"),
                               state_timeline=timeline)

        assert res["outcome"] == OUTCOME_BACKFLOWED
        rec = fac.store.prop_library["prop_old_letter"]
        assert bool(rec.get("is_real_image")) is True
        imgs = rec.get("prop_images_by_state") or {}
        # 每个声明的状态都出一张真实图
        assert set(imgs) >= {"完整", "被攥皱"}
        for state, relpath in imgs.items():
            assert bundle.path_for(relpath).exists(), f"{state} 缺真实道具图"
        # 绝对路径供跨剧回流复用
        abs_imgs = rec.get("prop_images_abspath") or {}
        assert all(Path(p).is_absolute() for p in abs_imgs.values())

    def test_prop_without_backend_stays_mock(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        fac = AssetBrainFacade(bundle=bundle)           # 无 image_backend
        fac.request_prop(PROP_REQ, ctx("task_propdna_002"),
                         state_timeline=[{"shot_id": "s1", "state": "完整"}])
        rec = fac.store.prop_library["prop_old_letter"]
        assert bool(rec.get("is_real_image")) is False
        assert not (rec.get("prop_images_by_state") or {})


# ---------------------------------------------------------------------------
# Kling image2video 用真实人脸参考
# ---------------------------------------------------------------------------


class TestKlingImageToVideo:
    def _contract_with_face(self, face_rel, bundle):
        from director.shot_contract import SHOT_MEDIUM, new_shot_contract
        from performance.service import PerformanceDNAService
        script = {"script_id": "s", "characters": [{"character_id": "char_a",
                  "name": "林", "personality_keywords": ["压抑"], "role_type": "主角"}],
                  "props": [], "episodes": [{"episode_id": "EP001", "scenes": [
                  {"scene_id": "EP001_SC001", "beat_type": "CONFLICT",
                   "spatial_needs": "x", "camera_intent": "x",
                   "characters": ["char_a"], "props": [], "prop_states": {}}]}]}
        c = new_shot_contract(
            shot_id="SH1", scene_id="EP001_SC001", episode_id="EP001", order=1,
            shot_type=SHOT_MEDIUM,
            quad=Quad(story_id="drama_0001", bundle_id=bundle.bundle_id,
                      task_id="t1", asset_hash=None),
            camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "x"},
            duration_sec=5.0, emotion={"beat": "CONFLICT", "intensity": 0.8, "mood": "x"},
            scene_asset={"asset_id": "sc", "asset_hash": "sha256:" + "a"*64,
                         "gate_passed": True, "tags": []},
            character_assets=[{"character_id": "char_a", "master_pack_id": "cmp",
                               "asset_hash": "sha256:" + "b"*64, "gate_passed": True,
                               "base_face_image": face_rel}],
            prop_assets=[], dialogue=[{"character_id": "char_a", "line": "x"}])
        PerformanceDNAService().run([c], shooting_script=script)
        return c

    def test_image2video_uses_face_reference(self, tmp_path):
        from render.budget import BudgetGate
        from render.kling_backend import KlingImageToVideoBackend
        from render.service import RenderBrainService
        from render.registry import BackendRegistry
        from render.router import route_shot

        root = tmp_path / "bundles"
        bundle = Bundle(root, "bundle_1").ensure()
        # 放一张假人脸图
        face_rel = "02_cast/char_a/face.png"
        bundle.path_for(face_rel).parent.mkdir(parents=True, exist_ok=True)
        bundle.path_for(face_rel).write_bytes(b"\x89PNG\r\n" + b"\x00" * 200)

        calls = {"submit": 0}
        def http(method, url, headers, body):
            if method == "POST":
                calls["submit"] += 1
                assert "image2video" in url
                assert body.get("image")          # 带了参考图 base64
                return 200, {"code": 0, "data": {"task_id": "job1"}}
            return 200, {"code": 0, "data": {"task_status": "succeed",
                "task_result": {"videos": [{"url": "https://cdn/v.mp4"}]}}}

        g = BudgetGate(max_units=100, confirm=confirm_yes)
        backend = KlingImageToVideoBackend(budget=g, api_key="k",
                    base="https://api-test.example", http=http, bundle_root=root)
        backend.download = lambda url, dest: dest.write_bytes(b"\x00" * 5000)

        c = self._contract_with_face(face_rel, bundle)
        svc = RenderBrainService(registry=BackendRegistry(default=backend),
                                 bundle=bundle)
        result = svc.render_all([c])
        assert result.rendered, result.failed
        assert calls["submit"] == 1
        assert result.rendered[0]["backend"] == "kling-image2video"

    def test_image2video_without_face_is_refused(self, tmp_path):
        from render.budget import BudgetGate
        from render.kling_backend import KlingImageToVideoBackend
        from render.service import RenderBrainService
        from render.registry import BackendRegistry

        bundle = Bundle(tmp_path, "bundle_1").ensure()
        c = self._contract_with_face(None, bundle)      # 无真实人脸图
        g = BudgetGate(max_units=100, confirm=confirm_yes)
        backend = KlingImageToVideoBackend(budget=g, api_key="k",
                    base="https://x", http=lambda *a: (200, {"code": 0}))
        svc = RenderBrainService(registry=BackendRegistry(default=backend),
                                 bundle=bundle)
        result = svc.render_all([c])
        assert not result.rendered
        assert result.failed and "真实人脸参考图" in result.failed[0]["reason"]
        assert g.spent_units == 0                        # 没出图不扣费
