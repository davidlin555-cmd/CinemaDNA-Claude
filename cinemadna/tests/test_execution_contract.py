"""Director Execution Engine（①）测试 —— 可拍摄事实编译/自检/硬门（0 成本）。"""

from __future__ import annotations

import pytest

from director.master_plan import DirectorMasterPlan, ShotPlan
from director.execution import (
    ExecutionContractCompiler, BlockingValidator, require_execution_contract,
    ExecutionContractError, ActorSlot, DirectorExecutionContract,
)


def _plan() -> DirectorMasterPlan:
    shots = [
        ShotPlan(  # 建立镜：女主独自，无台词
            shot_id="SH1", order=1, scene_id="SC1", story_function="钩子",
            action_beats=[{"body_action": "攥皱缴费单", "actors": ["linwan"]}],
            performance_intent="压抑", dialogue_owner=None, line="",
            camera={"shot_size": "全景", "intent": "建立环境", "duration_sec": 2.5,
                    "shot_type": "ESTABLISHING"},
            prop_usage={"prop_id": "bill", "contact": True},
            entry_state="", exit_state=""),
        ShotPlan(  # 对峙镜：女主对追债人施压，掏证据
            shot_id="SH2", order=2, scene_id="SC1", story_function="施压",
            action_beats=[{"body_action": "上前逼近", "actors": ["linwan", "zhao"]},
                          {"body_action": "掏出证据", "actors": ["linwan"]}],
            performance_intent="决绝", dialogue_owner="linwan", line="这钱我不还了",
            camera={"shot_size": "中景", "intent": "对峙", "duration_sec": 3.5},
            prop_usage={"prop_id": "iou", "contact": True},
            entry_state="", exit_state=""),
    ]
    return DirectorMasterPlan(
        story_id="s1",
        story_spine={"conflict": "护士vs高利贷"},
        cast_lock={"linwan": {"role": "护士", "appearance": "white uniform"},
                   "zhao": {"role": "讨债", "appearance": "leather jacket"}},
        scene_layout=[{"scene_id": "SC1", "location": "夜市", "blocking": {}}],
        prop_plan=[{"prop_id": "bill"}, {"prop_id": "iou"}],
        emotion_curve=[], density_plan={}, shot_list=shots).lock()


class TestCompile:
    def test_slot_registry_death_locks_abc(self):
        ec = ExecutionContractCompiler().compile(_plan())
        # 首次出场顺序：SH1 是 linwan → A；SH2 引入 zhao → B
        assert ec.slot_registry["A"].character_id == "linwan"
        assert ec.slot_registry["B"].character_id == "zhao"
        assert ec.slot_registry["A"].face_ref == "linwan"      # 身份锚已绑
        assert ec.slot_registry["A"].wardrobe == "white uniform"
        assert ec.slot_of("zhao") == "B"

    def test_action_predicate_prop_and_directed(self):
        ec = ExecutionContractCompiler().compile(_plan())
        sh2 = ec.shot("SH2")
        verbs = {(p.verb, p.obj_kind, p.obj) for p in sh2.action_predicates}
        assert ("上前逼近", "slot", "B") in verbs      # 指向对手=槽位B
        assert ("掏出证据", "prop", "iou") in verbs      # 作用于道具
        assert sh2.dialogue_slot == "A"

    def test_prop_physical_state_held_in_hand(self):
        ec = ExecutionContractCompiler().compile(_plan())
        sh2 = ec.shot("SH2")
        ps = sh2.prop_states[0]
        assert ps.prop_id == "iou" and ps.held_by == "A" and ps.contact
        assert ps.plane == "hand"

    def test_two_person_shot_has_both_slots(self):
        ec = ExecutionContractCompiler().compile(_plan())
        assert set(ec.shot("SH2").in_frame_slots) == {"A", "B"}


