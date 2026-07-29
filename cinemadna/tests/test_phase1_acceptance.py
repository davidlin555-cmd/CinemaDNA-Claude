"""Phase 1 验收对照测试

本文件按七条验收标准逐条落测，每个类对应一条，便于验收时直接对表。
与其它测试文件的分工：那边测"模块内部行为"，这里测"验收标准是否成立"，
并且尽量**走完整链路**（Orchestrator → Facade → Service → Gate → Backflow）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from asset_brain.common import schemas
from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad, QuadValidationError
from asset_brain.common.service_base import AssetServiceError
from asset_brain.common.workorder import WorkorderStatus
from asset_brain.facade import (
    OUTCOME_BACKFLOWED,
    OUTCOME_REJECTED,
    OUTCOME_REJECTED_HARD_BLOCK,
    OUTCOME_REUSED,
)
from asset_brain.identity_dna import fusion_mock
from asset_brain.identity_dna.gate import BLOCK_SINGLE_REAL_PERSON
from orchestrator.pipeline import ParallelCapacityError, PipelineOrchestrator
from orchestrator.story import (
    REPAIR_TARGETS,
    TERMINAL_STAGES,
    StoryStage,
    _ALLOWED_STAGE,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "mocks" / "sample_shooting_script.json"


@pytest.fixture(scope="module")
def script() -> dict:
    return json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))


def build_factory(tmp_path, **kwargs) -> PipelineOrchestrator:
    return PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723", **kwargs)


def run_story(orc: PipelineOrchestrator, script: dict, title: str = "A"):
    """完整跑一部剧到资产阶段，返回 (story, dispatch_result)。"""
    s = orc.create_story(title=title)
    orc.start_script(s.story_id)
    orc.submit_script(s.story_id, script_ref=f"{title}.json")
    return s, orc.dispatch_assets(s.story_id, script)


# ===========================================================================
# 验收 1：工厂骨架可用（四元组 + Bundle + Orchestrator 状态机）
# ===========================================================================


class TestCriterion1FactorySkeleton:
    def test_quad_rejects_incomplete_context(self):
        with pytest.raises(QuadValidationError):
            Quad.from_mapping({"story_id": "d1", "bundle_id": "b1", "task_id": "t1"})
        with pytest.raises(QuadValidationError):
            Quad(story_id="d1", bundle_id="b1", task_id="", asset_hash=None)
        # 未绑定 asset_hash 的四元组不得用于资产级操作
        with pytest.raises(QuadValidationError):
            Quad(story_id="d1", bundle_id="b1", task_id="t1", asset_hash=None).require_bound()

    def test_bundle_has_14_dirs_and_blocks_escape(self, tmp_path):
        b = Bundle(tmp_path, "bundle_20260723_001").ensure()
        assert len([d for d in b.root.iterdir() if d.is_dir()]) == 14
        assert len(schemas.BUNDLE_DIRS) == 14
        with pytest.raises(schemas.SchemaValidationError):
            b.write_json("../../escape.json", {})

    def test_orchestrator_state_machine_is_self_consistent(self):
        """状态机自洽性：非终态都有出边，且所有阶段都能从 CREATED 走到。"""
        for stage, outs in _ALLOWED_STAGE.items():
            if stage in TERMINAL_STAGES:
                assert outs == frozenset(), f"{stage} 是终态却有出边"
            else:
                assert outs, f"{stage} 是非终态却没有任何出边（孤儿阶段）"

        reachable = {StoryStage.CREATED}
        frontier = [StoryStage.CREATED]
        while frontier:
            for nxt in _ALLOWED_STAGE[frontier.pop()]:
                if nxt not in reachable:
                    reachable.add(nxt)
                    frontier.append(nxt)
        # FAILED / CANCELLED 走的是"任意非终态皆可进入"的逃生舱，不在正向表里
        unreachable = set(StoryStage) - reachable - {StoryStage.FAILED, StoryStage.CANCELLED}
        assert not unreachable, f"不可达阶段: {unreachable}"
        assert REPAIR_TARGETS <= reachable

    def test_multi_story_isolation(self, script, tmp_path):
        orc = build_factory(tmp_path)
        a, _ = run_story(orc, script, "A")
        b = orc.create_story(title="B")
        assert a.bundle_id != b.bundle_id
        assert orc.bundle_for(a.story_id).root != orc.bundle_for(b.story_id).root
        assert {w.quad.story_id for w in orc.store.list_workorders()} == {a.story_id}


# ===========================================================================
# 验收 2：三大资产模型 mock 闭环完整可跑
# ===========================================================================


class TestCriterion2ThreeModelsClosedLoop:
    def test_all_three_models_reach_backflow(self, script, tmp_path):
        orc = build_factory(tmp_path)
        _, res = run_story(orc, script)

        assert res["state"] == "ASSET_READY"
        for kind in ("scene", "identity", "prop"):
            assert res["results"][kind], f"{kind} 一个请求都没发出"
            assert all(r["outcome"] == OUTCOME_BACKFLOWED for r in res["results"][kind])

        # 三大库都真的沉淀了资产
        st = orc.store.stats()
        assert st["scene_atoms"] == 2 and st["characters"] == 1 and st["props"] == 1
        assert st["workorders_by_status"] == {"BACKFLOWED": 4}

    def test_每类工单都带完整四元组与_gate_report(self, script, tmp_path):
        orc = build_factory(tmp_path)
        s, _ = run_story(orc, script)
        wos = orc.store.list_workorders()
        assert {w.workorder_type for w in wos} == {"SCENE", "IDENTITY", "PROP"}
        for w in wos:
            w.quad.require_bound() if w.quad.is_bound else w.quad.validate()
            assert w.quad.story_id == s.story_id
            assert w.gate_report and w.gate_report["passed"] is True
            assert w.backflow_record_id
            assert w.status is WorkorderStatus.BACKFLOWED


# ===========================================================================
# 验收 3：IdentityDNA 硬性拦截「单一真人克隆」
# ===========================================================================


class TestCriterion3IdentityHardBlock:
    def _clone_script(self):
        return {
            "scenes": [],
            "characters": [
                {
                    "character_id": "char_clone",
                    "name": "克隆脸",
                    "generation_plan_override": {
                        "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
                    },
                }
            ],
            "props": [],
        }

    def test_clone_blocked_through_full_pipeline(self, tmp_path):
        orc = build_factory(tmp_path)
        s, res = run_story(orc, self._clone_script(), "C")

        # 红线 → 自修复态（不停厂），但绝不自动放行；驱动后升级人脸审核
        assert res["state"] == "AUTO_REPAIRING"
        assert res["results"]["identity"][0]["outcome"] == OUTCOME_REJECTED_HARD_BLOCK
        assert BLOCK_SINGLE_REAL_PERSON in res["results"]["identity"][0]["hard_blocks"]
        assert "char_clone" not in orc.store.character_registry
        assert orc.store.backflow_records == {}
        assert orc.get_story(s.story_id)["stage"] == "ASSET_MATCHING"
        assert orc.get_story(s.story_id)["repair_directive"]["code"] == "IDENTITY_REDLINE"

    def test_retry_without_fixing_the_plan_stays_blocked(self, tmp_path):
        """红线不因为"再试一次"而松动。"""
        orc = build_factory(tmp_path)
        s, _ = run_story(orc, self._clone_script(), "C")
        again = orc.retry_assets(s.story_id)
        assert again["state"] == "AUTO_REPAIRING"
        assert again["summary"]["retry_count"] == 1
        assert "char_clone" not in orc.store.character_registry

    def test_retry_with_legit_fusion_recovers(self, tmp_path):
        """换成合规的多源融合后才放行 —— 拦的是行为，不是这部剧。"""
        orc = build_factory(tmp_path)
        s, _ = run_story(orc, self._clone_script(), "C")
        fixed = orc.retry_assets(s.story_id, plan_override={"fusion_sources": []})
        assert fixed["state"] == "ASSET_READY"
        assert orc.store.character_registry["char_clone"]["gate_passed"] is True

    def test_human_cannot_override(self, tmp_path):
        from asset_brain.identity_dna.gate import HardBlockOverrideError

        orc = build_factory(tmp_path)
        s, res = run_story(orc, self._clone_script(), "C")
        gate_id = res["results"]["identity"][0]["gate_id"]
        with pytest.raises(HardBlockOverrideError):
            orc.facade_for(s.story_id).approve_gate(
                gate_id, {"approved": True, "decided_by": "human_boss"}
            )


# ===========================================================================
# 验收 4：SceneDNA 拒绝「要求自然场景却退化成纯 AI 生图」
# ===========================================================================


class TestCriterion4NaturalSceneFirst:
    def _ai_script(self):
        return {
            "scenes": [
                {
                    "scene_id": "EP001_SC001",
                    "location_description": "县城老旧出租屋",
                    "time_of_day": "深夜",
                    "mood": "压抑、疲惫",
                    "required_atoms": ["室内布局", "光线"],
                    "generation_plan_override": {"source_kind": "pure_ai_generated"},
                }
            ],
            "characters": [],
            "props": [],
        }

    def test_pure_ai_scene_rejected_through_full_pipeline(self, tmp_path):
        orc = build_factory(tmp_path)
        s, res = run_story(orc, self._ai_script(), "AI")

        r = res["results"]["scene"][0]
        assert r["outcome"] == OUTCOME_REJECTED
        assert any("自然场景原子优先" in i for i in r["issues"])
        # 场景判负 → 自动重生成态（ASSET_REGEN），不停厂
        assert res["state"] == "AUTO_REPAIRING"
        assert orc.get_story(s.story_id)["repair_directive"]["code"] == "ASSET_REGEN"
        # 未过闸的场景绝不入库
        assert orc.store.scene_atoms == {}

    def test_regenerating_with_natural_atoms_passes(self, tmp_path):
        orc = build_factory(tmp_path)
        s, _ = run_story(orc, self._ai_script(), "AI")
        fixed = orc.retry_assets(
            s.story_id, plan_override={"source_kind": "natural_atom_recompose"}
        )
        assert fixed["state"] == "ASSET_READY"
        assert len(orc.store.scene_atoms) == 1

    def test_prop_missing_state_also_blocked(self, tmp_path):
        """顺带验证 PropDNA 的连续性闸门在整链路上同样有效。"""
        orc = build_factory(tmp_path)
        prop_script = {
            "scenes": [],
            "characters": [],
            "props": [
                {
                    "prop_id": "prop_hospital_bill",
                    "name": "医院缴费单",
                    "states_needed": ["完整", "被攥皱"],
                    "continuity_critical": True,
                    "generation_plan_override": {"skip_states": ["被攥皱"]},
                }
            ],
        }
        s, res = run_story(orc, prop_script, "P")
        assert res["results"]["prop"][0]["outcome"] == OUTCOME_REJECTED
        assert res["state"] == "AUTO_REPAIRING"
        assert orc.store.prop_library == {}

    def test_regenerate_requires_rejected_status(self, script, tmp_path):
        orc = build_factory(tmp_path)
        s, res = run_story(orc, script)
        wid = res["results"]["scene"][0]["workorder_id"]
        # 已回流的工单不能"再生成一次"
        with pytest.raises(AssetServiceError):
            orc.facade_for(s.story_id).regenerate(wid)


# ===========================================================================
# 验收 5：假 shooting_script 跑通 查库 → 缺失建单 → mock 生成 → Gate → 回流
# ===========================================================================


class TestCriterion5EndToEndFromScript:
    def test_five_phases_all_happen_in_order(self, script, tmp_path):
        orc = build_factory(tmp_path)
        s, res = run_story(orc, script)

        for w in orc.store.list_workorders():
            # ① 查库：建单前查过，且明确记录"未命中"
            assert w.internal_search_result["found"] is False
            # ② 缺失建单：工单结构完整
            assert w.to_dict()["schema_version"] == schemas.SCHEMA_WORKORDER
            # ③ mock 生成：产出了资产 ID
            assert w.result_asset_ids
            # ④ Gate：报告存在且通过
            assert w.gate_report["passed"] is True
            assert w.gate_report["decided_by"] in ("auto_gate", "human_lin")
            # ⑤ 回流：记录存在
            assert w.backflow_record_id in orc.store.backflow_records

        # 回流的价值：同一份剧本第二次跑，全部命中，零新工单
        before = len(orc.store.workorders)
        b, res2 = run_story(orc, script, "B")
        assert all(
            r["outcome"] == OUTCOME_REUSED
            for group in res2["results"].values()
            for r in group
        )
        assert len(orc.store.workorders) == before

        # 全靠复用的剧，Bundle 里也必须留下"我用了哪些全局资产"的凭证
        root_b = orc.bundle_for(b.story_id).root
        reuse_files = sorted(p.relative_to(root_b).as_posix()
                             for p in root_b.rglob("reused.json"))
        assert reuse_files == [
            "02_cast/char_linwan/reused.json",
            "03_scene/EP001_SC001/reused.json",
            "03_scene/EP001_SC002/reused.json",
            "04_props/prop_hospital_bill/reused.json",
        ]
        rec = json.loads((root_b / "02_cast/char_linwan/reused.json").read_text("utf-8"))
        assert rec["schema_version"] == schemas.SCHEMA_ASSET_REUSE
        assert rec["story_id"] == b.story_id and rec["reused_asset_ids"]

    def test_bundle_contains_every_artifact(self, script, tmp_path):
        orc = build_factory(tmp_path)
        s, _ = run_story(orc, script)
        root = orc.bundle_for(s.story_id).root

        expected = [
            "01_script/story_manifest.json",
            "02_cast/char_linwan/master_pack.json",
            "03_scene/EP001_SC001/layout.json",
            "04_props/prop_hospital_bill/prop.json",
        ]
        for rel in expected:
            assert (root / rel).exists(), f"缺少 {rel}"
        for d in ("07_payloads", "08_submissions", "11_review", "12_asset_backflow"):
            assert list((root / d).rglob("*.json")), f"{d} 下没有任何记录"


# ===========================================================================
# 验收 6：支持同时启动第二部剧本（并行信号真实存在）
# ===========================================================================


class TestCriterion6ParallelProduction:
    def test_parallel_signal_is_real(self, script, tmp_path):
        orc = build_factory(tmp_path, max_concurrent_scripts=1)
        a = orc.create_story(title="A")
        b = orc.create_story(title="B")

        orc.start_script(a.story_id)
        with pytest.raises(ParallelCapacityError):
            orc.start_script(b.story_id)

        signal = orc.submit_script(a.story_id, script_ref="A.json")
        assert signal["new_story_allowed"] is True
        assert signal["script_slots_available"] == 1
        assert b.story_id in signal["queued_story_ids"]

        # A 才刚提交剧本（远未完成），B 已能开工 —— 这就是并行工厂
        orc.start_script(b.story_id)
        assert orc.get_story(a.story_id)["stage"] == "SCRIPT_DONE"
        assert orc.get_story(b.story_id)["stage"] == "SCRIPT_RUNNING"

        # A 继续往下走，完全不影响 B
        orc.dispatch_assets(a.story_id, script)
        orc.advance(a.story_id, StoryStage.DIRECTING)
        assert orc.get_story(b.story_id)["stage"] == "SCRIPT_RUNNING"

    def test_human_hold_does_not_block_other_stories(self, script, tmp_path):
        orc = build_factory(tmp_path, max_concurrent_scripts=1)
        a = orc.create_story(title="A")
        b = orc.create_story(title="B")
        orc.start_script(a.story_id)
        orc.hold_for_human(a.story_id, "script_human_gate")

        assert orc.can_start_new_story() is True
        orc.start_script(b.story_id)
        assert [h["story_id"] for h in orc.pending_holds()] == [a.story_id]
        assert [x["story_id"] for x in orc.next_actionable()] == [b.story_id]

    def test_two_stories_progress_independently(self, script, tmp_path):
        orc = build_factory(tmp_path, max_concurrent_scripts=2)
        a, _ = run_story(orc, script, "A")
        b = orc.create_story(title="B")
        orc.start_script(b.story_id)

        for st in ("DIRECTING", "PERFORMANCE", "RENDERING"):
            orc.advance(a.story_id, st)
        orc.submit_script(b.story_id, script_ref="B.json")

        st = orc.factory_status()
        assert st["by_stage"] == {"RENDERING": 1, "SCRIPT_DONE": 1}
        assert st["script_slots"]["available"] == 2


# ===========================================================================
# 验收 7：核心路径都有测试（此处补状态机边覆盖的自检）
# ===========================================================================


class TestCriterion7CorePathCoverage:
    def test_workorder_state_machine_edges(self):
        """通用 Workorder 状态机：每条边都被走过一次。"""
        from asset_brain.common.quad import Quad as Q
        from asset_brain.common.workorder import new_workorder

        def fresh():
            return new_workorder(
                workorder_id="wo_x",
                workorder_type="SCENE",
                quad=Q(story_id="d1", bundle_id="b1", task_id="t1", asset_hash=None),
                requested_by="scriptbrain",
            )

        # PENDING → RUNNING → GATE_REVIEW → APPROVED → BACKFLOWED
        wo = fresh()
        wo.mark_running().mark_gate_review().mark_approved().mark_backflowed("bf_1")
        assert wo.status is WorkorderStatus.BACKFLOWED
        # GATE_REVIEW → REJECTED → RUNNING（重生成）
        wo = fresh()
        wo.mark_running().mark_gate_review().mark_rejected().mark_running()
        assert wo.status is WorkorderStatus.RUNNING
        # 非终态 → FAILED
        wo.mark_failed("boom")
        assert wo.status is WorkorderStatus.FAILED

    def test_story_repair_and_terminal_paths(self, script, tmp_path):
        orc = build_factory(tmp_path)
        s, _ = run_story(orc, script)
        for st in ("DIRECTING", "PERFORMANCE", "RENDERING", "QA"):
            orc.advance(s.story_id, st)

        # QA 精准打回 → 重走 → 发布 → 归档
        orc.request_repair(s.story_id, StoryStage.PERFORMANCE, reason="表情呆滞")
        assert orc.get_story(s.story_id)["stage"] == "PERFORMANCE"
        for st in ("RENDERING", "QA", "POST", "FINAL_REVIEW", "PUBLISHED"):
            orc.advance(s.story_id, st)
        orc.archive_story(s.story_id)

        story = orc.get_story(s.story_id)
        assert story["stage"] == "ARCHIVED" and story["status"] == "CLOSED"
        events = [e["event"] for e in story["events"]]
        assert "REPAIR_ROLLBACK" in events and "STAGE_ADVANCED" in events
