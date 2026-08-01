"""Phase 1 端到端验收测试

用一份假 shooting_script.json 跑通完整闭环：
    查库 → 缺失建 Workorder → mock 生成 → Gate → 回流
并验证接口文档 §6 的九条最小可运行验收标准，外加"多故事并行"这一最高优先级原则。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from asset_brain.common.service_base import AssetServiceError
from asset_brain.facade import (
    OUTCOME_BACKFLOWED,
    OUTCOME_PENDING_HUMAN_REVIEW,
    OUTCOME_REJECTED,
    OUTCOME_REUSED,
    AssetBrainFacade,
)
from asset_brain.scene_dna.service import SceneDNAService

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "mocks" / "sample_shooting_script.json"


@pytest.fixture(scope="module")
def script() -> dict:
    return json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))


def quad(story_id: str, bundle_id: str, task_id: str) -> Quad:
    return Quad(
        story_id=story_id, bundle_id=bundle_id, task_id=task_id, asset_hash=None
    )


def run_script(fac: AssetBrainFacade, script: dict, story_id: str, bundle_id: str):
    """把一份剧本的全部场景 / 人物 / 道具跑完，返回各自结果。"""
    results = {"scene": [], "identity": [], "prop": []}

    for ep in script["episodes"]:
        for i, scene in enumerate(ep["scenes"]):
            results["scene"].append(
                fac.request_scene(
                    scene, quad(story_id, bundle_id, f"task_scenedna_{i:03d}")
                )
            )
    for i, char in enumerate(script["characters"]):
        results["identity"].append(
            fac.request_identity(
                char, quad(story_id, bundle_id, f"task_identitydna_{i:03d}")
            )
        )
    for i, prop in enumerate(script["props"]):
        results["prop"].append(
            fac.request_prop(prop, quad(story_id, bundle_id, f"task_propdna_{i:03d}"))
        )
    return results


class TestPhase1Acceptance:
    def test_full_closed_loop_from_fake_script(self, script, tmp_path):
        """验收 #1~#7、#9：一份假剧本跑通完整闭环并落 Bundle。"""
        bundle = Bundle(tmp_path, "bundle_20260723_001")
        fac = AssetBrainFacade(bundle=bundle)

        res = run_script(fac, script, "drama_0005", "bundle_20260723_001")

        # 三类资产全部走完 生成 → Gate → 回流
        assert [r["outcome"] for r in res["scene"]] == [OUTCOME_BACKFLOWED] * 2
        assert [r["outcome"] for r in res["identity"]] == [OUTCOME_BACKFLOWED]
        assert [r["outcome"] for r in res["prop"]] == [OUTCOME_BACKFLOWED]

        # 三种类型的工单都创建过（验收 #1）
        types = {w["workorder_type"] for w in fac.list_workorders()}
        assert types == {"SCENE", "IDENTITY", "PROP"}

        # 每张工单都有 Gate Report 与 Backflow（验收 #4 #6）
        for w in fac.list_workorders():
            assert w["status"] == "BACKFLOWED"
            assert w["gate_report"] is not None
            assert w["gate_report"]["passed"] is True
            assert w["backflow_record_id"]
            # 四元组完整（验收 #1）
            assert w["story_id"] and w["bundle_id"] and w["task_id"]

        # 三大库都已沉淀资产
        stats = fac.stats()
        assert stats["scene_atoms"] == 2
        assert stats["characters"] == 1
        assert stats["props"] == 1
        assert stats["workorders"] == 4
        assert stats["backflow_records"] == 4

    def test_bundle_layout_on_disk(self, script, tmp_path):
        """验收 #7：所有记录正确落在 Active Production Bundle 标准目录内。"""
        bundle = Bundle(tmp_path, "bundle_20260723_001")
        fac = AssetBrainFacade(bundle=bundle)
        run_script(fac, script, "drama_0005", "bundle_20260723_001")

        root = bundle.root
        assert (root / "01_script").is_dir()  # 14 目录已建
        assert len([d for d in root.iterdir() if d.is_dir()]) == 14

        assert list((root / "08_submissions" / "workorders").glob("*.json"))
        assert list((root / "07_payloads" / "scene").glob("*.json"))
        assert list((root / "07_payloads" / "identity").glob("*.json"))
        assert list((root / "11_review" / "scene_gate").glob("*.json"))
        assert list((root / "11_review" / "identity_gate").glob("*.json"))
        assert list((root / "12_asset_backflow" / "scene").glob("*.json"))
        assert (root / "02_cast" / "char_linwan" / "master_pack.json").exists()
        assert (root / "04_props" / "prop_hospital_bill" / "prop.json").exists()
        assert (root / "03_scene" / "EP001_SC001" / "layout.json").exists()

        # 落盘内容可读且结构正确
        pack = bundle.read_json("02_cast/char_linwan/master_pack.json")
        assert pack["schema_version"] == "cinemadna.character_master_pack.v1"
        assert pack["gate_passed"] is True

        # 盘上的 Gate Report 必须记录了决策人与决策时间（可审计）
        gate_file = next((root / "11_review" / "identity_gate").glob("*.json"))
        gate = json.loads(gate_file.read_text(encoding="utf-8"))
        assert gate["decided_by"] == "auto_gate"
        assert gate["decided_at"]

    def test_bundle_rejects_out_of_bundle_write(self, tmp_path):
        """Bundle 隔离：不允许写到标准目录之外。"""
        bundle = Bundle(tmp_path, "bundle_x").ensure()
        with pytest.raises(Exception):
            bundle.write_json("../escape.json", {"x": 1})
        with pytest.raises(Exception):
            bundle.write_json("99_unknown/x.json", {"x": 1})

    def test_second_story_reuses_first_story_assets(self, script, tmp_path):
        """回流的最终价值：第二部剧直接复用第一部沉淀的资产。"""
        fac = AssetBrainFacade()
        run_script(fac, script, "drama_0005", "bundle_20260723_001")
        before = fac.stats()["workorders"]

        res2 = run_script(fac, script, "drama_0006", "bundle_20260723_002")
        outcomes = [
            r["outcome"]
            for group in res2.values()
            for r in group
        ]
        assert outcomes == [OUTCOME_REUSED] * 4
        # 第二部剧一张新工单都不需要
        assert fac.stats()["workorders"] == before

    def test_parallel_stories_are_isolated(self, script, tmp_path):
        """最高优先级原则：多故事并行，工单按 story_id 隔离查询。"""
        fac = AssetBrainFacade()
        # 两部剧共用同一份剧本内容，但资产各自独立（用不同 story/bundle）
        run_script(fac, script, "drama_0005", "bundle_20260723_001")

        # 第二部剧换一套场景需求，确保会真正建单
        other_script = {
            "episodes": [
                {
                    "scenes": [
                        {
                            "scene_id": "EP001_SC001",
                            "location_description": "海边渔村",
                            "time_of_day": "黄昏",
                            "mood": "辽阔、孤独",
                            "required_atoms": ["海岸线", "夕照", "渔船"],
                        }
                    ]
                }
            ],
            "characters": [
                {
                    "character_id": "char_zhaoming",
                    "name": "赵明",
                    "gender": "male",
                    "role_type": "主角",
                }
            ],
            "props": [{"prop_id": "prop_fishing_net", "name": "渔网"}],
        }
        run_script(fac, other_script, "drama_0006", "bundle_20260723_002")

        a = fac.list_workorders(story_id="drama_0005")
        b = fac.list_workorders(story_id="drama_0006")
        assert len(a) == 4 and len(b) == 3
        assert {w["bundle_id"] for w in a} == {"bundle_20260723_001"}
        assert {w["bundle_id"] for w in b} == {"bundle_20260723_002"}
        # 互不干扰，全部完成
        assert all(w["status"] == "BACKFLOWED" for w in a + b)

    def test_status_query_interface(self, script):
        """验收 #8：提供状态查询接口，供后续 Web 看板使用。"""
        fac = AssetBrainFacade()
        res = run_script(fac, script, "drama_0005", "bundle_20260723_001")
        wid = res["scene"][0]["workorder_id"]

        status = fac.get_workorder_status(wid)
        assert status["workorder_id"] == wid
        assert status["schema_version"] == "cinemadna.workorder.v1"

        assert fac.list_workorders(status="BACKFLOWED")
        assert fac.list_workorders(status="FAILED") == []
        with pytest.raises(AssetServiceError):
            fac.get_workorder_status("wo_not_exist")


