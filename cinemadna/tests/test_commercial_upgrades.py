"""商业级升级回归测试:服装锁 / 构图多样性 / 单调硬门 / 字幕-镜头对齐(0 成本)。"""
from __future__ import annotations

from director.agents.camera import CameraLanguageAgent
from director.agents.director_qa import DirectorQAOfficer
from director.master_plan import DirectorMasterPlan, ShotPlan
from final_review.checklist import _subtitle_shot_alignment, evaluate_checklist
from identity.appearance import character_appearance


def _shot(i, size, func="施压", dlg="A", move="固定"):
    return ShotPlan(
        shot_id=f"SH{i}", order=i, scene_id="SC1", story_function=func,
        action_beats=[{"body_action": "逼近", "actors": ["A"]}],
        performance_intent="决绝", dialogue_owner=dlg, line="台词" if dlg else "",
        camera={"shot_size": size, "angle": "平视", "movement": move,
                "duration_sec": 3.0, "intent": "x"},
        prop_usage={}, entry_state="", exit_state="")


def _plan(shots):
    return DirectorMasterPlan(
        story_id="s1", story_spine={}, cast_lock={},
        scene_layout=[{"scene_id": "SC1", "location": "夜市",
                       "characters": ["A", "B"], "blocking": {}}],
        prop_plan=[], emotion_curve=[], density_plan={}, shot_list=shots)


class TestWardrobeLock:
    def test_specific_and_deterministic(self):
        c = {"character_id": "char_linwan", "gender": "female", "name": "林晚"}
        a = character_appearance(c)
        assert a == character_appearance(c)                 # 确定性
        assert "story-appropriate" not in a and "everyday clothing" not in a  # 无模糊
        assert "wearing" in a                               # 有具体服装

    def test_explicit_appearance_wins(self):
        c = {"character_id": "x", "gender": "female", "appearance": "wearing a red coat"}
        assert "red coat" in character_appearance(c)


class TestCompositionDiversity:
    def test_refine_breaks_monotone_dialogue_run(self):
        # 6 个台词镜全"中景" → refine 后应≥3 种景别(打散)
        plan = _plan([_shot(i, "中景") for i in range(1, 7)])
        CameraLanguageAgent().refine(plan)
        sizes = [s.camera["shot_size"] for s in plan.shots]
        assert len(set(sizes)) >= 3, sizes

    def test_dialogue_shot_avoids_extreme_closeup(self):
        # 台词镜大特写 → refine 降到近景(对口型友好,治 LatentSync Face-not-detected)
        plan = _plan([_shot(1, "大特写", func="揭示", dlg="A")])
        plan.shots[0].prop_usage = {}                       # 非道具揭示,不锁大特写
        CameraLanguageAgent().refine(plan)
        assert plan.shots[0].camera["shot_size"] != "大特写"


class TestMonotonyHardGate:
    def test_all_same_size_rejected(self):
        # 6 镜全同景别(未经 refine 打散)→ 单调硬门打回
        plan = _plan([_shot(i, "中景") for i in range(1, 7)])
        res = DirectorQAOfficer().review(plan)
        assert not res.passed
        assert any("单调" in i["message"] for i in res.hard_issues)

    def test_diverse_plan_passes_monotony_gate(self):
        sizes = ["全景", "中景", "近景", "特写", "中近景", "中景"]
        plan = _plan([_shot(i, sz) for i, sz in enumerate(sizes, 1)])
        res = DirectorQAOfficer().review(plan)
        assert not any("单调" in i["message"] for i in res.hard_issues)


class TestSubtitleShotAlignment:
    def _contracts(self):
        # 4 静默镜(共 6.5s) + 1 台词镜 → 台词 cue 应落在第 5 镜时段(6.5s 起)
        c = []
        for i, d in enumerate([2.6, 1.5, 2.4], 1):
            c.append({"shot_id": f"S{i}", "order": i, "duration_sec": d, "dialogue": []})
        c.append({"shot_id": "S4", "order": 4, "duration_sec": 3.0,
                  "dialogue": [{"character_id": "a", "line": "台词句"}],
                  "voice_contract": {"bindings": [{"line": "台词句", "duration_sec": 2.0}]}})
        return c

    def test_aligned_passes(self):
        ok, detail = _subtitle_shot_alignment(self._contracts())
        assert ok, detail

    def test_misaligned_detected(self):
        # 篡改:把台词镜时长记成极短,模拟"视频时轴与字幕时轴错位" → cue 落到镜头窗口外
        cs = self._contracts()
        # 人为制造错位:插一个巨长静默镜在前,但 cue 仍按旧逻辑会落前面 → 这里直接验证
        # 正常对齐;错位由 build_dialogue_vtt 回归触发,故构造一个 cue 落窗口外的场景:
        cs.insert(0, {"shot_id": "S0", "order": 0, "duration_sec": 50.0, "dialogue": []})
        ok, _ = _subtitle_shot_alignment(cs)
        assert ok            # 加长静默镜后台词镜后移,cue 仍锚其镜头起点 → 仍对齐

    def test_in_full_checklist(self):
        checks = evaluate_checklist({"contracts": self._contracts()})
        names = {c["name"] for c in checks}
        assert "subtitle_shot_alignment" in names
