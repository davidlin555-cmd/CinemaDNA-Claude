"""DirectorDNA + Shot Contract 单元测试 (Phase 3)

三组：
1. Shot Strategy —— 镜头表的电影逻辑与降级规则
2. Shot Contract —— 结构校验、签发、**渲染前置铁规**、防篡改
3. Continuity —— 账本与四类冲突检查
"""

from __future__ import annotations

import copy

import pytest

from asset_brain.common.quad import Quad
from asset_brain.common.store import AssetBrainStore
from director.continuity import build_ledger, check_continuity
from director.service import DirectorDNAService, DirectorError
from director.shot_contract import (
    SHOT_CLOSEUP,
    SHOT_INSERT,
    SHOT_MEDIUM,
    SHOT_REACTION,
    STATUS_DRAFT,
    STATUS_PERFORMANCE_READY,
    RenderNotAllowedError,
    ShotContractError,
    compute_contract_hash,
    new_shot_contract,
    require_render_ready,
    sign_contract,
    validate_contract,
)
from director.strategy import plan_scene_shots, plan_shot_count

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64


def quad(task_id="task_shot_0001") -> Quad:
    return Quad(story_id="drama_0001", bundle_id="bundle_1", task_id=task_id,
                asset_hash=None)


def scene(**overrides):
    base = {
        "scene_id": "EP001_SC001",
        "episode_id": "EP001",
        "beat_type": "CONFLICT",
        "camera_intent": "中近景，压迫感",
        "mood": "压抑",
        "estimated_duration_sec": 20,
        "characters": ["char_a", "char_b"],
        "props": ["prop_x"],
        "prop_states": {"prop_x": "完整"},
        "dialogue": [{"character_id": "char_a", "line": "我没有退路了。"}],
        "spatial_needs": "可支持人物走动与特写",
    }
    base.update(overrides)
    return base


def draft_contract(**overrides):
    kwargs = dict(
        shot_id="EP001_SC001_SH001",
        scene_id="EP001_SC001",
        episode_id="EP001",
        order=1,
        shot_type=SHOT_MEDIUM,
        quad=quad(),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "对峙"},
        duration_sec=5.0,
        emotion={"beat": "CONFLICT", "intensity": 0.8},
        scene_asset={"asset_id": "scene_x", "asset_hash": HASH_A, "gate_passed": True},
        character_assets=[
            {"character_id": "char_a", "master_pack_id": "cmp_0001",
             "asset_hash": HASH_B, "gate_passed": True}
        ],
        prop_assets=[
            {"prop_id": "prop_x", "state": "完整", "asset_id": "p1",
             "asset_hash": HASH_A, "gate_passed": True}
        ],
    )
    kwargs.update(overrides)
    return new_shot_contract(**kwargs)


def ready_contract(**overrides):
    c = draft_contract(**overrides)
    c["performance"] = {"acting_beats": [{"beat_index": 0, "type": "LINE"}]}
    return sign_contract(c)


# ---------------------------------------------------------------------------
# 一、Shot Strategy
# ---------------------------------------------------------------------------


class TestShotStrategy:
    @pytest.mark.parametrize(
        "duration,expected", [(5, 2), (10, 2), (12, 2), (20, 4), (40, 8), (120, 8)]
    )
    def test_shot_count_from_duration(self, duration, expected):
        assert plan_shot_count(duration) == expected

    def test_durations_sum_exactly_to_scene_duration(self):
        for dur in (10, 12, 20, 33, 40):
            shots = plan_scene_shots(scene(estimated_duration_sec=dur), seed="s")
            assert round(sum(s["duration_sec"] for s in shots), 1) == float(dur)

    def test_single_character_scene_has_no_reaction_shot(self):
        """标杆样片是「1 场景 1 主角」——单人场景不能排反应镜。"""
        shots = plan_scene_shots(
            scene(characters=["char_a"], dialogue=[]), seed="s"
        )
        assert all(s["shot_type"] != SHOT_REACTION for s in shots)
        assert all(s["characters"] == ["char_a"] for s in shots if s["characters"])

    def test_scene_without_props_has_no_insert_shot(self):
        shots = plan_scene_shots(
            scene(beat_type="CLIFFHANGER", props=[], prop_states={}), seed="s"
        )
        assert all(s["shot_type"] != SHOT_INSERT for s in shots)

    def test_every_prop_gets_at_least_one_shot(self):
        """道具生成了却一个镜头都没带到，等于白生成。"""
        shots = plan_scene_shots(
            scene(
                beat_type="CONFLICT",  # 模板全是特写/反应镜，不带道具
                props=["prop_x", "prop_y"],
                prop_states={"prop_x": "完整", "prop_y": "被摔裂"},
            ),
            seed="s",
        )
        covered = {p for s in shots for p in s["props"]}
        assert covered == {"prop_x", "prop_y"}

    def test_closeup_focuses_single_character(self):
        shots = plan_scene_shots(scene(), seed="s")
        for s in shots:
            if s["shot_type"] in (SHOT_CLOSEUP, SHOT_REACTION):
                assert len(s["characters"]) == 1

    def test_shot_ids_are_sequential_and_ascii(self):
        shots = plan_scene_shots(scene(), seed="s")
        assert [s["shot_id"] for s in shots][:2] == [
            "EP001_SC001_SH001", "EP001_SC001_SH002"
        ]
        assert all(s["shot_id"].isascii() for s in shots)

    def test_deterministic(self):
        a = plan_scene_shots(scene(), seed="s")
        b = plan_scene_shots(scene(), seed="s")
        assert a == b