class TestValidator:
    def test_good_contract_validates_and_locks(self):
        ec = ExecutionContractCompiler().compile(_plan())
        ok, issues = BlockingValidator().validate_and_lock(ec)
        assert ok, issues
        assert ec.validated and ec.contract_hash

    def test_speaking_slot_offscreen_fails(self):
        ec = ExecutionContractCompiler().compile(_plan())
        ec.shot("SH2").in_frame_slots = ["B"]          # 说话人 A 被移出画面
        ok, issues = BlockingValidator().validate_and_lock(ec)
        assert not ok
        assert any("说话槽位" in i["message"] for i in issues)

    def test_prop_holder_offscreen_fails(self):
        ec = ExecutionContractCompiler().compile(_plan())
        ec.shot("SH2").in_frame_slots = ["A"]          # 去掉 B
        ec.shot("SH2").prop_states[0].held_by = "B"     # 但道具在不在场的 B 手里
        ok, issues = BlockingValidator().validate_and_lock(ec)
        assert not ok
        assert any("持有者" in i["message"] for i in issues)

    def test_slot_conflict_fails(self):
        ec = ExecutionContractCompiler().compile(_plan())
        ec.slot_registry["B"] = ActorSlot("B", "linwan", "linwan", "x")  # 同角色占两槽
        ok, issues = BlockingValidator().validate(ec), None
        assert any("槽位冲突" in i["message"] for i in ok)

    def test_camera_missing_purpose_fails(self):
        ec = ExecutionContractCompiler().compile(_plan())
        ec.shot("SH1").camera.purpose = ""
        issues = BlockingValidator().validate(ec)
        assert any("缺目的" in i["message"] for i in issues)


class TestEndToEndFromDirector:
    """A·wiring：真总谱流程(showrunner+staging.actors)→执行合同，对手进场为锁定事实。"""

    def _authored_plan(self):
        from director.agents.showrunner import ShowrunnerDirector
        from director.agents.director_qa import DirectorQAOfficer
        script = {
            "logline": "x", "theme": "讨债对峙",
            "characters": [
                {"character_id": "linwan", "name": "林晚", "role": "护士",
                 "appearance": "white uniform"},
                {"character_id": "zhao", "name": "赵盛", "role": "讨债",
                 "appearance": "leather jacket"}],
            "props": [{"prop_id": "iou", "name": "欠条", "states_needed": ["完整"]}],
            "episodes": [{"scenes": [{
                "scene_id": "SC1", "location_description": "夜市",
                "characters": ["linwan", "zhao"],
                "narrative": {"cause": "追债", "effect": "反击"},
                "beats": [
                    {"type": "setup", "description": "林晚独自走", "seconds": 2.5},
                    {"type": "action", "line": "这钱我不还了",
                     "character_id": "linwan", "description": "上前逼近",
                     "seconds": 3.5},
                    {"type": "info", "description": "欠条特写", "seconds": 2.0}]}]}]}
        plan = ShowrunnerDirector().build_skeleton(script, story_id="s1")
        for ag in DirectorQAOfficer().specialists:
            if hasattr(ag, "refine"):
                ag.refine(plan)
        return plan.lock()

    def test_confrontation_near_shot_is_single_char_reverse(self):
        # 正反打：对峙近景 = 说话人单人镜（避 Kling 双人近景失控:接吻/漂移）；
        # 对手经反打镜出现,不同框近景。
        plan = self._authored_plan()
        ec = ExecutionContractCompiler().compile(plan)
        BlockingValidator().validate_and_lock(ec)
        pressure = next(s for s in ec.shots if s.shot_id == "SC1_SH002")  # 施压·中景
        assert pressure.in_frame_slots == ["A"]                # 单人正打(说话人)
        assert not pressure.is_prop_insert

    def test_reveal_cu_is_prop_insert_no_faces(self):
        plan = self._authored_plan()
        ec = ExecutionContractCompiler().compile(plan)
        BlockingValidator().validate_and_lock(ec)
        rev = next(s for s in ec.shots if s.shot_id == "SC1_SH003")
        assert rev.in_frame_slots == [] and rev.is_prop_insert   # 欠条特写无脸

    def test_authored_plan_execution_contract_validates(self):
        plan = self._authored_plan()
        ec = ExecutionContractCompiler().compile(plan)
        ok, issues = BlockingValidator().validate_and_lock(ec)
        assert ok, issues


