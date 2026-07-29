"""PerformanceDNA + Render Brain + 拼接 单元测试 (Phase 3)"""

from __future__ import annotations

import pytest

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from director.shot_contract import (
    SHOT_CLOSEUP,
    SHOT_ESTABLISHING,
    SHOT_INSERT,
    SHOT_MEDIUM,
    SHOT_REACTION,
    STATUS_FAILED,
    STATUS_PERFORMANCE_READY,
    STATUS_RENDERED,
    new_shot_contract,
    sign_contract,
)
from performance.service import PerformanceDNAService, design_micro_expression
from render.assembly import assemble_rough_cut
from render.backend import MockRenderBackend, RenderError
from render.router import (
    MODEL_REGISTRY,
    cost_summary,
    optimize_cost,
    route_shot,
)
from render.service import RenderBrainService

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

SCRIPT = {
    "script_id": "script_test",
    "title": "测试",
    "characters": [
        {"character_id": "char_a", "name": "林晚",
         "personality_keywords": ["压抑", "坚韧"], "role_type": "主角"},
        {"character_id": "char_b", "name": "赵胜",
         "personality_keywords": ["倨傲"], "role_type": "对手"},
    ],
    "props": [{"prop_id": "prop_x", "name": "缴费单", "states_needed": ["完整"]}],
    "episodes": [{"episode_id": "EP001", "scenes": [{
        "scene_id": "EP001_SC001", "episode_id": "EP001", "beat_type": "CONFLICT",
        "spatial_needs": "可走动", "camera_intent": "压迫感",
        "characters": ["char_a", "char_b"], "props": ["prop_x"],
        "prop_states": {"prop_x": "完整"},
    }]}],
}


def make_contract(shot_id="EP001_SC001_SH001", order=1, shot_type=SHOT_MEDIUM,
                  characters=("char_a",), duration=5.0, difficulty="normal",
                  dialogue=None):
    return new_shot_contract(
        shot_id=shot_id,
        scene_id="EP001_SC001",
        episode_id="EP001",
        order=order,
        shot_type=shot_type,
        quad=Quad(story_id="drama_0001", bundle_id="bundle_1",
                  task_id=f"task_shot_{order:04d}", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视",
                "intent": "对峙"},
        duration_sec=duration,
        emotion={"beat": "CONFLICT", "intensity": 0.82, "mood": "压抑"},
        scene_asset={"asset_id": "scene_x", "asset_hash": HASH_A,
                     "gate_passed": True, "tags": ["出租屋", "深夜"]},
        character_assets=[
            {"character_id": c, "master_pack_id": f"cmp_{c}",
             "asset_hash": HASH_B, "gate_passed": True} for c in characters
        ],
        prop_assets=[{"prop_id": "prop_x", "state": "完整", "asset_id": "p1",
                      "asset_hash": HASH_A, "gate_passed": True}],
        dialogue=dialogue or [],
        difficulty=difficulty,
        notes="她被当众收回工牌",
    )


def performed(contracts):
    PerformanceDNAService().run(contracts, shooting_script=SCRIPT)
    return contracts


# ---------------------------------------------------------------------------
# 一、PerformanceDNA
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_dialogue_becomes_line_beats(self):
        c = make_contract(dialogue=[{"character_id": "char_a", "line": "我没有退路了。"}])
        doc = PerformanceDNAService().plan_acting_beats(
            c, {x["character_id"]: x for x in SCRIPT["characters"]}
        )
        assert doc["beats"][0]["type"] == "LINE"
        assert doc["beats"][0]["cue"] == "我没有退路了。"

    def test_others_in_frame_get_reaction_beats(self):
        """有人说话就必然有人听 —— 同框的其他人要有反应节拍。"""
        c = make_contract(
            characters=("char_a", "char_b"),
            dialogue=[{"character_id": "char_a", "line": "你以为我还怕什么？"}],
        )
        doc = PerformanceDNAService().plan_acting_beats(
            c, {x["character_id"]: x for x in SCRIPT["characters"]}
        )
        types = [(b["type"], b["character_id"]) for b in doc["beats"]]
        assert ("LINE", "char_a") in types
        assert ("REACTION", "char_b") in types

    def test_silent_shot_still_gets_a_beat(self):
        """无台词镜头也必须有表演，否则是死画面。"""
        c = make_contract(shot_type=SHOT_ESTABLISHING, dialogue=[])
        doc = PerformanceDNAService().plan_acting_beats(
            c, {x["character_id"]: x for x in SCRIPT["characters"]}
        )
        assert doc["beats"] and doc["beats"][0]["type"] == "ACTION"

    def test_micro_expression_uses_traits(self):
        e = design_micro_expression(["压抑", "坚韧"], 0.8, SHOT_CLOSEUP)
        assert "下颌收紧" in e["primary"]
        assert e["secondary"] is not None
        assert "微表情主导" in e["scale"]

    def test_micro_expression_falls_back_on_intensity(self):
        e = design_micro_expression(["未知性格"], 0.95, SHOT_MEDIUM)
        assert "呼吸加快" in e["primary"]

    def test_bible_accumulates_signature_expressions(self):
        svc = PerformanceDNAService()
        svc.run(performed_contracts := [make_contract(
            dialogue=[{"character_id": "char_a", "line": "我没有退路了。"}]
        )], shooting_script=SCRIPT)
        bible = svc.bibles["char_a"]
        assert bible["signature_expressions"]
        # 引用带 story_id 前缀：shot_id 在不同故事间会重名
        c0 = performed_contracts[0]
        assert f"{c0['story_id']}/{c0['shot_id']}" in bible["source_shots"]

    def test_run_signs_all_contracts(self):
        cs = [make_contract(), make_contract("SH2", 2)]
        result = PerformanceDNAService().run(cs, shooting_script=SCRIPT)
        assert all(c["status"] == STATUS_PERFORMANCE_READY for c in cs)
        assert all(c["contract_hash"] for c in cs)
        assert result.summary()["shots_with_performance"] == 2


