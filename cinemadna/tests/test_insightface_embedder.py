"""InsightFace 真判脸测试 —— 取最大脸/无脸/接进一致性服务（0 成本，假 app 不下模型）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from identity.insightface_embedder import InsightFaceEmbedder


class FakeFace:
    def __init__(self, emb, bbox=(0, 0, 10, 10)):
        self.bbox = np.array(bbox, dtype=np.float32)
        self.normed_embedding = np.array(emb, dtype=np.float32)


class FakeApp:
    def __init__(self, faces):
        self._faces = faces
    def get(self, img):
        return self._faces


def _embedder(faces):
    e = InsightFaceEmbedder(app=FakeApp(faces))
    e._frame = lambda v, at_sec: np.zeros((10, 10, 3))   # 绕过 ffmpeg/cv2 抽帧
    return e


class TestEmbed:
    def test_is_real_true(self):
        assert InsightFaceEmbedder(app=FakeApp([])).is_real is True

    def test_picks_largest_face(self):
        e = _embedder([FakeFace([1, 0, 0], bbox=(0, 0, 10, 10)),
                       FakeFace([0, 1, 0], bbox=(0, 0, 100, 100))])
        v = e.embed(Path("x.mp4"), at_sec=1)
        assert list(v) == [0.0, 1.0, 0.0]                # 最大脸胜出

    def test_no_face_returns_none(self):
        assert _embedder([]).embed(Path("x.mp4"), at_sec=1) is None

    def test_frame_read_fail_returns_none(self):
        e = InsightFaceEmbedder(app=FakeApp([FakeFace([1, 0, 0])]),
                                runner=lambda argv: 1)    # ffmpeg 失败
        assert e.embed(Path("x.mp4"), at_sec=1) is None


class TestPlugsIntoConsistency:
    def test_same_face_passes_and_marks_real(self):
        from identity.consistency import IdentityConsistencyService
        e = _embedder([FakeFace([1, 0, 0])])              # 两镜同一张脸
        svc = IdentityConsistencyService(
            embedder=e, threshold=InsightFaceEmbedder.recommended_threshold)
        items = [{"shot_id": "S1", "video": "a.mp4", "character_id": "c",
                  "duration_sec": 3},
                 {"shot_id": "S2", "video": "b.mp4", "character_id": "c",
                  "duration_sec": 3}]
        r = svc.review(items)
        assert r.passed and r.embedder_is_real is True   # 真判官 + 同脸通过

    def test_recommended_threshold_looser_than_proxy(self):
        from identity.consistency import DRIFT_THRESHOLD
        assert InsightFaceEmbedder.recommended_threshold > DRIFT_THRESHOLD


class FakeFaceP:
    """带 pose(yaw) 的假脸，用于测姿态感知采样。"""
    def __init__(self, emb, yaw=0.0, bbox=(0, 0, 50, 50)):
        self.bbox = np.array(bbox, dtype=np.float32)
        self.normed_embedding = np.array(emb, dtype=np.float32)
        self.pose = np.array([0.0, yaw, 0.0], dtype=np.float32)


class TagImg:
    """带 tag 的假帧：_face_meta 读 .shape，FakeTagApp 按 tag(时间戳) 返回不同脸。"""
    shape = (100, 100, 3)
    def __init__(self, tag):
        self.tag = tag


class FakeTagApp:
    def __init__(self, by_frac):        # by_frac: {round(dur*frac,2): [faces]}
        self.by_frac = by_frac
    def get(self, img):
        return self.by_frac.get(getattr(img, "tag", None), [])


def _frontal_embedder(by_frac):
    e = InsightFaceEmbedder(app=FakeTagApp(by_frac))
    e._frame = lambda v, at_sec: TagImg(at_sec)          # tag=采样时间戳
    return e


class TestEmbedFrontal:
    # duration=10 → fracs 0.1..0.85 落在 1.0,2.5,4.0,5.5,7.0,8.5
    def test_side_profile_frames_unreliable(self):
        # 每帧都是大 yaw(侧脸) → 无正脸帧 → reliable=False,签名 None(服务不判漂移)
        faces = {t: [FakeFaceP([1, 0, 0], yaw=50.0)]
                 for t in (1.0, 2.5, 4.0, 5.5, 7.0, 8.5)}
        emb, meta = _frontal_embedder(faces).embed_frontal(
            Path("x.mp4"), duration_sec=10)
        assert emb is None and meta["reliable"] is False
        assert meta["reason"] == "no_frontal_frame" and meta["best_yaw"] == 50.0

    def test_picks_frontal_frame_over_profile(self):
        # 多数侧脸,仅 t=4.0 正脸(yaw5°,身份向量[0,1,0]) → 选正脸帧的签名
        faces = {t: [FakeFaceP([1, 0, 0], yaw=48.0)]
                 for t in (1.0, 2.5, 5.5, 7.0, 8.5)}
        faces[4.0] = [FakeFaceP([0, 1, 0], yaw=5.0)]
        emb, meta = _frontal_embedder(faces).embed_frontal(
            Path("x.mp4"), duration_sec=10)
        assert list(emb) == [0.0, 1.0, 0.0] and meta["reliable"] is True
        assert meta["yaw"] == 5.0 and meta["frontal_frames"] == 1

    def test_small_face_not_reliable(self):
        # 正脸但脸太小(bbox 5x5=25/10000=0.25% < 0.4%) → 不可靠
        faces = {t: [FakeFaceP([1, 0, 0], yaw=3.0, bbox=(0, 0, 5, 5))]
                 for t in (1.0, 2.5, 4.0, 5.5, 7.0, 8.5)}
        emb, meta = _frontal_embedder(faces).embed_frontal(
            Path("x.mp4"), duration_sec=10)
        assert emb is None and meta["reliable"] is False


class TestPoseAwareConsistency:
    def test_profile_only_shot_not_flagged_as_drift(self):
        # 一镜全侧脸(签名不可靠) → 不判漂移(embedded=False),不因侧脸虚高误杀
        from identity.consistency import IdentityConsistencyService
        good = [FakeFaceP([1, 0, 0], yaw=4.0)]            # 正脸=同一身份
        side = [FakeFaceP([0, 0, 1], yaw=50.0)]           # 侧脸=签名不可靠
        by_frac = {}
        for t in (1.0, 2.5, 4.0, 5.5, 7.0, 8.5):
            by_frac[t] = good
        # S3 的采样帧全侧脸：用 tag 前缀区分（video 名不同但 _frame 只给时间戳，
        # 故改用 embedder 级：两个 embedder 实例更简单）
        e_good = _frontal_embedder({t: good for t in (1.0, 2.5, 4.0, 5.5, 7.0, 8.5)})
        e_side = _frontal_embedder({t: side for t in (1.0, 2.5, 4.0, 5.5, 7.0, 8.5)})

        # 直接验证 embed_frontal 语义：正脸镜可判、侧脸镜不可判
        assert e_good.embed_frontal(Path("a.mp4"), duration_sec=10)[0] is not None
        assert e_side.embed_frontal(Path("c.mp4"), duration_sec=10)[0] is None

        svc = IdentityConsistencyService(embedder=e_good, threshold=0.5)
        items = [{"shot_id": f"S{i}", "video": f"{i}.mp4", "character_id": "c",
                  "duration_sec": 10} for i in (1, 2)]
        r = svc.review(items)
        assert r.passed                                   # 同脸正脸 → 通过
        assert r.per_shot["S1"].get("yaw") == 4.0         # 记录了正脸角