class TestRenderGateWiring:
    """步骤1·确认：真总谱下，无执行合同/合同失效 → run_render 硬门真的拦渲。"""

    def _orc_with_authored_plan(self, tmp_path):
        from orchestrator.pipeline import PipelineOrchestrator
        from director.agents.showrunner import ShowrunnerDirector
        from director.agents.director_qa import DirectorQAOfficer
        script = {
            "logline": "x", "theme": "讨债",
            "characters": [{"character_id": "linwan", "name": "林晚", "role": "护士",
                            "appearance": "white uniform"}],
            "props": [], "episodes": [{"scenes": [{
                "scene_id": "EP001_SC001", "location_description": "夜市",
                "characters": ["linwan"],
                "beats": [{"type": "action", "line": "我不还了",
                           "character_id": "linwan", "description": "上前逼近",
                           "seconds": 3.0}]}]}]}
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260726")
        s = orc.create_story(title="t")
        sid = s.story_id
        plan = ShowrunnerDirector().build_skeleton(script, story_id=sid)
        for ag in DirectorQAOfficer().specialists:
            if hasattr(ag, "refine"):
                ag.refine(plan)
        plan.lock()
        orc._master_plans[sid] = plan
        orc._contracts[sid] = [{"shot_id": sp.shot_id} for sp in plan.shots]
        return orc, sid, plan

    def test_authored_plan_without_exec_contract_blocks(self, tmp_path):
        orc, sid, _ = self._orc_with_authored_plan(tmp_path)
        # 没编译执行合同 → 硬门必须拦
        assert orc._require_execution_contract_for_render(sid) is False

    def test_authored_plan_with_valid_exec_contract_passes(self, tmp_path):
        orc, sid, plan = self._orc_with_authored_plan(tmp_path)
        orc._compile_execution_contract(sid, plan)              # 编译+自检+落盘
        assert orc._require_execution_contract_for_render(sid) is True

    def test_tampered_exec_contract_blocks(self, tmp_path):
        orc, sid, plan = self._orc_with_authored_plan(tmp_path)
        orc._compile_execution_contract(sid, plan)
        orc._exec_contracts[sid].shots[0].camera.shot_size = "篡改"  # 锁后改
        assert orc._require_execution_contract_for_render(sid) is False


class TestHardGate:
    def test_no_contract_blocks_render(self):
        with pytest.raises(ExecutionContractError):
            require_execution_contract(None, "SH1")

    def test_unvalidated_contract_blocks_render(self):
        ec = ExecutionContractCompiler().compile(_plan())   # 未 validate
        with pytest.raises(ExecutionContractError):
            require_execution_contract(ec, "SH1")

    def test_validated_contract_passes_and_returns_shot(self):
        ec = ExecutionContractCompiler().compile(_plan())
        BlockingValidator().validate_and_lock(ec)
        sh = require_execution_contract(ec, "SH2")
        assert sh.shot_id == "SH2" and sh.dialogue_slot == "A"

    def test_tampered_hash_blocks_render(self):
        ec = ExecutionContractCompiler().compile(_plan())
        BlockingValidator().validate_and_lock(ec)
        ec.shots[0].camera.shot_size = "改成大特写"     # 锁后篡改
        with pytest.raises(ExecutionContractError):
            require_execution_contract(ec, "SH1")

    def test_missing_shot_blocks_render(self):
        ec = ExecutionContractCompiler().compile(_plan())
        BlockingValidator().validate_and_lock(ec)
        with pytest.raises(ExecutionContractError):
            require_execution_contract(ec, "NOPE")