# ---------------------------------------------------------------------------
# 二、Shot Contract
# ---------------------------------------------------------------------------


class TestShotContract:
    def test_new_contract_is_draft(self):
        c = draft_contract()
        assert c["status"] == STATUS_DRAFT
        assert c["contract_hash"] is None
        assert c["asset_hash"] is None      # 渲染产物还不存在
        validate_contract(c)

    def test_illegal_shot_type_rejected(self):
        with pytest.raises(ShotContractError):
            draft_contract(shot_type="DRONE")

    def test_out_of_range_duration_rejected(self):
        with pytest.raises(ShotContractError):
            draft_contract(duration_sec=99)
        with pytest.raises(ShotContractError):
            draft_contract(duration_sec=0.2)

    def test_missing_camera_field_rejected(self):
        with pytest.raises(ShotContractError):
            draft_contract(camera={"shot_size": "中景"})

    def test_sign_requires_performance(self):
        c = draft_contract()
        with pytest.raises(ShotContractError):
            sign_contract(c)

    def test_sign_locks_hash(self):
        c = ready_contract()
        assert c["status"] == STATUS_PERFORMANCE_READY
        assert c["contract_hash"] == compute_contract_hash(c)
        require_render_ready(c)

    # -- 渲染前置铁规 ---------------------------------------------------

    def test_draft_contract_cannot_render(self):
        c = draft_contract()
        c["performance"] = {"acting_beats": [{"beat_index": 0}]}
        with pytest.raises(RenderNotAllowedError):
            require_render_ready(c)

    def test_ungated_scene_asset_blocks_render(self):
        c = ready_contract(
            scene_asset={"asset_id": "s", "asset_hash": HASH_A, "gate_passed": False}
        )
        with pytest.raises(RenderNotAllowedError) as ei:
            require_render_ready(c)
        assert "Production Gate" in str(ei.value)

    def test_missing_asset_hash_blocks_render(self):
        c = ready_contract(
            character_assets=[
                {"character_id": "char_a", "master_pack_id": "cmp_1",
                 "asset_hash": None, "gate_passed": True}
            ]
        )
        with pytest.raises(RenderNotAllowedError):
            require_render_ready(c)

    def test_character_without_master_pack_blocks_render(self):
        c = ready_contract(
            character_assets=[
                {"character_id": "char_a", "master_pack_id": None,
                 "asset_hash": HASH_B, "gate_passed": True}
            ]
        )
        with pytest.raises(RenderNotAllowedError):
            require_render_ready(c)

    def test_prop_without_state_blocks_render(self):
        c = ready_contract(
            prop_assets=[
                {"prop_id": "p", "state": "", "asset_id": "a",
                 "asset_hash": HASH_A, "gate_passed": True}
            ]
        )
        with pytest.raises(RenderNotAllowedError) as ei:
            require_render_ready(c)
        assert "穿帮" in str(ei.value)

    def test_shot_without_characters_blocks_render(self):
        c = ready_contract(character_assets=[])
        with pytest.raises(RenderNotAllowedError):
            require_render_ready(c)

    def test_tampered_contract_is_refused(self):
        """签发后改参数（比如偷偷换个没过 Gate 的资产）→ hash 对不上 → 拒渲。"""
        c = ready_contract()
        require_render_ready(c)
        c["assets"]["scene"]["asset_id"] = "另一个场景"
        with pytest.raises(RenderNotAllowedError) as ei:
            require_render_ready(c)
        assert "签发后被修改" in str(ei.value)

    def test_duration_change_after_sign_is_refused(self):
        c = ready_contract()
        c["duration_sec"] = 3.0
        with pytest.raises(RenderNotAllowedError):
            require_render_ready(c)


# ---------------------------------------------------------------------------
# 三、Continuity
# ---------------------------------------------------------------------------


