"""剧本驱动多模型协同：Contract V5 + Cross-Validation + PreRender Gate 测试

对应 PDF《剧本驱动多模型协同生产》。重点：12 项就绪总闸、模型互检拦住
"素材拼接感"、没过 gate 禁止渲染、V5 子合同完整。
"""

from __future__ import annotations

import pytest

from asset_brain.common.quad import Quad
from director.shot_contract import (
    MOUTH_SILENT_CLOSED,
    SHOT_CLOSEUP,
    SHOT_MEDIUM,
    new_shot_contract,
)
from performance.service import PerformanceDNAService
from prerender.contract_v5 import contract_v5_view, enrich_contract_v5
from prerender.cross_validation import cross_model_validate
from director.shot_contract import SHOT_CLOSEUP as _SC  # noqa
from prerender.gate import BLOCKED, FLAGS, READY, run_prerender_gate

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

CHARS = {
    "char_a": {"character_id": "char_a", "name": "林晚", "gender": "female",
               "age_range": "25-28", "personality_keywords": ["压抑"], "role_type": "主角"},
    "char_b": {"character_id": "char_b", "name": "赵胜", "gender": "male",
               "age_range": "40-45", "personality_keywords": ["倨傲"], "role_type": "对手"},
}
SCRIPT = {"script_id": "s1", "characters": list(CHARS.values()),
          "episodes": [{"episode_id": "EP001", "scenes": []}]}


def contract(shot_id="SH1", order=1, shot_type=SHOT_MEDIUM, beat="CONFLICT",
             chars=("char_a",), master="cmp_a", dialogue=None, mouth=None,
             face="02_cast/char_a/face.png", props=None):
    c = new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001", order=order,
        shot_type=shot_type,
        quad=Quad(story_id="drama_0001", bundle_id="b1", task_id=f"t{order}", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "对峙"},
        duration_sec=5.0, emotion={"beat": beat, "intensity": 0.8, "mood": "压抑"},
        scene_asset={"asset_id": "sc", "asset_hash": HASH_A, "gate_passed": True, "tags": []},
        character_assets=[{"character_id": ch, "master_pack_id": master,
                           "asset_hash": HASH_B, "gate_passed": True,
                           "base_face_image": face} for ch in chars],
        prop_assets=props or [], dialogue=dialogue or [], mouth_policy=mouth,
        notes="她被当众收回工牌")
    enrich_contract_v5(c, characters_by_id=CHARS)
    PerformanceDNAService().run([c], shooting_script={
        **SCRIPT, "episodes": [{"episode_id": "EP001", "scenes": [
            {"scene_id": "EP001_SC001", "characters": list(chars), "props": [],
             "prop_states": {}, "spatial_needs": "x", "camera_intent": "x",
             "beat_type": beat}]}]})
    return c


# ---------------------------------------------------------------------------
# Contract V5
# ---------------------------------------------------------------------------


class TestContractV5:
    def test_enrich_adds_subcontracts(self):
        c = contract(dialogue=[{"character_id": "char_a", "line": "我没退路了"}])
        assert c["story_function"].startswith("[CONFLICT]")
        assert c["route_contract"]["model"]
        assert c["qa_contract"]
        vc = c["voice_contract"]
        assert vc["has_dialogue"] and vc["bindings"][0]["voice_gender"] == "female"

    def test_v5_view_structure(self):
        c = contract(dialogue=[{"character_id": "char_a", "line": "x"}])
        v = contract_v5_view(c)
        for k in ("story_function", "scene_contract", "identity_contract",
                  "prop_contract", "performance_contract", "dialogue_contract",
                  "voice_contract", "mouth_policy", "camera_contract",
                  "route_contract", "qa_contract"):
            assert k in v
        assert v["identity_contract"]["face_images"] == ["02_cast/char_a/face.png"]


# ---------------------------------------------------------------------------
# Cross-Model Validation
# ---------------------------------------------------------------------------


