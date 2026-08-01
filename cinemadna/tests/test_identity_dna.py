"""IdentityDNA 单元测试 (Phase 1) —— 重点：「禁止单一真人克隆」硬性拦截

覆盖三层防线：
1. gate.detect_single_real_clone —— 纯函数级红线检测
2. IdentityDNAService.run_gate  —— Gate 结论驱动工单状态
3. facade.approve_gate / service.backflow —— 硬拦截不可人工推翻、不可回流
"""

from __future__ import annotations

import pytest

from asset_brain.common.quad import Quad
from asset_brain.common.service_base import AssetServiceError
from asset_brain.common.store import AssetBrainStore
from asset_brain.common.workorder import WorkorderStatus
from asset_brain.facade import (
    OUTCOME_BACKFLOWED,
    OUTCOME_REJECTED_HARD_BLOCK,
    OUTCOME_REUSED,
    AssetBrainFacade,
)
from asset_brain.identity_dna import fusion_mock
from asset_brain.identity_dna.gate import (
    BLOCK_FUSION_POLICY_DISABLED,
    BLOCK_INSUFFICIENT_FUSION_SOURCES,
    BLOCK_PUBLIC_FIGURE,
    BLOCK_SINGLE_REAL_PERSON,
    BLOCK_UNLICENSED_SOURCE,
    MAX_SINGLE_SOURCE_SIMILARITY,
    HardBlockOverrideError,
    IdentityGateReport,
    compute_likeness_risk,
    detect_single_real_clone,
    run_identity_gate,
)
from asset_brain.identity_dna.service import IdentityDNAService

CHAR = {
    "character_id": "char_linwan",
    "name": "林晚",
    "age_range": "25-28",
    "gender": "female",
    "ethnicity_preference": "东亚/中国",
    "personality_keywords": ["压抑", "坚韧", "疲惫"],
    "role_type": "主角",
    "need_age_line": True,
    "need_family": False,
}


def ctx(task_id: str = "task_identitydna_001") -> Quad:
    return Quad(
        story_id="drama_0005",
        bundle_id="bundle_20260723_001",
        task_id=task_id,
        asset_hash=None,
    )


def legit_requirement(**overrides):
    base = {
        "character_id": "char_x",
        "must_multi_face_fusion": True,
        "forbid_single_real_clone": True,
        "need_age_line": False,
        "need_family": False,
    }
    base.update(overrides)
    return base


def legit_pack(**overrides):
    pack = {
        "workorder_id": "wo_identitydna_0001",
        "character_id": "char_x",
        "base_face_asset_id": "face_x",
        "fusion_sources": fusion_mock.build_fusion_sources("seed", 3),
        "public_figure_matches": [],
        "naturalness": 0.90,
        "ethnicity_match": 0.90,
        "family_consistency": 0.90,
        "age_line": {},
    }
    pack.update(overrides)
    return pack


# ---------------------------------------------------------------------------
# 一、红线检测纯函数
# ---------------------------------------------------------------------------


