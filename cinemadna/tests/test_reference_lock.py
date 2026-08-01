"""② I2V Reference Lock 测试 —— 主角禁 T2V / 参考强制 / 身份可验证（0 成本）。"""

from __future__ import annotations

import pytest

from render.backend import RenderRequest
from render.reference_lock import (
    ReferenceLockError, has_core_character, is_prop_insert,
    enforce_reference_lock, identity_fingerprint, verify_reference_consistency,
)


def _req(shot_id="S1", *, chars=None, props=None, camera=None, seed=1):
    return RenderRequest(
        shot_id=shot_id, task_id="t", story_id="s", bundle_id="b",
        contract_hash="h", model="kling-v1", tier="std", duration_sec=3.0,
        seed=seed, prompt="x", camera=camera or {},
        reference_assets={"character_images": {c: "x.png" for c in (chars or [])},
                          "props": {p: "p.png" for p in (props or [])}})


class TestReferenceLock:
    def test_core_character_shot_without_reference_raises(self):
        req = _req(chars=["linwan"])
        with pytest.raises(ReferenceLockError):
            enforce_reference_lock(req, "")            # 主角镜 + 无参考 = 禁纯 T2V

    def test_core_character_shot_with_reference_passes(self):
        req = _req(chars=["linwan"])
        assert enforce_reference_lock(req, "BASE64DATA") == "BASE64DATA"

    def test_prop_insert_without_reference_is_exempt(self):
        # 纯道具插入镜（无角色）→ 不强制参考
        req = _req(chars=[], props=["iou"], camera={"shot_type": "INSERT"})
        assert enforce_reference_lock(req, "") == ""
        assert not has_core_character(req)

    def test_execution_facts_mark_prop_insert(self):
        req = _req(chars=["linwan"])
        req.reference_assets["execution"] = {"is_prop_insert": True}
        assert is_prop_insert(req)
        # 被合同标为插入镜 → 即便有角色也豁免
        assert enforce_reference_lock(req, "") == ""


class TestIdentityFingerprint:
    def test_fingerprint_records_reference_and_seed(self):
        req = _req(chars=["linwan"], seed=99)
        fp = identity_fingerprint(req, reference_kind="scene_first_frame",
                                  ref_key="shot__S1")
        assert fp["seed"] == 99 and fp["characters"] == ["linwan"]
        assert fp["reference_kind"] == "scene_first_frame"

    def test_verify_consistent_same_seed(self):
        fps = [{"characters": ["linwan"], "seed": 5},
               {"characters": ["linwan"], "seed": 5},
               {"characters": ["linwan", "zhao"], "seed": 5}]
        r = verify_reference_consistency(fps)
        assert r["consistent"] and not r["anomalies"]

    def test_verify_flags_seed_drift(self):
        fps = [{"characters": ["linwan"], "seed": 5},
               {"characters": ["linwan"], "seed": 7}]   # 同角色两个锚 = 漂移
        r = verify_reference_consistency(fps)
        assert not r["consistent"]
        assert r["anomalies"][0]["character"] == "linwan"
