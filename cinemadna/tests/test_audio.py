"""AudioDNA (Phase E) 测试 —— 声音性别绑定 / 口型策略实测 / 字幕 + 真实 R8 修复

验证声音专业审核会抓出问题，且 AudioDNA 能**真实自动修复**（重绑性别、重推口型、
重签合约），驱动器据此自愈，不停厂、不等人。
"""

from __future__ import annotations

from audio.service import AudioDNAService, audio_review
from director.shot_contract import (
    MOUTH_SILENT_CLOSED,
    SHOT_MEDIUM,
    STATUS_PERFORMANCE_READY,
    new_shot_contract,
    sign_contract,
)
from asset_brain.common.quad import Quad

CHARS = {"char_a": {"character_id": "char_a", "name": "林", "gender": "female"},
         "char_nogender": {"character_id": "char_nogender", "name": "无性别"}}


def _contract(*, dialogue=None, mouth=None, speakers=("char_a",),
              gender_map=None, shot_id="SH1"):
    """造一张已签合约，voice_contract 由 gender_map 决定绑定。"""
    c = new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001", order=1,
        shot_type=SHOT_MEDIUM,
        quad=Quad(story_id="drama_0001", bundle_id="b1", task_id="t1", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "x"},
        duration_sec=5.0, emotion={"beat": "CONFLICT", "intensity": 0.6, "mood": "x"},
        scene_asset={"asset_id": "sc", "asset_hash": "sha256:" + "a"*64,
                     "gate_passed": True, "tags": []},
        character_assets=[{"character_id": s, "master_pack_id": "cmp",
                           "asset_hash": "sha256:" + "b"*64, "gate_passed": True}
                          for s in speakers],
        prop_assets=[], dialogue=dialogue or [], mouth_policy=mouth)
    # 最小表演节拍（签合约要求 PerformanceDNA 已处理）
    c["performance"] = {"acting_beats": [
        {"type": "LINE", "character_id": s, "cue": "", "micro_expression": {}}
        for s in speakers]}
    gm = gender_map or {}
    c["voice_contract"] = {
        "has_dialogue": bool(dialogue),
        "bindings": [{"speaker": ln["character_id"], "voice_gender": gm.get(
            ln["character_id"], "unspecified"), "line": ln["line"]}
            for ln in (dialogue or [])],
    }
    from director.shot_contract import STATUS_DRAFT
    if c["status"] != STATUS_DRAFT:
        c["status"] = STATUS_DRAFT
        c["contract_hash"] = None
    sign_contract(c)
    return c


class TestAudioReview:
    def test_unspecified_gender_flagged(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth="SPEAKING_LIPSYNC")
        r = audio_review([c], story_id="d1", characters_by_id=CHARS)
        assert r.verdict == "REPAIR"
        assert any(i["code"] == "VOICE_GENDER_UNSPECIFIED" for i in r.issues)

    def test_bound_gender_passes(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth="SPEAKING_LIPSYNC", gender_map={"char_a": "female"})
        r = audio_review([c], story_id="d1", characters_by_id=CHARS)
        assert r.passed

    def test_mouth_policy_violation_flagged(self):
        # 有台词却标嘴闭合 → 违规
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SILENT_CLOSED, gender_map={"char_a": "female"})
        r = audio_review([c], story_id="d1", characters_by_id=CHARS)
        assert any(i["code"] == "MOUTH_POLICY_VIOLATION" for i in r.issues)

    def test_gender_mismatch_flagged(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth="SPEAKING_LIPSYNC", gender_map={"char_a": "male"})
        r = audio_review([c], story_id="d1", characters_by_id=CHARS)
        assert any(i["code"] == "VOICE_GENDER_MISMATCH" for i in r.issues)

    def test_subtitle_coverage_checked(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth="SPEAKING_LIPSYNC", gender_map={"char_a": "female"})
        r = audio_review([c], story_id="d1", characters_by_id=CHARS,
                         rough_cut={"subtitles": []})   # 台词有、字幕空
        assert any(i["code"] == "SUBTITLE_COVERAGE" for i in r.issues)


class TestAudioRepair:
    def test_repair_binds_gender_from_character(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth="SPEAKING_LIPSYNC")
        old_hash = c["contract_hash"]
        fixed = AudioDNAService().repair_contracts([c], characters_by_id=CHARS)
        assert c["shot_id"] in fixed
        assert c["voice_contract"]["bindings"][0]["voice_gender"] == "female"
        assert c["status"] == STATUS_PERFORMANCE_READY
        assert c["contract_hash"] != old_hash        # 重签，hash 更新

    def test_repair_assigns_stable_default_when_no_gender(self):
        c = _contract(dialogue=[{"character_id": "char_nogender", "line": "喂"}],
                      mouth="SPEAKING_LIPSYNC", speakers=("char_nogender",))
        AudioDNAService().repair_contracts([c], characters_by_id=CHARS)
        b = c["voice_contract"]["bindings"][0]
        assert b["voice_gender"] in ("female", "male")
        assert b["auto_assigned"] is True
        # 稳定：再修一部同 id 的，性别一致
        c2 = _contract(dialogue=[{"character_id": "char_nogender", "line": "喂"}],
                       mouth="SPEAKING_LIPSYNC", speakers=("char_nogender",),
                       shot_id="SH2")
        AudioDNAService().repair_contracts([c2], characters_by_id=CHARS)
        assert c2["voice_contract"]["bindings"][0]["voice_gender"] == b["voice_gender"]

    def test_repair_fixes_mouth_policy(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SILENT_CLOSED, gender_map={"char_a": "female"})
        AudioDNAService().repair_contracts([c], characters_by_id=CHARS)
        # 修好后审核通过
        assert audio_review([c], story_id="d1", characters_by_id=CHARS).passed

    def test_review_then_repair_converges(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SILENT_CLOSED)   # 双问题：性别未定 + 口型违规
        assert not audio_review([c], story_id="d1", characters_by_id=CHARS).passed
        AudioDNAService().repair_contracts([c], characters_by_id=CHARS)
        assert audio_review([c], story_id="d1", characters_by_id=CHARS).passed