class TestDetectSingleRealClone:
    def test_legit_three_source_fusion_has_no_block(self):
        assert detect_single_real_clone(legit_pack()) == []

    def test_single_dominant_real_person_blocked(self):
        """1 张真人 90% + 2 张陪衬 —— 形式多源，实质克隆。"""
        pack = legit_pack(
            fusion_sources=fusion_mock.simulate_single_real_clone_sources()
        )
        assert BLOCK_SINGLE_REAL_PERSON in detect_single_real_clone(pack)

    def test_only_one_source_blocked(self):
        pack = legit_pack(fusion_sources=fusion_mock.build_fusion_sources("s", 1))
        blocks = detect_single_real_clone(pack)
        assert BLOCK_INSUFFICIENT_FUSION_SOURCES in blocks
        # 单源权重必然为 1.0，同时命中"单一真人主导"
        assert BLOCK_SINGLE_REAL_PERSON in blocks

    def test_two_sources_blocked(self):
        pack = legit_pack(fusion_sources=fusion_mock.build_fusion_sources("s", 2))
        assert BLOCK_INSUFFICIENT_FUSION_SOURCES in detect_single_real_clone(pack)

    def test_duplicate_source_ids_cannot_pad_the_count(self):
        """同一张脸报三次不能凑数——按 source_id 去重后仍算 1 源。"""
        one = fusion_mock.build_fusion_sources("s", 3)[0]
        pack = legit_pack(fusion_sources=[dict(one), dict(one), dict(one)])
        assert BLOCK_INSUFFICIENT_FUSION_SOURCES in detect_single_real_clone(pack)

    def test_forged_low_weight_but_high_similarity_blocked(self):
        """伪造权重（写成 0.2）但成品与真人相似度 0.8 —— 仍必须拦下。"""
        srcs = fusion_mock.build_fusion_sources("s", 3)
        srcs[0]["weight"] = 0.2
        srcs[0]["similarity_to_result"] = 0.80
        pack = legit_pack(fusion_sources=srcs)
        assert BLOCK_SINGLE_REAL_PERSON in detect_single_real_clone(pack)

    def test_similarity_exactly_at_threshold_blocked(self):
        """阈值取闭区间：等于阈值即拦截（红线宁严勿宽）。"""
        srcs = fusion_mock.build_fusion_sources("s", 3)
        srcs[0]["similarity_to_result"] = MAX_SINGLE_SOURCE_SIMILARITY
        pack = legit_pack(fusion_sources=srcs)
        assert BLOCK_SINGLE_REAL_PERSON in detect_single_real_clone(pack)

    def test_synthetic_dominant_source_not_treated_as_clone(self):
        """纯合成源占比高不构成肖像权风险，不应误伤。"""
        srcs = fusion_mock.build_fusion_sources("s", 3)
        srcs[0]["is_real_person"] = False
        srcs[0]["license"] = "synthetic"
        srcs[0]["weight"] = 0.80
        srcs[0]["similarity_to_result"] = 0.85
        pack = legit_pack(fusion_sources=srcs)
        assert BLOCK_SINGLE_REAL_PERSON not in detect_single_real_clone(pack)

    def test_unlicensed_source_blocked(self):
        srcs = fusion_mock.build_fusion_sources("s", 3)
        srcs[1]["license"] = "scraped_from_web"
        pack = legit_pack(fusion_sources=srcs)
        assert BLOCK_UNLICENSED_SOURCE in detect_single_real_clone(pack)

    def test_public_figure_similarity_blocked(self):
        pack = legit_pack(
            public_figure_matches=[{"name": "某明星", "similarity": 0.72}]
        )
        assert BLOCK_PUBLIC_FIGURE in detect_single_real_clone(pack)

    def test_likeness_risk_takes_max_not_average(self):
        """两张不相干的脸不能把一张高相似的脸平均掉。"""
        srcs = fusion_mock.build_fusion_sources("s", 3)
        srcs[0]["similarity_to_result"] = 0.90
        srcs[1]["similarity_to_result"] = 0.05
        srcs[2]["similarity_to_result"] = 0.05
        assert compute_likeness_risk(legit_pack(fusion_sources=srcs)) == 0.90


# ---------------------------------------------------------------------------
# 二、Gate Report 不变式
# ---------------------------------------------------------------------------


class TestIdentityGateReport:
    def test_hard_block_forces_failure(self):
        report = run_identity_gate(
            gate_id="gate_identity_0001",
            workorder_id="wo_identitydna_0001",
            identity_pack=legit_pack(
                fusion_sources=fusion_mock.simulate_single_real_clone_sources()
            ),
            requirement=legit_requirement(),
        )
        assert report.passed is False
        assert report.has_hard_block is True
        assert BLOCK_SINGLE_REAL_PERSON in report.hard_blocks
        # 硬拦截无需人工复核——人也推不翻
        assert report.human_review_required is False

    def test_cannot_construct_passed_report_with_hard_block(self):
        """结构性不变式：带硬拦截却标 passed=True 的报告根本无法构造。"""
        with pytest.raises(Exception):
            IdentityGateReport(
                schema_version="cinemadna.identity_gate.v1",
                gate_id="g1",
                workorder_id="wo1",
                passed=True,
                hard_blocks=[BLOCK_SINGLE_REAL_PERSON],
            )

    def test_policy_flags_cannot_be_disabled(self):
        """把红线开关关掉本身就是违规。"""
        report = run_identity_gate(
            gate_id="g2",
            workorder_id="wo1",
            identity_pack=legit_pack(),
            requirement=legit_requirement(must_multi_face_fusion=False),
        )
        assert BLOCK_FUSION_POLICY_DISABLED in report.hard_blocks
        assert report.passed is False

    def test_legit_pack_passes(self):
        report = run_identity_gate(
            gate_id="g3",
            workorder_id="wo1",
            identity_pack=legit_pack(),
            requirement=legit_requirement(),
        )
        assert report.hard_blocks == []
        assert report.passed is True
        assert report.scores["likeness_risk"] < 0.35

    def test_report_dict_matches_spec_shape(self):
        report = run_identity_gate(
            gate_id="g4",
            workorder_id="wo1",
            identity_pack=legit_pack(),
            requirement=legit_requirement(),
        )
        d = report.to_dict()
        assert d["schema_version"] == "cinemadna.identity_gate.v1"
        assert set(d["scores"]) == {
            "naturalness",
            "likeness_risk",
            "ethnicity_match",
            "family_consistency",
            "overall",
        }
        assert "hard_blocks" in d


