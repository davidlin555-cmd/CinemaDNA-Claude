"""DirectorDNA 宪法层测试：总谱生成/验收/锁定/发单/无谱禁工（0 成本）。"""

from __future__ import annotations

import pytest

from director.dna_service import DirectorPlanningService
from director.broadcast import plan_to_shots
from director.gates import require_locked_plan, DirectorGateError
from director.master_plan import DirectorMasterPlan, STORY_FUNCTIONS


def _script():
    def beat(t, sec, desc, cid=None, line="", emo="", shift=""):
        return {"type": t, "seconds": sec, "description": desc,
                "character_id": cid, "line": line, "emotion": emo, "internal_shift": shift}
    scene1 = {
        "scene_id": "EP001_SC001", "mood": "压抑", "beat_type": "HOOK",
        "location_description": "县城出租屋·深夜",
        "characters": ["char_linwan", "char_zhao"],
        "narrative": {"cause": "欠债", "effect": "被逼面对", "entry_state": "疲惫",
                      "exit_state": "决意", "emotional_tone": "压抑"},
        "beats": [
            beat("setup", 3, "林晚翻出攥皱的缴费单，盯着金额", "char_linwan", "", "绝望",
                 "看单→手指收紧→闭眼"),
            beat("info", 2, "手机弹出催债短信", "char_linwan", "又来了", "恐惧"),
            beat("interruption", 3, "赵胜踹门而入打断她", "char_zhao", "钱呢", "威胁"),
            beat("obstruction", 2, "林晚后退撞到桌角，无路可退", None, "", "无助"),
            beat("counterattack", 3, "林晚抓起录音笔怼到赵胜脸前", "char_linwan",
                 "我全录了", "决绝"),
            beat("reversal", 3, "赵胜脸色骤变", None, "", "惊恐"),
            beat("cliffhanger", 3, "警灯映上墙，林晚冷笑", "char_linwan", "你完了", "胜利"),
        ]}
    return {"title": "催债逆袭", "logline": "护士反杀讨债人", "theme": "高利贷催债逆袭",
            "characters": [{"character_id": "char_linwan", "name": "林晚", "role": "护士"},
                           {"character_id": "char_zhao", "name": "赵胜", "role": "讨债人"}],
            "props": [{"prop_id": "prop_bill", "name": "缴费单",
                       "states_needed": ["逾期"]}],
            "episodes": [{"episode_id": "EP001", "scenes": [scene1]}]}


class TestDirectorMasterPlan:
    def test_plan_produced_qa_passed_and_locked(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        assert res.qa.passed, res.qa.report()
        assert res.plan.locked and res.plan.verify_lock()
        assert res.plan.plan_hash.startswith("sha256:")

    def test_every_shot_has_function_and_executable_action(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        for sp in res.plan.shots:
            assert sp.story_function in STORY_FUNCTIONS
            assert sp.action_beats and sp.action_beats[0]["body_action"]
            assert sp.camera.get("shot_size") and sp.camera.get("duration_sec")

    def test_cast_power_locked(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        assert res.plan.cast_lock["char_zhao"]["power"] == "高"      # 讨债人
        assert res.plan.cast_lock["char_linwan"]["power"] == "低"    # 护士/欠债

    def test_hash_tamper_detected(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        res.plan.shot_list[0].story_function = "施压"    # 篡改
        assert res.plan.verify_lock() is False


class TestBroadcast:
    def test_broadcast_carries_plan_hash_and_function(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        shots = plan_to_shots(res.plan)
        assert len(shots) == len(res.plan.shots)
        for sh in shots:
            assert sh["plan_hash"] == res.plan.plan_hash          # 可追溯
            assert sh["story_function"] and sh["action_beats"]
        # 有台词的镜绝不落无声景别
        for sh in shots:
            if sh["dialogue"]:
                assert sh["shot_type"] not in ("REACTION", "INSERT", "ESTABLISHING")

    def test_in_frame_follows_staging_actors_not_scene_cast(self):
        # 身份漂移根因回归：broadcast 的「谁在画面」必须跟随 staging 写入的
        # action_beats[].actors(与执行合同 in_frame_slots 同源),不能按整场 cast 重算,
        # 否则①正反打单人中景被当双人 → character_images 双人 → 漏锁 PuLID → 同角色多脸。
        from director.master_plan import DirectorMasterPlan, ShotPlan
        plan = DirectorMasterPlan(
            story_id="s1", story_spine={}, cast_lock={},
            scene_layout=[{"scene_id": "SC1", "location": "夜市",
                           "characters": ["A", "B"], "blocking": {}}],
            prop_plan=[], emotion_curve=[], density_plan={},
            shot_list=[
                ShotPlan(shot_id="SH1", order=1, scene_id="SC1",
                         story_function="施压",     # 对峙,但 staging 定为单人正打
                         action_beats=[{"body_action": "逼近", "actors": ["A"]}],
                         performance_intent="决绝", dialogue_owner="A", line="别逼我",
                         camera={"shot_size": "中景", "duration_sec": 3.0},
                         prop_usage={}, entry_state="", exit_state=""),
                ShotPlan(shot_id="SH2", order=2, scene_id="SC1", story_function="揭示",
                         action_beats=[{"body_action": "", "actors": []}],  # 插入镜无脸
                         performance_intent="", dialogue_owner=None, line="",
                         camera={"shot_size": "大特写", "duration_sec": 2.0},
                         prop_usage={}, entry_state="", exit_state=""),
            ]).lock()
        shots = {s["shot_id"]: s for s in plan_to_shots(plan)}
        assert shots["SH1"]["characters"] == ["A"]     # 单人正打,非整场["A","B"]
        assert shots["SH2"]["characters"] == []        # 插入镜无脸


class TestGate:
    def test_no_plan_blocks_work(self):
        with pytest.raises(DirectorGateError):
            require_locked_plan(None, action="资产生产")

    def test_unlocked_plan_blocks_work(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        res.plan.locked = False
        with pytest.raises(DirectorGateError):
            require_locked_plan(res.plan, action="资产生产")

    def test_locked_plan_allows_work(self):
        res = DirectorPlanningService().plan(_script(), story_id="s1")
        assert require_locked_plan(res.plan, action="资产生产") is res.plan
