"""派任务单接进产线测试 —— 导演验收后每镜作者化 A–K 主任务单 + 登记 + 就地把关(0 成本)。"""

from __future__ import annotations

from director.master_plan import DirectorMasterPlan, ShotPlan
from orchestrator.pipeline import PipelineOrchestrator


def _plan(story_id):
    shots = [
        ShotPlan(shot_id="SC1_SH1", order=1, scene_id="SC1", story_function="钩子",
                 action_beats=[{"body_action": "攥皱缴费单", "actors": ["linwan"]}],
                 performance_intent="压抑", dialogue_owner=None, line="",
                 camera={"shot_size": "全景", "duration_sec": 2.5,
                         "shot_type": "ESTABLISHING", "movement": "固定", "angle": "平"},
                 prop_usage={"prop_id": "bill", "contact": True}, entry_state="紧",
                 exit_state="决"),
        ShotPlan(shot_id="SC1_SH2", order=2, scene_id="SC1", story_function="施压",
                 action_beats=[{"body_action": "上前逼近", "actors": ["linwan"]}],
                 performance_intent="决绝", dialogue_owner="linwan", line="这钱我不还了",
                 camera={"shot_size": "中景", "duration_sec": 3.5, "movement": "手持",
                         "angle": "平"},
                 prop_usage={"prop_id": "iou", "contact": True}, entry_state="决",
                 exit_state="锋"),
    ]
    return DirectorMasterPlan(
        story_id=story_id, story_spine={"conflict": "护士vs高利贷"},
        cast_lock={"linwan": {"role": "护士", "appearance": "white uniform"},
                   "zhao": {"role": "讨债", "appearance": "leather jacket"}},
        scene_layout=[{"scene_id": "SC1", "location": "夜市", "blocking": {},
                       "cause": "追债", "effect": "反击"}],
        prop_plan=[{"prop_id": "bill"}, {"prop_id": "iou"}],
        emotion_curve=[], density_plan={}, shot_list=shots).lock()


def _orc(tmp_path, story_id="drama_0001"):
    orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260728")
    s = orc.create_story(title="t", story_id=story_id)
    plan = _plan(s.story_id)
    orc._master_plans[s.story_id] = plan
    orc._contracts[s.story_id] = [{"shot_id": sp.shot_id} for sp in plan.shots]
    orc._compile_execution_contract(s.story_id, plan)     # 建槽位注册表
    return orc, s.story_id, plan


class TestShotSheetProduction:
    def test_authors_and_registers_sheets(self, tmp_path):
        orc, sid, plan = _orc(tmp_path)
        rep = orc._author_shot_sheets(sid, plan)
        assert rep["authored"] == 2 and rep["all_valid"] is True
        # 登记后每镜可取到主任务单
        assert orc.shot_sheet_for(sid, "SC1_SH1") is not None
        assert orc.shot_sheet_for(sid, "SC1_SH2") is not None
        # 渲染前硬门现在生效(已登记) → 有效单放行
        assert orc._require_shot_sheets_for_render(sid) is True

    def test_sheet_blocks_render_when_missing(self, tmp_path):
        # 登记一个 None 单(模拟缺块未过) → 渲染前硬门拦
        orc, sid, plan = _orc(tmp_path)
        orc.register_shot_sheets(sid, {"SC1_SH1": None, "SC1_SH2": None})
        assert orc._require_shot_sheets_for_render(sid) is False

    def test_authored_sheet_has_all_blocks(self, tmp_path):
        orc, sid, plan = _orc(tmp_path)
        orc._author_shot_sheets(sid, plan)
        sheet = orc.shot_sheet_for(sid, "SC1_SH2")
        # A–K 关键块非空 + 已锁
        assert sheet.validated and sheet.narrative and sheet.action
        assert sheet.dialogue.get("speech") is True         # 有台词镜
        assert sheet.camera and sheet.lighting and sheet.sound

    def test_gate_chain_shows_task_sheet_pass(self, tmp_path):
        orc, sid, plan = _orc(tmp_path)
        orc._author_shot_sheets(sid, plan)                  # 记 SHOT_SHEET_AUTHORED PASS
        rep = orc.gate_chain_report(sid)
        steps = {st["step"]: st for st in rep["steps"]}
        assert steps["分镜任务单(A–K/执行合同)"]["ok"] is True