# ---------------------------------------------------------------------------
# 二、Model Router / Cost
# ---------------------------------------------------------------------------


class TestRouter:
    def test_closeup_goes_to_face_model(self):
        d = route_shot(make_contract(shot_type=SHOT_CLOSEUP))
        assert d["model"] == "cloud-face-v1" and d["tier"] == "cloud"
        assert d["downgradable"] is False

    def test_insert_goes_local(self):
        d = route_shot(make_contract(shot_type=SHOT_INSERT))
        assert d["tier"] == "local"

    def test_hard_scene_goes_cloud(self):
        d = route_shot(make_contract(shot_type=SHOT_MEDIUM, difficulty="hard"))
        assert d["tier"] == "cloud" and "高难度" in d["reason"]

    def test_cost_optimizer_never_downgrades_faces(self):
        """省钱不能以角色漂移为代价。"""
        cs = [make_contract(f"SH{i}", i, SHOT_CLOSEUP) for i in range(1, 5)]
        decisions = optimize_cost([route_shot(c) for c in cs], max_cloud_ratio=0.0)
        assert all(d["tier"] == "cloud" for d in decisions)
        summary = cost_summary(decisions, max_cloud_ratio=0.0)
        assert summary["over_budget"] is True   # 如实上报超预算，而不是偷偷降级

    def test_cost_optimizer_downgrades_what_it_can(self):
        cs = [make_contract(f"SH{i}", i, SHOT_MEDIUM, difficulty="hard")
              for i in range(1, 5)]
        decisions = optimize_cost([route_shot(c) for c in cs], max_cloud_ratio=0.5)
        assert sum(1 for d in decisions if d["tier"] == "local") == 2

    def test_all_models_registered_with_cost(self):
        for name, spec in MODEL_REGISTRY.items():
            assert spec["cost_per_sec"] > 0 and spec["max_duration_sec"] > 0


# ---------------------------------------------------------------------------
# 三、Render Brain
# ---------------------------------------------------------------------------


