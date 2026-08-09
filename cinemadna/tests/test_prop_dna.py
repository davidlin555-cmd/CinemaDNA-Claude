"""PropDNA 单元测试 (Phase 1)：状态版本、跨镜头连续性、Gate、回流。"""

from __future__ import annotations

import pytest

from cinemadna.asset_brain.common.quad import Quad
from cinemadna.asset_brain.common.service_base import AssetServiceError
from cinemadna.asset_brain.common.store import AssetBrainStore
from cinemadna.asset_brain.facade import OUTCOME_BACKFLOWED, OUTCOME_REUSED, AssetBrainFacade
from cinemadna.asset_brain.prop_dna.gate import run_prop_gate
from cinemadna.asset_brain.prop_dna.service import PropDNAService

PROP = {
    "prop_id": "prop_hospital_bill",
    "name": "医院缴费单",
    "description": "旧的纸质医院缴费单",
    "states_needed": ["完整", "被攥皱", "放在桌上"],
    "story_function": "建立女主经济压力",
    "continuity_critical": True,
}

TIMELINE = [
    {"shot_id": "EP001_SC001_SH003", "state": "完整"},
    {"shot_id": "EP001_SC001_SH007", "state": "被攥皱"},
    {"shot_id": "EP001_SC002_SH002", "state": "放在桌上"},
]


def ctx(task_id: str = "task_propdna_001") -> Quad:
    return Quad(
        story_id="drama_0005",
        bundle_id="bundle_20260723_001",
        task_id=task_id,
        asset_hash=None,
    )


class TestPropRequirement:
    def test_parse_requirement(self):
        svc = PropDNAService(AssetBrainStore())
        req = svc.parse_requirement(PROP, ctx())
        assert req["states_needed"] == ["完整", "被攥皱", "放在桌上"]
        assert req["continuity_critical"] is True
        assert req["must_be_natural"] is True

    def test_missing_prop_id_rejected(self):
        svc = PropDNAService(AssetBrainStore())
        with pytest.raises(AssetServiceError):
            svc.parse_requirement({"name": "无 id 道具"}, ctx())

    def test_stateless_prop_gets_default_state(self):
        svc = PropDNAService(AssetBrainStore())
        req = svc.parse_requirement({"prop_id": "prop_cup", "name": "杯子"}, ctx())
        assert req["states_needed"] == ["默认"]
        assert req["continuity_critical"] is False


class TestPropGate:
    def _asset(self, **overrides):
        base = {
            "prop_id": "prop_hospital_bill",
            "asset_ids_by_state": {s: f"a_{i}" for i, s in enumerate(PROP["states_needed"])},
            "sources_used": ["internal_db", "synthetic"],
            "real_brand_marks": [],
            "naturalness": 0.88,
            "script_match": 0.85,
            "rights_risk": 0.03,
        }
        base.update(overrides)
        return base

    def _req(self, **overrides):
        base = {
            "states_needed": PROP["states_needed"],
            "continuity_critical": True,
        }
        base.update(overrides)
        return base

    def test_full_state_coverage_passes(self):
        r = run_prop_gate(
            gate_id="g1", workorder_id="wo1",
            prop_asset=self._asset(), requirement=self._req(),
        )
        assert r.passed is True
        assert r.scores["state_consistency"] == 1.0
        assert r.human_review_required is False

    def test_missing_state_fails_for_continuity_critical_prop(self):
        r = run_prop_gate(
            gate_id="g2", workorder_id="wo1",
            prop_asset=self._asset(asset_ids_by_state={"完整": "a_0"}),
            requirement=self._req(),
        )
        assert r.passed is False
        assert r.scores["state_consistency"] < 1.0
        assert any("连续性关键道具" in i for i in r.issues)

    def test_real_brand_mark_raises_rights_risk(self):
        r = run_prop_gate(
            gate_id="g3", workorder_id="wo1",
            prop_asset=self._asset(real_brand_marks=["某品牌 logo"]),
            requirement=self._req(),
        )
        assert r.passed is False
        assert r.scores["rights_risk"] >= 0.9


class TestPropFlow:
    def test_full_loop_backflows_with_continuity(self):
        fac = AssetBrainFacade()
        res = fac.request_prop(PROP, ctx(), state_timeline=TIMELINE)
        assert res["outcome"] == OUTCOME_BACKFLOWED
        assert len(res["asset_ids"]) == 3

        record = fac.store.backflow_records[res["backflow_record_id"]]
        assert record["schema_version"] == "cinemadna.prop_backflow.v1"
        assert record["continuity_supported"] is True
        assert set(record["asset_ids_by_state"]) == set(PROP["states_needed"])

    def test_manage_continuity_binds_shots_to_versions(self):
        store = AssetBrainStore()
        svc = PropDNAService(store)
        req = svc.parse_requirement(PROP, ctx())
        wo = svc.create_workorder(req, {"found": False}, ctx())
        asset = svc.mock_synthesize(wo)
        asset = svc.manage_continuity(asset, TIMELINE)

        assert asset["continuity_resolved"] is True
        assert all(e["asset_id"] for e in asset["state_timeline"])
        assert asset["state_timeline"][0]["shot_id"] == "EP001_SC001_SH003"

    def test_unknown_state_in_timeline_marked_unresolved(self):
        store = AssetBrainStore()
        svc = PropDNAService(store)
        req = svc.parse_requirement(PROP, ctx())
        wo = svc.create_workorder(req, {"found": False}, ctx())
        asset = svc.mock_synthesize(wo)
        asset = svc.manage_continuity(
            asset, TIMELINE + [{"shot_id": "SH999", "state": "被烧毁"}]
        )
        assert asset["continuity_resolved"] is False
        assert asset["state_timeline"][-1]["asset_id"] is None

    def test_second_request_reuses_library(self):
        fac = AssetBrainFacade()
        fac.request_prop(PROP, ctx(), state_timeline=TIMELINE)
        res2 = fac.request_prop(PROP, ctx("task_propdna_002"))
        assert res2["outcome"] == OUTCOME_REUSED

    def test_reuse_refused_when_state_missing(self):
        """已有道具但缺新状态 → 不算命中，必须补建工单。"""
        fac = AssetBrainFacade()
        fac.request_prop(PROP, ctx(), state_timeline=TIMELINE)
        extended = {**PROP, "states_needed": PROP["states_needed"] + ["被烧毁"]}
        search = fac.prop.search_internal(
            fac.prop.parse_requirement(extended, ctx()), ctx()
        )
        assert search["found"] is False
        assert search["missing_states"] == ["被烧毁"]