class TestContinuity:
    def _contracts(self):
        a = ready_contract(shot_id="SH1")
        b = ready_contract(shot_id="SH2", order=2, quad=quad("task_shot_0002"))
        return [a, b]

    def test_clean_ledger_passes(self):
        ledger = build_ledger(self._contracts(), story_id="drama_0001",
                              bundle_id="bundle_1")
        report = check_continuity(
            ledger,
            prop_state_order={"prop_x": ["完整", "被撕毁"]},
            scene_prop_states={"EP001_SC001": {"prop_x": "完整"}},
        )
        assert report["passed"] is True

    def test_identity_drift_detected(self):
        cs = self._contracts()
        cs[1]["assets"]["characters"][0]["master_pack_id"] = "cmp_9999"
        ledger = build_ledger(cs, story_id="d", bundle_id="b")
        report = check_continuity(ledger)
        assert report["passed"] is False
        assert any("角色会漂移" in i for i in report["identity_issues"])

    def test_scene_asset_drift_detected(self):
        cs = self._contracts()
        cs[1]["assets"]["scene"]["asset_id"] = "scene_other"
        ledger = build_ledger(cs, story_id="d", bundle_id="b")
        report = check_continuity(ledger)
        assert any("不同的场景资产" in i for i in report["scene_issues"])

    def test_prop_state_regression_detected(self):
        """道具已经被撕毁又变回完整 —— 经典穿帮。"""
        cs = self._contracts()
        cs[0]["assets"]["props"][0]["state"] = "被撕毁"
        cs[1]["assets"]["props"][0]["state"] = "完整"
        ledger = build_ledger(cs, story_id="d", bundle_id="b")
        report = check_continuity(ledger, prop_state_order={"prop_x": ["完整", "被撕毁"]})
        assert any("状态倒退" in i for i in report["prop_issues"])

    def test_prop_state_mismatch_with_script_detected(self):
        cs = self._contracts()
        ledger = build_ledger(cs, story_id="d", bundle_id="b")
        report = check_continuity(
            ledger,
            prop_state_order={"prop_x": ["完整", "被撕毁"]},
            scene_prop_states={"EP001_SC001": {"prop_x": "被撕毁"}},
        )
        assert any("但剧本" in i for i in report["prop_issues"])

    def test_unregistered_state_detected(self):
        cs = self._contracts()
        cs[0]["assets"]["props"][0]["state"] = "被烧成灰"
        ledger = build_ledger(cs, story_id="d", bundle_id="b")
        report = check_continuity(ledger, prop_state_order={"prop_x": ["完整"]})
        assert any("未在剧本登记" in i for i in report["prop_issues"])


# ---------------------------------------------------------------------------
# 四、Director Service 的资产解析
# ---------------------------------------------------------------------------


class TestDirectorAssetResolution:
    def _store(self):
        st = AssetBrainStore()
        st.put_scene_atom("scene_x", {"asset_hash": HASH_A, "gate_passed": True,
                                      "tags": ["出租屋", "深夜"]})
        st.put_character("char_a", {"master_pack_id": "cmp_1", "asset_hash": HASH_B,
                                    "gate_passed": True, "base_face_asset_id": "f1"})
        st.put_prop("prop_x", {"asset_hash": HASH_A, "gate_passed": True,
                               "asset_ids_by_state": {"完整": "p1"}})
        return st

    def test_resolves_all_three(self):
        svc = DirectorDNAService(self._store())
        assert svc.resolve_scene_asset("EP001_SC001", "scene_x")["gate_passed"] is True
        assert svc.resolve_character_asset("char_a")["master_pack_id"] == "cmp_1"
        assert svc.resolve_prop_asset("prop_x", "完整")["asset_id"] == "p1"

    def test_missing_scene_binding_rejected(self):
        svc = DirectorDNAService(self._store())
        with pytest.raises(DirectorError):
            svc.resolve_scene_asset("EP001_SC001", None)

    def test_character_not_in_registry_rejected(self):
        """人物没过 IdentityDNA Gate → Registry 里没有 → 不许签合约。"""
        svc = DirectorDNAService(self._store())
        with pytest.raises(DirectorError) as ei:
            svc.resolve_character_asset("char_clone")
        assert "Character Registry" in str(ei.value)

    def test_missing_prop_state_rejected(self):
        svc = DirectorDNAService(self._store())
        with pytest.raises(DirectorError) as ei:
            svc.resolve_prop_asset("prop_x", "被撕毁")
        assert "穿帮" in str(ei.value)

    def test_run_without_scenes_rejected(self):
        svc = DirectorDNAService(self._store())
        with pytest.raises(DirectorError):
            svc.run({"episodes": []}, story_id="d", bundle_id="b",
                    scene_bindings={}, task_id_factory=lambda: "task_shot_0001")