class TestRenderBrain:
    def test_happy_path(self, tmp_path):
        bundle = Bundle(tmp_path, "bundle_1").ensure()
        cs = performed([make_contract(), make_contract("SH2", 2, SHOT_CLOSEUP)])
        result = RenderBrainService(bundle=bundle).render_all(cs)

        assert result.ok is True
        assert len(result.rendered) == 2
        assert all(c["status"] == STATUS_RENDERED for c in cs)
        assert all(c["asset_hash"] for c in cs)   # 四元组此时才补全

    def test_downloads_isolated_by_task_id(self, tmp_path):
        """铁规：下载物按 task_id 隔离。"""
        bundle = Bundle(tmp_path, "bundle_1").ensure()
        cs = performed([make_contract(), make_contract("SH2", 2)])
        result = RenderBrainService(bundle=bundle).render_all(cs)
        dirs = {r["file_relpath"].split("/")[1] for r in result.rendered}
        assert dirs == {"task_shot_0001", "task_shot_0002"}
        for r in result.rendered:
            assert (bundle.root / r["file_relpath"]).exists()

    def test_unsigned_contract_is_rejected(self):
        c = make_contract()   # 没跑 Performance，仍是 DRAFT
        result = RenderBrainService().render_all([c])
        assert result.rendered == []
        assert result.rejected and c["status"] == STATUS_FAILED

    def test_ungated_asset_is_rejected(self):
        cs = performed([make_contract()])
        # 模拟"事后把资产换成没过 Gate 的" —— 会同时触发篡改与 Gate 两道拦截
        cs[0]["assets"]["scene"]["gate_passed"] = False
        result = RenderBrainService().render_all(cs)
        assert result.rendered == []
        assert result.rejected

    def test_payload_injects_every_reference_hash(self):
        cs = performed([make_contract(characters=("char_a", "char_b"))])
        d = route_shot(cs[0])
        payload = RenderBrainService.build_payload(cs[0], d)
        refs = payload["reference_hashes"]
        assert refs["scene"] == HASH_A
        assert set(refs["characters"]) == {"char_a", "char_b"}
        assert refs["props"]["prop_x"] == HASH_A
        assert "出租屋" in payload["prompt"]
        assert payload["contract_hash"] == cs[0]["contract_hash"]

    def test_backend_failure_is_recorded(self):
        cs = performed([make_contract(), make_contract("SH2", 2)])
        backend = MockRenderBackend(fail_shots={"SH2"})
        result = RenderBrainService(backend=backend).render_all(cs)
        assert len(result.rendered) == 1
        assert result.failed[0]["shot_id"] == "SH2"
        assert cs[1]["status"] == STATUS_FAILED
        assert result.ok is False

    def test_identity_drift_fails_consistency(self):
        cs = performed([make_contract(), make_contract("SH2", 2)])
        cs[1]["assets"]["characters"][0]["master_pack_id"] = "cmp_other"
        report = RenderBrainService.enforce_consistency(cs, [])
        assert report["passed"] is False
        assert any("必然漂移" in i for i in report["structural_issues"])

    def test_mock_media_is_honest(self, tmp_path):
        bundle = Bundle(tmp_path, "bundle_1").ensure()
        cs = performed([make_contract()])
        result = RenderBrainService(bundle=bundle).render_all(cs)
        r = result.rendered[0]
        assert r["is_real_media"] is False
        assert r["media_type"] == "mock/none"
        assert r["metrics"]["mock"] is True
        assert result.summary()["is_real_media"] is False

    def test_render_is_deterministic(self):
        cs1 = performed([make_contract()])
        cs2 = performed([make_contract()])
        a = RenderBrainService().render_all(cs1).rendered[0]
        b = RenderBrainService().render_all(cs2).rendered[0]
        assert a["seed"] == b["seed"]
        assert a["metrics"] == b["metrics"]

    def test_mock_backend_conforms_to_protocol(self):
        from render.backend import RenderBackend
        assert isinstance(MockRenderBackend(), RenderBackend)


# ---------------------------------------------------------------------------
# 四、拼接
# ---------------------------------------------------------------------------


class TestAssembly:
    def _rendered(self, n=3, dur=5.0):
        cs = performed([make_contract(f"SH{i}", i, duration=dur) for i in range(1, n + 1)])
        RenderBrainService().render_all(cs)
        return cs

    def test_timeline_is_continuous(self):
        cut = assemble_rough_cut(self._rendered(), story_id="d", bundle_id="b")
        prev_out = 0.0
        for e in cut["timeline"]:
            assert e["in_sec"] == prev_out
            prev_out = e["out_sec"]
        assert cut["total_duration_sec"] == prev_out

    def test_duration_target_flag(self):
        short = assemble_rough_cut(self._rendered(2, 5), story_id="d", bundle_id="b")
        assert short["duration_in_target"] is False    # 10s，够不着 30s
        ok = assemble_rough_cut(self._rendered(8, 5), story_id="d", bundle_id="b")
        assert ok["total_duration_sec"] == 40.0
        assert ok["duration_in_target"] is True

    def test_media_ready_is_false_under_mock(self):
        """mock 渲染下绝不能声称素材就绪。"""
        cut = assemble_rough_cut(self._rendered(), story_id="d", bundle_id="b")
        assert cut["media_ready"] is False
        assert all(e["is_real_media"] is False for e in cut["timeline"])

    def test_unrendered_shots_are_listed(self):
        cs = self._rendered(2)
        cs.append(make_contract("SH9", 9))     # 没渲染
        cut = assemble_rough_cut(cs, story_id="d", bundle_id="b")
        assert cut["missing_shots"] == ["SH9"]
        assert cut["shot_count"] == 2

    def test_subtitles_follow_dialogue(self):
        cs = performed([make_contract(
            dialogue=[{"character_id": "char_a", "line": "我没有退路了。"}]
        )])
        RenderBrainService().render_all(cs)
        cut = assemble_rough_cut(cs, story_id="d", bundle_id="b")
        assert cut["subtitles"][0]["text"] == "我没有退路了。"
        assert cut["subtitles"][0]["out_sec"] == cut["timeline"][0]["out_sec"]

    def test_edl_lines_present(self):
        cut = assemble_rough_cut(self._rendered(), story_id="d", bundle_id="b")
        assert len(cut["edl"]) == 3
        assert "00:00.000-00:05.000" in cut["edl"][0]