class TestHumanReviewPath:
    """Gate 通过但需人工复核时的挂起 → 审批闭环。"""

    @staticmethod
    def _borderline_scene(monkeypatch):
        """让 mock 生成的场景带中等版权风险，从而触发人工复核。"""
        original = SceneDNAService.mock_generate

        def patched(self, workorder):
            asset = original(self, workorder)
            asset["rights_risk"] = 0.22  # 低于失败线 0.30，高于自动通过线 0.15
            return asset

        monkeypatch.setattr(SceneDNAService, "mock_generate", patched)

    def test_pending_then_human_approves(self, script, monkeypatch):
        self._borderline_scene(monkeypatch)
        fac = AssetBrainFacade()
        scene = script["episodes"][0]["scenes"][0]
        res = fac.request_scene(scene, quad("drama_0005", "bundle_1", "task_s_001"))

        assert res["outcome"] == OUTCOME_PENDING_HUMAN_REVIEW
        assert fac.get_workorder_status(res["workorder_id"])["status"] == "GATE_REVIEW"

        done = fac.approve_gate(
            res["gate_id"], {"approved": True, "decided_by": "human_lin"}
        )
        assert done["outcome"] == OUTCOME_BACKFLOWED
        wo = fac.get_workorder_status(res["workorder_id"])
        assert wo["status"] == "BACKFLOWED"
        assert wo["gate_report"]["decided_by"] == "human_lin"

    def test_pending_then_human_rejects(self, script, monkeypatch):
        self._borderline_scene(monkeypatch)
        fac = AssetBrainFacade()
        scene = script["episodes"][0]["scenes"][0]
        res = fac.request_scene(scene, quad("drama_0005", "bundle_1", "task_s_001"))

        done = fac.approve_gate(
            res["gate_id"], {"approved": False, "decided_by": "human_lin"}
        )
        assert done["outcome"] == OUTCOME_REJECTED
        assert fac.get_workorder_status(res["workorder_id"])["status"] == "REJECTED"
        # 未过审的资产绝不入库（防资产污染）
        assert fac.store.scene_atoms == {}

    def test_unknown_gate_id_rejected(self):
        fac = AssetBrainFacade()
        with pytest.raises(AssetServiceError):
            fac.approve_gate("gate_not_exist", {"approved": True})
