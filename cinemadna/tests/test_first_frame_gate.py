"""首帧素材闸测试 —— 付费渲染前验主角锁脸(0 成本,假 embedder)。

覆盖今天的身份漂移 bug: 主角单人镜若没锁 PuLID 锚脸(漏成随机脸)→ 闸必须拦下,
不让进 Kling 付费渲染。
"""

from __future__ import annotations

from pathlib import Path

from render.preflight import (
    FirstFrameIdentityPreflight, FIRST_FRAME_IDENTITY_THRESHOLD)


def _cos(a, b):
    # 假向量用一维标量表示"脸": 距离=|a-b|,便于构造
    return abs(float(a) - float(b))


def _preflight(anchors, embeds, *, real=True):
    """anchors: cid->锚脸标量; embeds: png名->标量。"""
    def embed_png(p):
        return embeds.get(Path(p).name)
    def anchor_for(cid):
        return Path(f"{cid}_anchor.png") if cid in anchors else None
    # 锚脸图也走 embed_png:名字=cid_anchor.png
    for cid, v in anchors.items():
        embeds.setdefault(f"{cid}_anchor.png", v)
    return FirstFrameIdentityPreflight(
        embed_png=embed_png, anchor_for=anchor_for, cos_dist=_cos,
        embedder_is_real=real)


class TestFirstFrameGate:
    def test_locked_shot_passes(self):
        # PuLID 锁的首帧≈锚脸 → 过
        pf = _preflight({"linwan": 1.0}, {"ff1.png": 1.02})
        res = pf.check([{"shot_id": "S1", "character_id": "linwan",
                         "single_char": True, "has_anchor": True,
                         "tier": "pulid_anchor", "first_frame": "ff1.png"}])
        assert res.ready and res.checks[0].ok
        assert res.checks[0].distance is not None

    def test_random_flux_face_blocked(self):
        # 漏成 FLUX 随机脸(远离锚脸) → 拦
        pf = _preflight({"linwan": 1.0}, {"ff2.png": 1.9})   # 距 0.9 ≥ 0.35
        res = pf.check([{"shot_id": "S2", "character_id": "linwan",
                         "single_char": True, "has_anchor": True,
                         "tier": "pulid_anchor", "first_frame": "ff2.png"}])
        assert not res.ready and "S2" in res.failed_ids

    def test_main_shot_without_pulid_tier_blocked(self):
        # 关键回归: 主角单人镜但 tier 不是 pulid_anchor(=今天的漏锁 bug)→ 直接拦,
        # 不用比对(会渲成随机脸)。
        pf = _preflight({"linwan": 1.0}, {"ff3.png": 1.0})
        res = pf.check([{"shot_id": "S3", "character_id": "linwan",
                         "single_char": True, "has_anchor": True,
                         "tier": "scene_first_frame", "first_frame": "ff3.png"}])
        assert not res.ready and "S3" in res.failed_ids
        assert "未锁 PuLID" in res.failed[0].reason

    def test_two_person_shot_skipped(self):
        # 双人建立镜不承诺 PuLID → 不判(skipped)
        pf = _preflight({"linwan": 1.0}, {})
        res = pf.check([{"shot_id": "S4", "character_id": "linwan",
                         "single_char": False, "has_anchor": True,
                         "tier": "scene_first_frame", "first_frame": "ff4.png"}])
        assert res.ready and "S4" in res.skipped and not res.checks

    def test_insert_shot_no_anchor_skipped(self):
        # 无锚脸角色/插入镜 → 不判
        pf = _preflight({}, {})
        res = pf.check([{"shot_id": "S5", "character_id": "",
                         "single_char": True, "has_anchor": False,
                         "tier": "scene_first_frame", "first_frame": "ff5.png"}])
        assert res.ready and "S5" in res.skipped

    def test_no_face_detected_not_false_killed(self):
        # 首帧抽不到脸(None)→ 诚实不误杀(交渲染后 InsightFace 兜底)
        pf = _preflight({"linwan": 1.0}, {"ff6.png": None})
        res = pf.check([{"shot_id": "S6", "character_id": "linwan",
                         "single_char": True, "has_anchor": True,
                         "tier": "pulid_anchor", "first_frame": "ff6.png"}])
        assert res.ready and res.checks[0].ok

    def test_report_shape(self):
        pf = _preflight({"linwan": 1.0}, {"a.png": 1.0, "b.png": 2.0})
        res = pf.check([
            {"shot_id": "S1", "character_id": "linwan", "single_char": True,
             "has_anchor": True, "tier": "pulid_anchor", "first_frame": "a.png"},
            {"shot_id": "S2", "character_id": "linwan", "single_char": True,
             "has_anchor": True, "tier": "pulid_anchor", "first_frame": "b.png"},
        ])
        rep = res.report()
        assert rep["checked"] == 2 and rep["passed"] == 1
        assert rep["failed"][0]["shot_id"] == "S2"
        assert rep["threshold"] == FIRST_FRAME_IDENTITY_THRESHOLD
        assert rep["embedder_is_real"] is True

    def test_mixed_batch_blocks_on_any_failure(self):
        # 今天的场景: 4 镜锁对 + 8 镜漏锁 → 闸整体不过,列出漏锁镜
        shots = []
        embeds = {}
        for i in range(4):
            embeds[f"ok{i}.png"] = 1.0
            shots.append({"shot_id": f"OK{i}", "character_id": "linwan",
                          "single_char": True, "has_anchor": True,
                          "tier": "pulid_anchor", "first_frame": f"ok{i}.png"})
        for i in range(8):
            shots.append({"shot_id": f"BAD{i}", "character_id": "linwan",
                          "single_char": True, "has_anchor": True,
                          "tier": "scene_first_frame", "first_frame": f"bad{i}.png"})
        pf = _preflight({"linwan": 1.0}, embeds)
        res = pf.check(shots)
        assert not res.ready
        assert len(res.failed) == 8
        assert all(c.shot_id.startswith("BAD") for c in res.failed)
