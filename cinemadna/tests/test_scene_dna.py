"""SceneDNA 单元测试 (Phase 1)：检索 → 工单 → mock 生成 → Gate → 回流。"""

from __future__ import annotations

import pytest

from asset_brain.common.quad import Quad
from asset_brain.common.service_base import AssetServiceError
from asset_brain.common.store import AssetBrainStore
from asset_brain.facade import OUTCOME_BACKFLOWED, OUTCOME_REUSED, AssetBrainFacade
from asset_brain.scene_dna.gate import run_scene_gate
from asset_brain.scene_dna.service import SceneDNAService
from asset_brain.scene_dna.workorder import derive_tags, split_mood

SCENE = {
    "scene_id": "EP001_SC001",
    "location_description": "县城老旧出租屋",
    "time_of_day": "深夜",
    "mood": "压抑、疲惫",
    "camera_intent": "中近景，压迫感",
    "required_atoms": ["室内布局", "光线", "家具陈设", "窗户"],
    "spatial_needs": "可支持人物走动与特写",
}


def ctx(task_id: str = "task_scenedna_001") -> Quad:
    return Quad(
        story_id="drama_0005",
        bundle_id="bundle_20260723_001",
        task_id=task_id,
        asset_hash=None,
    )


class TestRequirementParsing:
    def test_split_mood(self):
        assert split_mood("压抑、疲惫") == ["压抑", "疲惫"]
        assert split_mood("") == []

    def test_parse_requirement_forces_natural(self):
        svc = SceneDNAService(AssetBrainStore())
        req = svc.parse_requirement({**SCENE, "must_be_natural": False}, ctx())
        assert req["must_be_natural"] is True
        assert req["scene_id"] == "EP001_SC001"

    def test_missing_scene_id_rejected(self):
        svc = SceneDNAService(AssetBrainStore())
        with pytest.raises(AssetServiceError):
            svc.parse_requirement({"location_description": "x"}, ctx())

    def test_derive_tags(self):
        svc = SceneDNAService(AssetBrainStore())
        req = svc.parse_requirement(SCENE, ctx())
        assert derive_tags(req) == ["县城老旧出租屋", "深夜", "压抑", "疲惫"]


class TestSceneGate:
    def _asset(self, **overrides):
        base = {
            "workorder_id": "wo_scenedna_0001",
            "asset_id": "scene_layout_x",
            "asset_type": "COMPOSED_LAYOUT",
            "atoms": [{"atom_type": a} for a in SCENE["required_atoms"]],
            "source_kind": "natural_atom_recompose",
            "sources_used": ["internal_db", "authorized_public"],
            "naturalness": 0.88,
            "script_match": 0.86,
            "layout_usability": 0.82,
            "rights_risk": 0.04,
        }
        base.update(overrides)
        return base

    def _req(self, **overrides):
        base = {
            "required_atoms": SCENE["required_atoms"],
            "must_be_natural": True,
        }
        base.update(overrides)
        return base

    def test_clean_asset_passes(self):
        r = run_scene_gate(
            gate_id="g1",
            workorder_id="wo1",
            generated_asset=self._asset(),
            requirement=self._req(),
        )
        assert r.passed is True
        assert r.human_review_required is False

    def test_unlicensed_source_fails(self):
        r = run_scene_gate(
            gate_id="g2",
            workorder_id="wo1",
            generated_asset=self._asset(sources_used=["scraped_web"]),
            requirement=self._req(),
        )
        assert r.passed is False
        assert r.scores["rights_risk"] > 0.3

    def test_pure_ai_generation_fails_natural_requirement(self):
        """守住"自然场景原子优先"——退化成纯 AI 生图必须判失败。"""
        r = run_scene_gate(
            gate_id="g3",
            workorder_id="wo1",
            generated_asset=self._asset(source_kind="pure_ai_generated"),
            requirement=self._req(),
        )
        assert r.passed is False
        assert any("自然场景原子优先" in i for i in r.issues)

    def test_missing_atoms_fails(self):
        r = run_scene_gate(
            gate_id="g4",
            workorder_id="wo1",
            generated_asset=self._asset(atoms=[{"atom_type": "光线"}]),
            requirement=self._req(),
        )
        assert r.passed is False
        assert any("缺少剧本要求的场景原子" in i for i in r.issues)

    def test_low_naturalness_fails(self):
        r = run_scene_gate(
            gate_id="g5",
            workorder_id="wo1",
            generated_asset=self._asset(naturalness=0.4),
            requirement=self._req(),
        )
        assert r.passed is False


class TestSceneFlow:
    def test_full_loop_backflows(self):
        fac = AssetBrainFacade()
        res = fac.request_scene(SCENE, ctx())
        assert res["outcome"] == OUTCOME_BACKFLOWED
        assert res["asset_ids"]

        wo = fac.get_workorder_status(res["workorder_id"])
        assert wo["status"] == "BACKFLOWED"
        assert wo["workorder_type"] == "SCENE"
        assert wo["backflow_record_id"] == res["backflow_record_id"]

        record = fac.store.backflow_records[res["backflow_record_id"]]
        assert record["schema_version"] == "cinemadna.scene_backflow.v1"
        assert record["tags"] == ["县城老旧出租屋", "深夜", "压抑", "疲惫"]
        assert record["asset_hash"].startswith("sha256:")
        assert record["reusable"] is True

    def test_second_identical_scene_reuses_library(self):
        """回流的价值：同一场景第二次请求直接命中，不再建工单。"""
        fac = AssetBrainFacade()
        fac.request_scene(SCENE, ctx())
        res2 = fac.request_scene(SCENE, ctx("task_scenedna_002"))
        assert res2["outcome"] == OUTCOME_REUSED
        assert res2["similarity_scores"][0] == 1.0
        assert len(fac.list_workorders(workorder_type="SCENE")) == 1

    def test_different_scene_does_not_false_hit(self):
        fac = AssetBrainFacade()
        fac.request_scene(SCENE, ctx())
        other = {
            "scene_id": "EP001_SC002",
            "location_description": "医院走廊",
            "time_of_day": "清晨",
            "mood": "焦灼、无助",
            "required_atoms": ["走廊纵深", "冷白光线"],
        }
        res = fac.request_scene(other, ctx("task_scenedna_003"))
        assert res["outcome"] == OUTCOME_BACKFLOWED

    def test_create_workorder_refused_when_already_found(self):
        svc = SceneDNAService(AssetBrainStore())
        req = svc.parse_requirement(SCENE, ctx())
        with pytest.raises(AssetServiceError):
            svc.create_workorder(req, {"found": True}, ctx())

    def test_mock_generate_is_deterministic(self):
        store = AssetBrainStore()
        svc = SceneDNAService(store)
        req = svc.parse_requirement(SCENE, ctx())
        wo = svc.create_workorder(req, {"found": False}, ctx())
        a1 = svc.mock_generate(wo)
        a2 = svc.mock_generate(wo)  # 仍在 RUNNING，可重复生成
        assert a1["asset_hash"] == a2["asset_hash"]
