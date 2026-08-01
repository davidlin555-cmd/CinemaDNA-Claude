"""ScriptBrain 单元测试 (Phase 2)

五组：
1. Brief 解析与题材归类
2. 生成结构（节拍完整性、确定性、参数生效）
3. 结构契约校验器（ScriptBrain ↔ 资产大脑之间的硬约定）
4. 双 Critic（逻辑 / 连续性 / 拍摄可行性 / 留存）
5. 与 AssetBrainFacade + Orchestrator 的实际对接
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from asset_brain.common import schemas
from asset_brain.common.quad import Quad
from asset_brain.facade import OUTCOME_BACKFLOWED, OUTCOME_REUSED, AssetBrainFacade
from orchestrator.pipeline import OrchestratorError, PipelineOrchestrator
from scriptbrain import (
    GATE_SHOOTING_SCRIPT,
    ScriptBrainService,
    ScriptBrief,
    ScriptBriefError,
    ScriptValidationError,
    match_genre,
    validate_shooting_script,
)
from scriptbrain.agents import market_hook_critic, script_critic
from scriptbrain.library import (
    ALL_GENRES,
    BEAT_CLIFFHANGER,
    BEAT_CONFLICT,
    BEAT_HOOK,
    FALLBACK_GENRE,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "mocks" / "sample_shooting_script.json"

THEME = "县城女护士被高利贷追债，最后逆袭翻身"


@pytest.fixture(scope="module")
def result():
    return ScriptBrainService().run(THEME)


@pytest.fixture(scope="module")
def handwritten() -> dict:
    return json.loads(SCRIPT_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 一、Brief 与题材归类
# ---------------------------------------------------------------------------


class TestBrief:
    def test_coerce_from_plain_string(self):
        b = ScriptBrief.coerce("都市逆袭")
        assert b.theme == "都市逆袭" and b.episode_count == 1

    def test_coerce_from_dict_keeps_extra(self):
        b = ScriptBrief.coerce({"theme": "悬疑", "episode_count": 2, "客户": "某平台"})
        assert b.episode_count == 2 and b.extra == {"客户": "某平台"}

    def test_empty_theme_rejected(self):
        with pytest.raises(ScriptBriefError):
            ScriptBrief(theme="   ")

    def test_too_few_scenes_rejected(self):
        """少于 3 场凑不齐「钩子 + 冲突 + 悬念」，直接拒绝而不是悄悄降级。"""
        with pytest.raises(ScriptBriefError):
            ScriptBrief(theme="x", scenes_per_episode=2)

    def test_episode_bounds(self):
        with pytest.raises(ScriptBriefError):
            ScriptBrief(theme="x", episode_count=0)
        with pytest.raises(ScriptBriefError):
            ScriptBrief(theme="x", episode_count=999)

    @pytest.mark.parametrize(
        "theme,expected",
        [
            ("落魄千金逆袭打脸", "urban_revenge"),
            ("凶手就在这栋楼里，失踪案追凶", "suspense"),
            ("婆媳矛盾与养老困局", "family"),
        ],
    )
    def test_keyword_genre_matching(self, theme, expected):
        assert match_genre(theme).key == expected

    def test_unmatched_theme_falls_back(self):
        assert match_genre("一只猫在下雨天").key == FALLBACK_GENRE.key

    def test_explicit_genre_wins(self):
        r = ScriptBrainService().run(ScriptBrief(theme="随便什么", genre="suspense"))
        assert r.shooting_script["genre"] == "suspense"
        assert r.concept["auto_classified"] is False

    def test_unknown_genre_rejected(self):
        with pytest.raises(KeyError):
            ScriptBrainService().run(ScriptBrief(theme="x", genre="wuxia"))


# ---------------------------------------------------------------------------
# 二、生成结构
# ---------------------------------------------------------------------------


class TestGeneratedStructure:
    def test_every_episode_has_hook_conflict_cliffhanger(self):
        r = ScriptBrainService().run(ScriptBrief(theme=THEME, episode_count=3))
        assert len(r.shooting_script["episodes"]) == 3
        for ep in r.shooting_script["episodes"]:
            beats = [s["beat_type"] for s in ep["scenes"]]
            assert beats[0] == BEAT_HOOK
            assert beats[-1] == BEAT_CLIFFHANGER
            assert BEAT_CONFLICT in beats

    def test_scenes_per_episode_respected(self):
        r = ScriptBrainService().run(ScriptBrief(theme=THEME, scenes_per_episode=6))
        for ep in r.shooting_script["episodes"]:
            assert len(ep["scenes"]) == 6

    def test_scene_ids_unique_and_ascii(self, result):
        ids = [s["scene_id"] for ep in result.shooting_script["episodes"]
               for s in ep["scenes"]]
        assert len(ids) == len(set(ids))
        assert all(i.isascii() for i in ids)
        assert ids[0] == "EP001_SC001"

    def test_ids_are_filesystem_safe(self, result):
        """character_id / prop_id 会变成 Bundle 内的目录名，必须是安全 ASCII。"""
        for c in result.scene_export["characters"]:
            assert c["character_id"].isascii() and " " not in c["character_id"]
        for p in result.scene_export["props"]:
            assert p["prop_id"].isascii() and " " not in p["prop_id"]

    def test_every_character_appears_in_a_scene(self, result):
        for c in result.shooting_script["characters"]:
            assert c["appears_in"], f"{c['character_id']} 一场戏都没有"

    def test_prop_state_timeline_within_declared_states(self, result):
        for p in result.scene_export["props"]:
            declared = set(p["states_needed"])
            assert declared
            for entry in p["state_timeline"]:
                assert entry["state"] in declared
                assert entry["scene_id"]

    def test_dialogue_attached_to_present_characters(self, result):
        for ep in result.shooting_script["episodes"]:
            for s in ep["scenes"]:
                assert s["dialogue"]
                for line in s["dialogue"]:
                    assert line["character_id"] in s["characters"]

    def test_generation_is_deterministic(self):
        a = ScriptBrainService().run(THEME)
        b = ScriptBrainService().run(THEME)
        assert a.script_id == b.script_id
        # created_at 会变，其余内容必须逐字相同
        sa = {k: v for k, v in a.shooting_script.items() if k != "created_at"}
        sb = {k: v for k, v in b.shooting_script.items() if k != "created_at"}
        assert sa == sb

    def test_different_theme_gives_different_script(self):
        a = ScriptBrainService().run(THEME)
        b = ScriptBrainService().run("婆媳矛盾与养老困局")
        assert a.script_id != b.script_id
        assert a.shooting_script["genre"] != b.shooting_script["genre"]

    @pytest.mark.parametrize("genre", [g.key for g in ALL_GENRES])
    @pytest.mark.parametrize("scenes", [3, 4, 5])
    def test_every_genre_produces_a_castable_script(self, genre, scenes):
        """回归网：任何题材 × 任何场景数，都不能生成"有人没戏"的剧本。

        （曾经的真实 bug：家庭伦理三场戏时先取了配角、漏掉对手，
        导致配角一场戏都没有，被 Script Critic 抓出来。）
        """
        r = ScriptBrainService().run(
            ScriptBrief(theme="回归测试", genre=genre, scenes_per_episode=scenes)
        )
        assert r.script_review["continuity_issues"] == []
        for c in r.shooting_script["characters"]:
            assert c["appears_in"], f"{genre}/{scenes}场: {c['character_id']} 没有戏"
        # 冲突场必须有对手在场，否则冲突无从发生
        conflict = next(
            s for s in r.shooting_script["episodes"][0]["scenes"]
            if s["beat_type"] == BEAT_CONFLICT
        )
        assert len(conflict["characters"]) >= 2

    def test_summary_shape(self, result):
        s = result.summary()
        assert s["scenes"] == 3 and s["episodes"] == 1
        assert s["needs_human_review"] is False
        assert 0.0 <= s["retention_score"] <= 1.0


# ---------------------------------------------------------------------------
# 三、结构契约校验器
# ---------------------------------------------------------------------------


class TestValidator:
    def test_generated_script_is_valid(self, result):
        assert validate_shooting_script(result.shooting_script) is result.shooting_script

    def test_handwritten_fixture_is_valid(self, handwritten):
        """人手写的假剧本走同一道校验 —— 契约对内对外一致。"""
        validate_shooting_script(handwritten)

    def test_wrong_schema_version_rejected(self, result):
        bad = {**result.shooting_script, "schema_version": "cinemadna.workorder.v1"}
        with pytest.raises(schemas.SchemaValidationError):
            validate_shooting_script(bad)

    def test_missing_required_scene_field_rejected(self, result):
        bad = copy.deepcopy(result.shooting_script)
        del bad["episodes"][0]["scenes"][0]["required_atoms"]
        with pytest.raises(ScriptValidationError) as ei:
            validate_shooting_script(bad)
        assert "required_atoms" in str(ei.value)

    def test_non_ascii_character_id_rejected(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["characters"][0]["character_id"] = "char_林晚"
        with pytest.raises(ScriptValidationError):
            validate_shooting_script(bad)

    def test_duplicate_scene_id_rejected(self, result):
        bad = copy.deepcopy(result.shooting_script)
        scenes = bad["episodes"][0]["scenes"]
        scenes[1]["scene_id"] = scenes[0]["scene_id"]
        with pytest.raises(ScriptValidationError):
            validate_shooting_script(bad)

    def test_undeclared_character_reference_rejected(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["episodes"][0]["scenes"][0]["characters"] = ["char_ghost"]
        with pytest.raises(ScriptValidationError):
            validate_shooting_script(bad)

    def test_empty_episodes_rejected(self, result):
        bad = {**result.shooting_script, "episodes": []}
        with pytest.raises(ScriptValidationError):
            validate_shooting_script(bad)

    def test_prop_without_states_rejected(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["props"][0]["states_needed"] = []
        with pytest.raises(ScriptValidationError):
            validate_shooting_script(bad)


# ---------------------------------------------------------------------------
# 四、双 Critic
# ---------------------------------------------------------------------------


class TestCritics:
    def test_clean_script_passes_both(self, result):
        assert result.script_review["passed"] is True
        assert result.market_review["passed"] is True
        assert result.gate3["passed"] is True

    def test_hard_scene_is_caught(self):
        """水下打捞这类场景当前生成能力做不了，必须在剧本阶段就拦下。"""
        r = ScriptBrainService().run(
            ScriptBrief(theme="悬疑追凶：河边失踪案", scenes_per_episode=4)
        )
        assert r.script_review["passed"] is False
        assert r.script_review["production_difficulty"]["level"] == "hard"
        assert r.needs_human_review is True
        assert any("水下" in i for i in r.gate3["issues"])

    def test_missing_cliffhanger_flagged(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["episodes"][0]["scenes"][-1]["beat_type"] = BEAT_CONFLICT
        report = script_critic(bad)
        assert report["passed"] is False
        assert any("悬念" in i for i in report["logic_issues"])

    def test_unregistered_prop_state_flagged(self, result):
        bad = copy.deepcopy(result.shooting_script)
        scene = bad["episodes"][0]["scenes"][0]
        pid = next(iter(scene["prop_states"]))
        scene["prop_states"][pid] = "被烧成灰"
        report = script_critic(bad)
        assert any("未登记状态" in i for i in report["continuity_issues"])

    def test_character_with_no_scenes_flagged(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["characters"].append(
            {"character_id": "char_idle", "name": "闲人", "role_type": "配角"}
        )
        report = script_critic(bad)
        assert any("一场戏都没有" in i for i in report["continuity_issues"])

    def test_undeclared_reference_flagged(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["episodes"][0]["scenes"][0]["props"].append("prop_ghost")
        report = script_critic(bad)
        assert any("未声明的道具" in i for i in report["continuity_issues"])

    def test_market_critic_punishes_missing_hook(self, result):
        bad = copy.deepcopy(result.shooting_script)
        bad["episodes"][0]["scenes"][0]["beat_type"] = BEAT_CONFLICT
        report = market_hook_critic(bad)
        assert report["passed"] is False
        assert report["hook_strength"] < 0.5

    def test_critic_handles_foreign_script(self, handwritten):
        """外来剧本（人写的）也能过 Critic —— 它不依赖我们的生成器。"""
        report = script_critic(handwritten)
        assert isinstance(report["issues"], list)
        assert report["schema_version"] == schemas.SCHEMA_SCRIPT_REVIEW


# ---------------------------------------------------------------------------
# 五、与资产大脑 / Orchestrator 对接
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_scene_export_consumed_by_facade_directly(self, result, tmp_path):
        """scene_export 不经 Orchestrator 也能被 AssetBrainFacade 直接消费。"""
        fac = AssetBrainFacade()
        q = lambda t: Quad(story_id="drama_0001", bundle_id="bundle_1",
                           task_id=t, asset_hash=None)
        exp = result.scene_export
        outcomes = []
        for i, sc in enumerate(exp["scenes"]):
            outcomes.append(fac.request_scene(sc, q(f"task_scenedna_{i:03d}"))["outcome"])
        for i, ch in enumerate(exp["characters"]):
            outcomes.append(fac.request_identity(ch, q(f"task_identitydna_{i:03d}"))["outcome"])
        for i, pr in enumerate(exp["props"]):
            outcomes.append(
                fac.request_prop(pr, q(f"task_propdna_{i:03d}"),
                                 state_timeline=pr["state_timeline"])["outcome"]
            )
        assert set(outcomes) == {OUTCOME_BACKFLOWED}

    def test_orchestrator_run_scriptbrain_emits_parallel_signal(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s = orc.create_story(title="A")
        res = orc.run_scriptbrain(s.story_id, THEME)

        assert res["state"] == "SCRIPT_DONE"
        assert res["parallel_signal"]["new_story_allowed"] is True
        story = orc.get_story(s.story_id)
        assert story["script_ref"] == "01_script/shooting_script.json"
        assert story["script_summary"]["scenes"] == 3

    def test_scriptbrain_artifacts_land_in_bundle(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s = orc.create_story(title="A")
        res = orc.run_scriptbrain(s.story_id, THEME)
        root = orc.bundle_for(s.story_id).root

        for rel in (
            "01_script/concept_brief.json",
            "01_script/show_bible.json",
            "01_script/episode_outline.json",
            "01_script/shooting_script.json",
            "01_script/scene_export.json",
            "01_script/character_list.json",
            f"11_review/script_review/{res['script_id']}.json",
            f"11_review/market_review/{res['script_id']}.json",
        ):
            assert (root / rel).exists(), f"缺少 {rel}"

        on_disk = json.loads((root / "01_script/shooting_script.json").read_text("utf-8"))
        validate_shooting_script(on_disk)

    def test_full_chain_theme_to_backflow(self, tmp_path):
        """一句题材 → 剧本 → 三大资产全部回流。"""
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)
        res = orc.dispatch_assets(s.story_id)  # 不必再传剧本

        assert res["state"] == "ASSET_READY"
        assert set(res["summary"]["by_outcome"]) == {OUTCOME_BACKFLOWED}
        st = orc.store.stats()
        assert st["scene_atoms"] == 3 and st["characters"] == 2 and st["props"] == 2

    def test_prop_continuity_driven_by_script(self, tmp_path):
        """剧本里的跨场景状态时间线真正驱动了 PropDNA 的连续性。"""
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        s = orc.create_story(title="A")
        orc.run_scriptbrain(s.story_id, THEME)
        orc.dispatch_assets(s.story_id)

        rec = orc.store.prop_library["prop_hospital_bill"]
        assert rec["continuity_supported"] is True
        wo = next(
            w for w in orc.store.list_workorders(workorder_type="PROP")
            if w.requirement["prop_id"] == "prop_hospital_bill"
        )
        timeline = wo.generation_plan["state_timeline"]
        assert len(timeline) >= 2
        # 时间线锚在剧本场景上，且每个状态都在需求里登记过
        assert all(t["scene_id"].startswith("EP001_SC") for t in timeline)
        assert {t["state"] for t in timeline} == set(wo.requirement["states_needed"])

    def test_dispatch_without_script_rejected(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path)
        s = orc.create_story(title="A")
        orc.start_script(s.story_id)
        orc.submit_script(s.story_id, script_ref="external.json")
        with pytest.raises(OrchestratorError):
            orc.dispatch_assets(s.story_id)

    def test_gate3_failure_holds_story_without_blocking_others(self, tmp_path):
        """剧本闸门没过 → 挂人工，但槽位释放，别的剧照开。"""
        orc = PipelineOrchestrator(
            bundle_root=tmp_path, bundle_date="20260723", max_concurrent_scripts=1
        )
        a = orc.create_story(title="A")
        res = orc.run_scriptbrain(
            a.story_id, ScriptBrief(theme="悬疑追凶：河边失踪案", scenes_per_episode=4)
        )
        assert res["parallel_signal"] is None
        assert res["state"] == "WAITING_HUMAN"
        assert orc.pending_holds()[0]["reason"] == "script_gate3"

        # 槽位已释放，B 可以立刻开工
        b = orc.create_story(title="B")
        orc.run_scriptbrain(b.story_id, THEME)
        assert orc.get_story(b.story_id)["stage"] == "SCRIPT_DONE"

        # 人工放行 A → 此时才提交并产生并行信号
        done = orc.approve_script_gate(a.story_id, {"approved": True, "decided_by": "human_lin"})
        assert done["approved"] is True
        assert done["parallel_signal"]["new_story_allowed"] is True
        assert orc.get_story(a.story_id)["stage"] == "SCRIPT_DONE"

    def test_gate3_rejection_keeps_story_in_script_stage(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723")
        a = orc.create_story(title="A")
        orc.run_scriptbrain(
            a.story_id, ScriptBrief(theme="悬疑追凶：河边失踪案", scenes_per_episode=4)
        )
        out = orc.approve_script_gate(a.story_id, {"approved": False, "decided_by": "human_lin"})
        assert out["approved"] is False and out["parallel_signal"] is None
        assert orc.get_story(a.story_id)["stage"] == "SCRIPT_RUNNING"

        # 重跑一版合格剧本即可继续
        again = orc.run_scriptbrain(a.story_id, THEME)
        assert again["parallel_signal"]["new_story_allowed"] is True

    def test_second_story_same_theme_reuses_everything(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723",
                                   max_concurrent_scripts=2)
        a = orc.create_story(title="A")
        orc.run_scriptbrain(a.story_id, THEME)
        orc.dispatch_assets(a.story_id)
        before = len(orc.store.workorders)

        b = orc.create_story(title="B")
        orc.run_scriptbrain(b.story_id, THEME)
        res = orc.dispatch_assets(b.story_id)
        assert set(res["summary"]["by_outcome"]) == {OUTCOME_REUSED}
        assert len(orc.store.workorders) == before

    def test_gate3_is_human_overridable_unlike_identity_redline(self, tmp_path):
        """对照：剧本闸门人工可放行；IdentityDNA 红线人工不可放行。"""
        r = ScriptBrainService().run(
            ScriptBrief(theme="悬疑追凶：河边失踪案", scenes_per_episode=4)
        )
        gate3 = next(g for g in r.gates if g["gate"] == GATE_SHOOTING_SCRIPT)
        assert gate3["passed"] is False
        assert gate3["human_overridable"] is True
