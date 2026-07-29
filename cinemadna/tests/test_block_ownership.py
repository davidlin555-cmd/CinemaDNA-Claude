"""Block Ownership + LightingAgent 测试 —— 唯一owner可写/越权拒/空块BLOCKED（0成本）。"""

from __future__ import annotations

import pytest

from director.shot_task_sheet import (
    ShotMasterTaskSheet, BLOCK_OWNERS, OwnershipViolation, write_block,
    validate_block_ownership, ShotTaskSheetValidator,
)
from director.agents.lighting import LightingAgent


def _empty_sheet() -> ShotMasterTaskSheet:
    return ShotMasterTaskSheet(
        story_id="s1", scene_id="SC1", shot_id="SC1_SH1",
        narrative={}, character={}, action={}, expression={}, dialogue={},
        props={}, scene_art={}, lighting={}, camera={}, sound={}, acceptance={})


class TestOwnership:
    def test_owner_can_write_its_block(self):
        sh = _empty_sheet()
        write_block(sh, "camera", "camera", {"shot_size": "MS"})
        assert sh.camera["shot_size"] == "MS"
        assert sh.provenance["camera"] == "camera"

    def test_non_owner_write_is_rejected(self):
        sh = _empty_sheet()
        with pytest.raises(OwnershipViolation):
            write_block(sh, "camera", "action", {"shot_size": "MS"})  # action 越权写 camera

    def test_unknown_block_rejected(self):
        with pytest.raises(OwnershipViolation):
            write_block(_empty_sheet(), "hologram", "x", {})

    def test_every_block_has_unique_owner(self):
        # A–K 全部 11 块都在 owner 表里，且 owner 唯一映射
        assert len(BLOCK_OWNERS) == 11
        assert set(BLOCK_OWNERS) == {
            "narrative", "character", "action", "expression", "dialogue",
            "props", "scene_art", "lighting", "camera", "sound", "acceptance"}


class TestOwnershipValidation:
    def test_empty_blocks_are_blocked(self):
        issues = validate_block_ownership(_empty_sheet())
        assert len(issues) == 11                       # 全空 → 11 块全 BLOCKED
        assert all(i["verdict"] == "BLOCKED" for i in issues)

    def test_wrong_owner_provenance_blocked(self):
        sh = _empty_sheet()
        sh.lighting = {"mood": "pressure", "key_rim": True}
        sh.provenance["lighting"] = "action"           # 伪造：action 冒充写了灯光
        issues = validate_block_ownership(sh)
        assert any(i["block"] == "lighting" and "非法定 owner" in i["message"]
                   for i in issues)

    def test_correctly_owned_block_not_flagged(self):
        sh = _empty_sheet()
        LightingAgent().produce(sh, story_function="pressure", intensity=0.8)
        light_issues = [i for i in validate_block_ownership(sh)
                        if i["block"] == "lighting"]
        assert light_issues == []                      # 正确 owner 写的灯光块不报


class TestLightingAgent:
    def test_produces_valid_lighting_block(self):
        sh = _empty_sheet()
        LightingAgent().produce(sh, story_function="pressure", intensity=0.85)
        assert sh.lighting["mood"] == "pressure"
        assert sh.lighting["key_rim"] is True          # 对峙+强情绪 → 轮廓分离
        assert sh.provenance["lighting"] == "lighting"

    def test_soft_shot_no_rim(self):
        sh = _empty_sheet()
        LightingAgent().produce(sh, story_function="reaction", intensity=0.3,
                                night=False)
        assert sh.lighting["mood"] == "soft"
        assert sh.lighting["key_rim"] is False

    def test_inherits_scene_when_matching(self):
        sh = _empty_sheet()
        LightingAgent().produce(sh, story_function="reaction", intensity=0.3,
                                scene_lighting={"mood": "soft"}, night=False)
        assert sh.lighting["source"] == "inherit_scene"

    def test_lighting_block_passes_sheet_validator_field_check(self):
        # H 块字段（mood + key_rim）被主 validator 认可
        sh = _empty_sheet()
        LightingAgent().produce(sh, story_function="pressure", intensity=0.8)
        issues = ShotTaskSheetValidator().validate(sh)
        assert not any(i["block"] == "lighting" for i in issues)