# ---------------------------------------------------------------------------
# 三、Service 层
# ---------------------------------------------------------------------------


class TestIdentityService:
    def test_parse_requirement_forces_policy_flags(self):
        svc = IdentityDNAService(AssetBrainStore())
        hostile = {**CHAR, "must_multi_face_fusion": False, "forbid_single_real_clone": False}
        req = svc.parse_requirement(hostile, ctx())
        # 调用方传 false 也没用，策略在入口被强制拉回 True
        assert req["must_multi_face_fusion"] is True
        assert req["forbid_single_real_clone"] is True

    def test_happy_path_creates_master_pack_with_age_line(self):
        fac = AssetBrainFacade()
        res = fac.request_identity(CHAR, ctx())
        assert res["outcome"] == OUTCOME_BACKFLOWED
        pack = fac.store.character_registry["char_linwan"]
        assert pack["gate_passed"] is True
        assert set(pack["age_line"]) == {"25", "35", "50"}
        assert pack["asset_hash"].startswith("sha256:")
        wo = fac.get_workorder_status(res["workorder_id"])
        assert wo["status"] == "BACKFLOWED"

    def test_second_request_reuses_registry(self):
        fac = AssetBrainFacade()
        fac.request_identity(CHAR, ctx())
        res2 = fac.request_identity(CHAR, ctx("task_identitydna_002"))
        assert res2["outcome"] == OUTCOME_REUSED
        assert res2["workorder_id"] is None

    def test_clone_scenario_rejected_end_to_end(self):
        """核心验收项 #5：单一真人克隆必须被识别并拒绝。"""
        fac = AssetBrainFacade()
        hostile = {
            **CHAR,
            "generation_plan_override": {
                "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
            },
        }
        res = fac.request_identity(hostile, ctx())

        assert res["outcome"] == OUTCOME_REJECTED_HARD_BLOCK
        assert BLOCK_SINGLE_REAL_PERSON in res["hard_blocks"]
        # 工单落 REJECTED
        assert fac.get_workorder_status(res["workorder_id"])["status"] == "REJECTED"
        # 绝不能进 Character Registry
        assert "char_linwan" not in fac.store.character_registry
        assert fac.store.backflow_records == {}

    def test_human_cannot_override_hard_block(self):
        """红线保护：人工审核接口对硬拦截一律抛错。"""
        fac = AssetBrainFacade()
        hostile = {
            **CHAR,
            "generation_plan_override": {
                "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
            },
        }
        res = fac.request_identity(hostile, ctx())
        with pytest.raises(HardBlockOverrideError):
            fac.approve_gate(
                res["gate_id"], {"approved": True, "decided_by": "human_boss"}
            )
        assert "char_linwan" not in fac.store.character_registry

    def test_backflow_refuses_tampered_workorder(self):
        """第二道防线：即使有人手工把工单推到 APPROVED，回流仍会二次校验。"""
        store = AssetBrainStore()
        svc = IdentityDNAService(store)
        req = svc.parse_requirement(CHAR, ctx())
        wo_dict = svc.create_workorder(req, {"found": False}, ctx())
        wo = store.get_workorder(wo_dict["workorder_id"])
        assert wo is not None

        # 伪造一份"通过但带硬拦截"的报告并强行推到 APPROVED
        wo.mark_running()
        wo.mark_gate_review(
            {"passed": True, "hard_blocks": [BLOCK_SINGLE_REAL_PERSON], "scores": {}}
        )
        wo.mark_approved()
        assert wo.status is WorkorderStatus.APPROVED

        with pytest.raises(AssetServiceError):
            svc.backflow({"character_id": "char_linwan", "master_pack_id": "cmp_x"}, wo, ctx())
        assert store.character_registry == {}

    def test_gate_requires_running_state(self):
        store = AssetBrainStore()
        svc = IdentityDNAService(store)
        req = svc.parse_requirement(CHAR, ctx())
        wo_dict = svc.create_workorder(req, {"found": False}, ctx())
        pack = {"workorder_id": wo_dict["workorder_id"]}
        # 还没进 RUNNING（未生成）就跑 Gate → 拒绝
        with pytest.raises(Exception):
            svc.run_gate(pack, req, ctx())

    def test_fusion_is_deterministic(self):
        """同一工单重复 mock 融合必须得到同样结果，保证 Gate 可复现。"""
        a = fusion_mock.mock_multi_face_fusion(
            workorder_id="wo_x", requirement=legit_requirement(), generation_plan={}
        )
        b = fusion_mock.mock_multi_face_fusion(
            workorder_id="wo_x", requirement=legit_requirement(), generation_plan={}
        )
        assert a["asset_hash"] == b["asset_hash"]
