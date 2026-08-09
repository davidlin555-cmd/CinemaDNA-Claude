"""Pipeline Orchestrator 单元测试 (Phase 1)

四组：
1. Story 状态机本身（两轴：stage / status）
2. **并行核心**：剧本提交后立刻可开新剧、人工闸门不阻塞其它故事
3. 与四元组 / Bundle / AssetBrainFacade 的对接
4. 看板与调度查询
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cinemadna.asset_brain.common.quad import Quad
from cinemadna.asset_brain.identity_dna import fusion_mock
from cinemadna.asset_brain.identity_dna.service import IdentityDNAService
from cinemadna.asset_brain.scene_dna.service import SceneDNAService
from orchestrator.pipeline import (
    ParallelCapacityError,
    PipelineOrchestrator,
    StoryNotFoundError,
)
from orchestrator.story import InvalidStageTransition, Story, StoryStage, StoryStatus

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "mocks" / "sample_shooting_script.json"


@pytest.fixture(scope="module")
def script() -> dict:
    return json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))


def make_story(**overrides) -> Story:
    base = dict(story_id="drama_0001", bundle_id="bundle_20260723_001", title="测试剧")
    base.update(overrides)
    return Story(**base)


def to_stage(s: Story, target: StoryStage) -> Story:
    """按合法链条把故事推到目标阶段。"""
    chain = [
        StoryStage.SCRIPT_RUNNING,
        StoryStage.SCRIPT_DONE,
        StoryStage.DIRECTOR_PLANNING,
        StoryStage.DIRECTOR_QA,
        StoryStage.ASSET_MATCHING,
        StoryStage.ASSET_READY,
        StoryStage.DIRECTING,
        StoryStage.PERFORMANCE,
        StoryStage.RENDERING,
        StoryStage.QA,
        StoryStage.POST,
        StoryStage.FINAL_REVIEW,
        StoryStage.PUBLISHED,
        StoryStage.ARCHIVED,
    ]
    for st in chain:
        s.advance_to(st)
        if st is target:
            break
    return s


# ---------------------------------------------------------------------------
# 一、Story 状态机
# ---------------------------------------------------------------------------


class TestStoryStateMachine:
    def test_initial_state(self):
        s = make_story()
        assert s.stage is StoryStage.CREATED
        assert s.status is StoryStatus.RUNNING
        assert s.state == "CREATED"
        assert s.is_terminal is False
        assert s.to_dict()["schema_version"] == "cinemadna.story.v1"

    def test_full_legal_chain(self):
        s = to_stage(make_story(), StoryStage.ARCHIVED)
        assert s.stage is StoryStage.ARCHIVED
        assert s.status is StoryStatus.CLOSED
        assert s.is_terminal is True

    def test_illegal_skip_rejected(self):
        s = make_story()
        with pytest.raises(InvalidStageTransition):
            s.advance_to(StoryStage.ASSET_MATCHING)  # 跳过剧本阶段
        s.advance_to(StoryStage.SCRIPT_RUNNING)
        with pytest.raises(InvalidStageTransition):
            s.advance_to(StoryStage.RENDERING)

    def test_terminal_locked(self):
        s = to_stage(make_story(), StoryStage.ARCHIVED)
        with pytest.raises(InvalidStageTransition):
            s.advance_to(StoryStage.SCRIPT_RUNNING)
        with pytest.raises(InvalidStageTransition):
            s.fail("已归档还想失败")

    def test_fail_from_any_active_stage(self):
        s = to_stage(make_story(), StoryStage.RENDERING)
        s.fail("渲染节点全部离线")
        assert s.stage is StoryStage.FAILED
        assert s.status is StoryStatus.CLOSED
        assert s.error == "渲染节点全部离线"

    # -- Human Hold：stage 不变，只动 status --------------------------------

    def test_hold_keeps_stage_and_reports_waiting_human(self):
        s = to_stage(make_story(), StoryStage.RENDERING)
        s.hold_for_human("shot_review", gate_ids=["gate_x"])
        assert s.status is StoryStatus.WAITING_HUMAN
        assert s.stage is StoryStage.RENDERING  # 挂起不丢进度
        assert s.state == "WAITING_HUMAN"
        assert s.hold["stage"] == "RENDERING"

    def test_cannot_advance_while_held(self):
        s = to_stage(make_story(), StoryStage.RENDERING)
        s.hold_for_human("shot_review")
        with pytest.raises(InvalidStageTransition):
            s.advance_to(StoryStage.QA)

    def test_release_resumes_from_same_stage(self):
        """人工通过后自动续跑：从挂起那一步继续，而不是重来。"""
        s = to_stage(make_story(), StoryStage.RENDERING)
        s.hold_for_human("shot_review")
        s.release_hold(decided_by="human_lin")
        assert s.status is StoryStatus.RUNNING
        assert s.stage is StoryStage.RENDERING
        s.advance_to(StoryStage.QA)
        assert s.stage is StoryStage.QA

    def test_can_still_cancel_while_held(self):
        s = to_stage(make_story(), StoryStage.RENDERING)
        s.hold_for_human("shot_review")
        s.cancel("题材下架")
        assert s.stage is StoryStage.CANCELLED

    def test_release_without_hold_rejected(self):
        with pytest.raises(InvalidStageTransition):
            make_story().release_hold(decided_by="human")

    # -- 机器阻塞 -----------------------------------------------------------

    def test_block_and_unblock(self):
        s = to_stage(make_story(), StoryStage.ASSET_MATCHING)
        s.block("资产 Gate 未过")
        assert s.state == "BLOCKED"
        s.unblock("已重生成")
        assert s.state == "ASSET_MATCHING"

    def test_advancing_clears_block(self):
        s = to_stage(make_story(), StoryStage.ASSET_MATCHING)
        s.block("资产 Gate 未过")
        s.advance_to(StoryStage.ASSET_READY)
        assert s.status is StoryStatus.RUNNING
        assert s.blocked_reason is None

    # -- Repair 打回 ---------------------------------------------------------

    def test_repair_rollback_to_performance(self):
        s = to_stage(make_story(), StoryStage.QA)
        s.repair_to(StoryStage.PERFORMANCE, reason="女主表情呆滞")
        assert s.stage is StoryStage.PERFORMANCE
        assert s.events[-1]["event"] == "REPAIR_ROLLBACK"

    def test_repair_to_illegal_target_rejected(self):
        s = to_stage(make_story(), StoryStage.QA)
        with pytest.raises(InvalidStageTransition):
            s.repair_to(StoryStage.SCRIPT_RUNNING, reason="想重写剧本")

    def test_repair_from_illegal_source_rejected(self):
        s = to_stage(make_story(), StoryStage.DIRECTING)
        with pytest.raises(InvalidStageTransition):
            s.repair_to(StoryStage.ASSET_MATCHING, reason="还没到 QA")

    # -- 四元组对接 ---------------------------------------------------------

    def test_quad_binding(self):
        s = make_story()
        q = s.quad(kind="scenedna")
        assert isinstance(q, Quad)
        assert q.story_id == "drama_0001"
        assert q.bundle_id == "bundle_20260723_001"
        assert q.task_id == "task_scenedna_0001"
        assert s.quad(kind="scenedna").task_id == "task_scenedna_0002"
        assert s.quad(kind="propdna").task_id == "task_propdna_0001"

    def test_illegal_story_id_rejected(self):
        with pytest.raises(Exception):
            make_story(story_id="drama 0001")  # 含空格，不能作路径片段

    def test_events_recorded(self):
        s = to_stage(make_story(), StoryStage.SCRIPT_DONE)
        kinds = [e["event"] for e in s.events]
        assert kinds[0] == "STORY_CREATED"
        assert kinds.count("STAGE_ADVANCED") == 2
        assert all(e["at"] for e in s.events)


# ---------------------------------------------------------------------------
# 二、并行核心
# ---------------------------------------------------------------------------


class TestParallelProduction:
    def test_script_slot_blocks_second_story_until_submit(self):
        orc = PipelineOrchestrator(max_concurrent_scripts=1)
        a = orc.create_story(title="A")
        b = orc.create_story(title="B")

        orc.start_script(a.story_id)
        assert orc.can_start_new_story() is False
        with pytest.raises(ParallelCapacityError):
            orc.start_script(b.story_id)

        signal = orc.submit_script(a.story_id, script_ref="shooting_script_A.json")
        assert signal["new_story_allowed"] is True
        assert signal["script_slots_available"] == 1
        assert b.story_id in signal["queued_story_ids"]

        # A 还远没做完（只到 SCRIPT_DONE），B 已经可以开工
        orc.start_script(b.story_id)
        assert orc.get_story(a.story_id)["stage"] == "SCRIPT_DONE"
        assert orc.get_story(b.story_id)["stage"] == "SCRIPT_RUNNING"

    def test_downstream_stages_never_consume_script_slot(self, script):
        """A 一路做到渲染，B 的剧本照开不误。"""
        orc = PipelineOrchestrator(max_concurrent_scripts=1)
        a = orc.create_story(title="A")
        orc.start_script(a.story_id)
        orc.submit_script(a.story_id, script_ref="A.json")
        orc.dispatch_assets(a.story_id, script)
        for st in ("DIRECTING", "PERFORMANCE", "RENDERING"):
            orc.advance(a.story_id, st)

        b = orc.create_story(title="B")
        orc.start_script(b.story_id)  # 不应抛异常
        assert orc.get_story(b.story_id)["stage"] == "SCRIPT_RUNNING"
        assert orc.script_slots_available() == 0  # 现在轮到 B 占位

    def test_human_hold_releases_the_script_slot(self):
        """铁规：人工审核不得阻塞其它故事。"""
        orc = PipelineOrchestrator(max_concurrent_scripts=1)
        a = orc.create_story(title="A")
        b = orc.create_story(title="B")
        orc.start_script(a.story_id)
        assert orc.can_start_new_story() is False

        # A 的剧本卡在人工闸门
        orc.hold_for_human(a.story_id, "script_human_gate")
        assert orc.can_start_new_story() is True
        orc.start_script(b.story_id)

        # A 放行后仍回到 SCRIPT_RUNNING，可继续提交
        orc.release_hold(a.story_id, decided_by="human_lin")
        assert orc.get_story(a.story_id)["stage"] == "SCRIPT_RUNNING"
        orc.submit_script(a.story_id, script_ref="A.json")
        assert orc.get_story(a.story_id)["stage"] == "SCRIPT_DONE"

    def test_two_slots_allow_two_concurrent_scripts(self):
        orc = PipelineOrchestrator(max_concurrent_scripts=2)
        a = orc.create_story()
        b = orc.create_story()
        c = orc.create_story()
        orc.start_script(a.story_id)
        orc.start_script(b.story_id)
        assert orc.script_slots_available() == 0
        with pytest.raises(ParallelCapacityError):
            orc.start_script(c.story_id)

    def test_submit_from_wrong_stage_rejected(self):
        orc = PipelineOrchestrator()
        a = orc.create_story()
        with pytest.raises(InvalidStageTransition):
            orc.submit_script(a.story_id, script_ref="A.json")

    def test_unknown_story_rejected(self):
        orc = PipelineOrchestrator()
        with pytest.raises(StoryNotFoundError):
            orc.get_story("drama_9999")


# ---------------------------------------------------------------------------
# 三、与四元组 / Bundle / AssetBrainFacade 对接
# ---------------------------------------------------------------------------


class TestAssetBrainIntegration:
    def _run_to_assets(self, orc, script, title="A"):
        s = orc.create_story(title=title)
        orc.start_script(s.story_id)
        orc.submit_script(s.story_id, script_ref=f"{title}.json")
        return s, orc.dispatch_assets(s.story_id, script)

    def test_dispatch_assets_reaches_asset_ready(self, script, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s, res = self._run_to_assets(orc, script)

        assert res["state"] == "ASSET_READY"
        assert res["summary"]["total"] == 4  # 2 场景 + 1 角色 + 1 道具
        assert res["summary"]["by_outcome"]["BACKFLOWED"] == 4

        # 工单四元组全部绑定到本故事
        wos = orc.store.list_workorders(story_id=s.story_id)
        assert len(wos) == 4
        assert {w.quad.bundle_id for w in wos} == {s.bundle_id}
        assert all(w.quad.task_id.startswith("task_") for w in wos)

    def test_each_story_writes_only_its_own_bundle(self, script, tmp_path):
        """Bundle 隔离：A 的资产不得落进 B 的 Bundle。"""
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        a, _ = self._run_to_assets(orc, script, "A")

        other = {
            "scenes": [
                {
                    "scene_id": "EP001_SC001",
                    "location_description": "海边渔村",
                    "time_of_day": "黄昏",
                    "mood": "辽阔、孤独",
                    "required_atoms": ["海岸线", "夕照"],
                }
            ],
            "characters": [{"character_id": "char_zhaoming", "name": "赵明"}],
            "props": [{"prop_id": "prop_fishing_net", "name": "渔网"}],
        }
        b, _ = self._run_to_assets(orc, other, "B")

        root_a = orc.bundle_for(a.story_id).root
        root_b = orc.bundle_for(b.story_id).root
        assert root_a != root_b
        assert (root_a / "02_cast" / "char_linwan" / "master_pack.json").exists()
        assert not (root_b / "02_cast" / "char_linwan").exists()
        assert (root_b / "02_cast" / "char_zhaoming" / "master_pack.json").exists()

        # 故事清单落在各自 Bundle 的 01_script 内
        manifest = json.loads(
            (root_a / "01_script" / "story_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["story_id"] == a.story_id
        assert manifest["state"] == "ASSET_READY"

    def test_second_story_reuses_shared_asset_libraries(self, script, tmp_path):
        """三大库共享：第二部剧直接复用，不再建工单。"""
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        self._run_to_assets(orc, script, "A")
        before = len(orc.store.workorders)

        _, res_b = self._run_to_assets(orc, script, "B")
        assert res_b["summary"]["by_outcome"]["REUSED"] == 4
        assert len(orc.store.workorders) == before

    def test_identity_hard_block_auto_escalates_to_face_review(self, script, tmp_path):
        """IdentityDNA 红线命中 → 进入自修复态；驱动后升级人脸审核，永不自动放行。"""
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        hostile = {
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
        s, res = self._run_to_assets(orc, hostile, "C")

        # dispatch 后先进自修复态（红线 max_attempts=0）；不停厂、也不自动放行
        assert res["state"] == "AUTO_REPAIRING"
        story = orc.get_story(s.story_id)
        assert story["repair_directive"]["code"] == "IDENTITY_REDLINE"
        assert "char_clone" not in orc.store.character_registry

        # 驱动：红线不自动绕过 → 升级到人脸审核（人工点 2）
        orc.drive(s.story_id)
        story = orc.get_story(s.story_id)
        assert story["state"] == "WAITING_HUMAN"
        assert story["hold"]["reason"] == "identity_review"
        assert story["stage"] == "ASSET_MATCHING"
        assert "char_clone" not in orc.store.character_registry

    def test_scene_pending_auto_approved_no_human(self, script, tmp_path, monkeypatch):
        """场景 Gate 需复核 → **自动放行**（非人工点），不再挂人工。"""
        original = SceneDNAService.mock_generate

        def patched(self, workorder):
            asset = original(self, workorder)
            asset["rights_risk"] = 0.22  # 过闸但触发"需复核"
            return asset

        monkeypatch.setattr(SceneDNAService, "mock_generate", patched)

        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s, res = self._run_to_assets(orc, script, "A")

        # 场景待审自动放行 → 直接就绪，无人工挂起
        assert res["state"] == "ASSET_READY"
        assert orc.pending_holds() == []
        assert res["summary"]["auto_approved_gate_ids"]
        assert orc.get_story(s.story_id)["status"] == "RUNNING"

    def test_real_face_holds_for_face_review(
        self, script, tmp_path, monkeypatch
    ):
        """生成真实人脸 → 必过人工点 2（人脸审核）；批准后续跑 ASSET_READY。"""
        original = IdentityDNAService.mock_multi_face_fusion

        def patched(self, workorder):
            pack = original(self, workorder)
            pack["is_real_image"] = True  # 模拟"真实人脸已生成"→ 必进人脸审核
            return pack

        monkeypatch.setattr(IdentityDNAService, "mock_multi_face_fusion", patched)

        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s, res = self._run_to_assets(orc, script, "A")

        assert res["state"] == "WAITING_HUMAN"
        holds = orc.pending_holds()
        assert holds[0]["reason"] == "identity_review"
        gate_ids = list(holds[0]["gate_ids"])

        for g in gate_ids:
            orc.resolve_asset_gate(
                s.story_id, g, {"approved": True, "decided_by": "human_lin"})
        assert orc.get_story(s.story_id)["state"] == "ASSET_READY"

    def test_resolve_unknown_gate_rejected(self, script, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path)
        s, _ = self._run_to_assets(orc, script, "A")
        with pytest.raises(InvalidStageTransition):
            orc.resolve_asset_gate(s.story_id, "gate_x", {"approved": True})

    def test_archive_closes_bundle(self, script, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s, _ = self._run_to_assets(orc, script, "A")
        for st in ("DIRECTING", "PERFORMANCE", "RENDERING", "QA", "POST",
                   "FINAL_REVIEW", "PUBLISHED"):
            orc.advance(s.story_id, st)
        orc.archive_story(s.story_id)

        assert orc.get_story(s.story_id)["stage"] == "ARCHIVED"
        assert (orc.bundle_for(s.story_id).root / "13_archive" / "story_final.json").exists()

    def test_archive_requires_published(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path)
        s = orc.create_story()
        with pytest.raises(InvalidStageTransition):
            orc.archive_story(s.story_id)


# ---------------------------------------------------------------------------
# 四、看板与调度查询
# ---------------------------------------------------------------------------


class TestDashboardAndPlanner:
    def _factory(self, script, tmp_path):
        orc = PipelineOrchestrator(
            bundle_root=tmp_path, bundle_date="20260723", max_concurrent_scripts=1
        )
        a = orc.create_story(title="A", priority="high")
        orc.start_script(a.story_id)
        orc.submit_script(a.story_id, script_ref="A.json")
        orc.dispatch_assets(a.story_id, script)

        b = orc.create_story(title="B")
        orc.start_script(b.story_id)

        c = orc.create_story(title="C", priority="critical")  # 排队中
        return orc, a, b, c

    def test_factory_status_shape(self, script, tmp_path):
        orc, a, b, c = self._factory(script, tmp_path)
        st = orc.factory_status()
        assert st["schema_version"] == "cinemadna.factory_status.v1"
        assert st["stories_total"] == 3
        assert st["by_stage"]["ASSET_READY"] == 1
        assert st["by_stage"]["SCRIPT_RUNNING"] == 1
        assert st["by_stage"]["CREATED"] == 1
        assert st["script_slots"] == {"max": 1, "available": 0}
        assert st["asset_brain"]["workorders"] == 4

    def test_list_stories_filters(self, script, tmp_path):
        orc, a, b, c = self._factory(script, tmp_path)
        assert [x["story_id"] for x in orc.list_stories(stage=StoryStage.CREATED)] == [
            c.story_id
        ]
        assert len(orc.list_stories(status=StoryStatus.RUNNING)) == 3
        orc.hold_for_human(b.story_id, "script_human_gate")
        assert [x["story_id"] for x in orc.list_stories(state="WAITING_HUMAN")] == [
            b.story_id
        ]

    def test_next_actionable_excludes_held_blocked_and_queued(self, script, tmp_path):
        orc, a, b, c = self._factory(script, tmp_path)
        # 槽位被 B 占着，排队中的 C 此刻不可推进
        assert [x["story_id"] for x in orc.next_actionable()] == [a.story_id, b.story_id]

        orc.hold_for_human(b.story_id, "script_human_gate")
        ids = [x["story_id"] for x in orc.next_actionable()]
        # B 挂起 → 退出可推进队列；槽位释放 → critical 的 C 顶上并排在最前
        assert ids == [c.story_id, a.story_id]

        orc.fail_story(a.story_id, "算力全挂")
        assert [x["story_id"] for x in orc.next_actionable()] == [c.story_id]