class TestCrossValidation:
    def test_clean_passes_with_pending(self):
        r = cross_model_validate([contract()], characters_by_id=CHARS)
        assert r["passed"] is True
        assert r["pending"]        # 多模态项待接入

    def test_identity_drift_caught(self):
        cs = [contract("SH1", 1, master="cmp_a"),
              contract("SH2", 2, master="cmp_b")]    # 同角色不同 Master Pack
        r = cross_model_validate(cs, characters_by_id=CHARS)
        assert r["passed"] is False
        assert any(i["code"] == "IDENTITY_DRIFT" for i in r["issues"])

    def test_voice_gender_mismatch_caught(self):
        """男主的台词却绑成女声 → Dialogue→Voice 互检拦下。"""
        c = contract(chars=("char_b",), master="cmp_b", face="02_cast/char_b/face.png",
                     dialogue=[{"character_id": "char_b", "line": "签了它"}])
        c["voice_contract"]["bindings"][0]["voice_gender"] = "female"  # 篡改成女声
        r = cross_model_validate([c], characters_by_id=CHARS)
        assert any(i["code"] == "VOICE_GENDER_MISMATCH" for i in r["issues"])

    def test_speaker_not_in_frame_caught(self):
        c = contract(chars=("char_a",),
                     dialogue=[{"character_id": "char_b", "line": "x"}],  # 说话人不在画面
                     mouth=MOUTH_SILENT_CLOSED)
        # 让它带台词但说话人 char_b 不在场，且非画外音
        c["voice_contract"] = {"has_dialogue": True, "bindings": [
            {"speaker": "char_b", "voice_gender": "male", "age_feel": "40",
             "line": "x", "mode": c["mouth_policy"]}]}
        r = cross_model_validate([c], characters_by_id=CHARS)
        assert any(i["code"] == "SPEAKER_NOT_IN_FRAME" for i in r["issues"])


# ---------------------------------------------------------------------------
# PreRender Commercial Gate V2
# ---------------------------------------------------------------------------


