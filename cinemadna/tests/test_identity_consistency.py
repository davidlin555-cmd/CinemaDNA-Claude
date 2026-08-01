"""跨镜身份一致性 QA 测试 —— 漂移检测逻辑（注入假 embedder，不跑 ffmpeg）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from identity.consistency import IdentityConsistencyService


class FakeEmbedder:
    """按 shot_id 返回预置向量，验证漂移算法（不依赖真实视频/ffmpeg）。"""
    is_real = True

    def __init__(self, vecs: dict[str, list[float]]) -> None:
        self.vecs = vecs

    def embed(self, video, *, at_sec):
        v = self.vecs.get(Path(video).stem)
        return np.array(v, dtype=np.float32) if v is not None else None


def _items(ids_chars):
    return [{"shot_id": s, "video": f"/{s}.mp4", "character_id": c, "duration_sec": 3}
            for s, c in ids_chars]


class TestDriftDetection:
    def test_consistent_character_passes(self):
        emb = FakeEmbedder({"S1": [1, 0, 0], "S2": [0.98, 0.02, 0], "S3": [0.99, 0, 0.01]})
        svc = IdentityConsistencyService(embedder=emb)
        r = svc.review(_items([("S1", "a"), ("S2", "a"), ("S3", "a")]))
        assert r.passed and not r.drifted_shots and r.embedder_is_real

    def test_drifted_shot_flagged(self):
        # S3 与另外两镜是"不同的人"→ 偏离质心超阈
        emb = FakeEmbedder({"S1": [1, 0, 0], "S2": [1, 0, 0], "S3": [0, 1, 0]})
        svc = IdentityConsistencyService(embedder=emb)
        r = svc.review(_items([("S1", "a"), ("S2", "a"), ("S3", "a")]))
        assert not r.passed and "S3" in r.drifted_shots

    def test_single_shot_character_cannot_drift(self):
        emb = FakeEmbedder({"S1": [1, 0, 0]})
        svc = IdentityConsistencyService(embedder=emb)
        r = svc.review(_items([("S1", "solo")]))
        assert r.passed          # 无参照，判不了漂移

    def test_per_character_grouping(self):
        # a 两镜一致；b 两镜一致；互不干扰
        emb = FakeEmbedder({"A1": [1, 0], "A2": [1, 0], "B1": [0, 1], "B2": [0, 1]})
        svc = IdentityConsistencyService(embedder=emb)
        r = svc.review(_items([("A1", "a"), ("A2", "a"), ("B1", "b"), ("B2", "b")]))
        assert r.passed

    def test_missing_embed_marked_not_embedded(self):
        emb = FakeEmbedder({"S1": [1, 0]})     # S2 无向量
        svc = IdentityConsistencyService(embedder=emb)
        r = svc.review(_items([("S1", "a"), ("S2", "a")]))
        assert r.per_shot["S2"]["embedded"] is False


class TestProxyEmbedderHonesty:
    def test_proxy_is_not_real(self):
        from identity.consistency import ProxyFaceEmbedder
        assert ProxyFaceEmbedder().is_real is False   # 诚实：代理非真人脸ID