class TestRealAssetValidation:
    """真实角色资产进入 Contract + PreRender Gate 被真实校验（Phase 1）。"""

    def _checker(self, existing):
        """asset_checker：existing 是"存在且够大"的相对路径集合。"""
        def check(rel):
            if rel in existing:
                return {"exists": True, "bytes": 150000}
            return {"exists": False, "bytes": 0}
        return check

    def test_real_face_present_passes(self):
        c = contract(face="02_cast/char_a/face.png")
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 asset_checker=self._checker({"02_cast/char_a/face.png"}))
        assert r["passed"] is True

    def test_declared_face_missing_blocks(self):
        """声明了真实人脸图但文件不存在 → 拦下（防断链）。"""
        c = contract(face="02_cast/char_a/face.png")
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 asset_checker=self._checker(set()))  # 文件不存在
        assert r["passed"] is False
        assert any(i["code"] == "REAL_FACE_MISSING" for i in r["issues"])

    def test_tiny_face_flagged_as_invalid(self):
        c = contract(face="02_cast/char_a/face.png")
        def check(rel):
            return {"exists": True, "bytes": 200}     # 过小
        r = cross_model_validate([c], characters_by_id=CHARS, asset_checker=check)
        assert any(i["code"] == "REAL_FACE_INVALID" for i in r["issues"])

    def test_real_production_requires_face(self):
        """真实生产模式：能看脸的镜头缺真实人脸图 → 拦下。"""
        c = contract(shot_type=SHOT_CLOSEUP, face=None)   # 无真实人脸
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 require_real_faces=True)
        assert any(i["code"] == "NO_REAL_FACE" for i in r["issues"])

    def test_mock_mode_does_not_require_face(self):
        """mock/开发模式：不强求真实人脸（老行为保留）。"""
        c = contract(shot_type=SHOT_CLOSEUP, face=None)
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 require_real_faces=False)
        assert not any(i["code"] == "NO_REAL_FACE" for i in r["issues"])

    def test_gate_blocks_on_missing_real_face(self):
        c = contract(face="02_cast/char_a/face.png")
        r = run_prerender_gate([c], story_id="d1", characters_by_id=CHARS,
                               asset_checker=self._checker(set()))
        assert r.status == BLOCKED
        assert r.flags["cross_model_validation_ready"] == BLOCKED

    # -- 真实场景/道具（Phase 2）------------------------------------------

    def test_real_scene_present_passes(self):
        """声明真实场景图且文件存在 → 通过。"""
        c = contract(face="02_cast/char_a/face.png")
        c["assets"]["scene"]["scene_image"] = "03_scene/EP001_SC001/scene.png"
        r = cross_model_validate([c], characters_by_id=CHARS,
            asset_checker=self._checker(
                {"02_cast/char_a/face.png", "03_scene/EP001_SC001/scene.png"}))
        assert r["passed"] is True

    def test_declared_scene_missing_blocks(self):
        c = contract(face="02_cast/char_a/face.png")
        c["assets"]["scene"]["scene_image"] = "03_scene/EP001_SC001/scene.png"
        r = cross_model_validate([c], characters_by_id=CHARS,
            asset_checker=self._checker({"02_cast/char_a/face.png"}))  # 场景图缺
        assert r["passed"] is False
        assert any(i["code"] == "REAL_SCENE_MISSING" for i in r["issues"])

    def test_real_production_requires_scene(self):
        """真实生产模式：缺真实场景图 → 拦下。"""
        c = contract(face="02_cast/char_a/face.png")   # scene 无 scene_image
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 require_real_assets=True)
        assert any(i["code"] == "NO_REAL_SCENE" for i in r["issues"])

    def test_real_prop_present_passes(self):
        props = [{"prop_id": "prop_x", "asset_hash": HASH_A, "gate_passed": True,
                  "state": "完整", "prop_image": "04_props/prop_x/完整.png"}]
        c = contract(face="02_cast/char_a/face.png", props=props)
        c["assets"]["scene"]["scene_image"] = "03_scene/EP001_SC001/scene.png"
        r = cross_model_validate([c], characters_by_id=CHARS,
            asset_checker=self._checker({
                "02_cast/char_a/face.png", "03_scene/EP001_SC001/scene.png",
                "04_props/prop_x/完整.png"}))
        assert r["passed"] is True

    def test_declared_prop_missing_blocks(self):
        props = [{"prop_id": "prop_x", "asset_hash": HASH_A, "gate_passed": True,
                  "state": "完整", "prop_image": "04_props/prop_x/完整.png"}]
        c = contract(face="02_cast/char_a/face.png", props=props)
        r = cross_model_validate([c], characters_by_id=CHARS,
            asset_checker=self._checker({"02_cast/char_a/face.png"}))  # 道具图缺
        assert any(i["code"] == "REAL_PROP_MISSING" for i in r["issues"])

    def test_real_production_requires_prop(self):
        props = [{"prop_id": "prop_x", "asset_hash": HASH_A, "gate_passed": True,
                  "state": "完整"}]                     # 无 prop_image
        c = contract(face="02_cast/char_a/face.png", props=props)
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 require_real_assets=True)
        assert any(i["code"] == "NO_REAL_PROP" for i in r["issues"])

    def test_mock_mode_does_not_require_scene_or_prop(self):
        props = [{"prop_id": "prop_x", "asset_hash": HASH_A, "gate_passed": True,
                  "state": "完整"}]
        c = contract(face="02_cast/char_a/face.png", props=props)
        r = cross_model_validate([c], characters_by_id=CHARS,
                                 require_real_assets=False)
        codes = {i["code"] for i in r["issues"]}
        assert "NO_REAL_SCENE" not in codes and "NO_REAL_PROP" not in codes

    def test_asset_checker_accepts_absolute_path(self, tmp_path):
        """跨剧回流复用：场景图存的是绝对路径，checker 也能核验。"""
        from prerender.service import PreRenderService
        from asset_brain.common.bundle import Bundle
        bundle = Bundle(tmp_path, "b_abs").ensure()
        real = bundle.path_for("03_scene/EP001_SC001/scene.png")
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_bytes(b"\x89PNG\r\n" + b"\x00" * 4000)
        checker = PreRenderService(bundle=bundle)._asset_checker()
        assert checker(str(real.resolve()))["exists"] is True   # 绝对路径
        assert checker("03_scene/EP001_SC001/scene.png")["exists"] is True  # 相对路径


class TestPreRenderGate:
    def test_all_flags_ready(self):
        r = run_prerender_gate([contract(), contract("SH2", 2)],
                               story_id="d1", characters_by_id=CHARS)
        assert r.status == READY
        assert set(r.flags) == set(FLAGS)
        assert all(v == READY for v in r.flags.values())

    def test_no_contracts_blocked(self):
        r = run_prerender_gate([], story_id="d1")
        assert r.status == BLOCKED and r.next_action

    def test_identity_drift_blocks_gate(self):
        cs = [contract("SH1", 1, master="cmp_a"),
              contract("SH2", 2, master="cmp_b")]
        r = run_prerender_gate(cs, story_id="d1", characters_by_id=CHARS)
        assert r.status == BLOCKED
        assert r.flags["cross_model_validation_ready"] == BLOCKED

    def test_script_not_ready_blocks(self):
        r = run_prerender_gate([contract()], story_id="d1",
                               characters_by_id=CHARS, script_ready=False)
        assert r.flags["script_contract_ready"] == BLOCKED
        assert r.status == BLOCKED

    def test_report_shape(self):
        r = run_prerender_gate([contract()], story_id="d1", characters_by_id=CHARS)
        rep = r.report()
        assert rep["schema_version"] == "cinemadna.prerender_gate.v2"
        assert len(rep["flags"]) == 12


# ---------------------------------------------------------------------------
# 整条管线：没过 PreRender Gate 禁止渲染
# ---------------------------------------------------------------------------


class TestPipelineGuard:
    def _to_performance(self, orc, sid):
        orc.run_scriptbrain(sid, "县城女护士被高利贷追债，最后逆袭翻身")
        orc.dispatch_assets(sid)
        orc.run_director(sid); orc.run_performance(sid)

    def test_gate_runs_and_passes(self, tmp_path):
        from orchestrator import PipelineOrchestrator
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        self._to_performance(orc, s.story_id)
        res = orc.run_prerender_gate(s.story_id)
        assert res["summary"]["status"] == "READY"
        # 报告落 Bundle
        root = orc.bundle_for(s.story_id).root
        assert (root / f"11_review/prerender_gate/{s.story_id}.json").exists()
        assert (root / f"06_shots/contract_v5/{s.story_id}.json").exists()

    def test_render_refused_when_gate_blocked(self, tmp_path):
        """互检失败 → PreRender Gate BLOCKED → 渲染被拒（PDF 铁律）。"""
        from orchestrator import PipelineOrchestrator
        from orchestrator.story import InvalidStageTransition
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        self._to_performance(orc, s.story_id)
        # 人为制造身份漂移：改一个镜头的 master_pack（破坏签名后 require_render_ready
        # 也会拦，但这里先验证 PreRender Gate 层）
        cs = orc.contracts_for(s.story_id)
        if len(cs) >= 2:
            cs[1]["assets"]["characters"][0]["master_pack_id"] = "cmp_tampered"
        res = orc.run_prerender_gate(s.story_id)
        assert res["summary"]["status"] == "BLOCKED"       # 门报告仍是 BLOCKED
        # 但故事不停厂：转自修复态，自动打回对应模块；渲染仍被拒
        assert orc.get_story(s.story_id)["status"] == "AUTO_REPAIRING"
        assert orc.get_story(s.story_id)["repair_directive"]["code"]
        with pytest.raises(InvalidStageTransition):
            orc.run_render(s.story_id)

    def test_render_auto_runs_gate_if_not_run(self, tmp_path):
        """直接 run_render 会自动先跑 PreRender Gate。"""
        from orchestrator import PipelineOrchestrator
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        self._to_performance(orc, s.story_id)
        orc.run_render(s.story_id)         # 没先手动跑 gate
        assert orc.prerender_for(s.story_id) is not None
        assert orc.get_story(s.story_id)["prerender_summary"]["status"] == "READY"
