"""跨镜身份一致性 QA —— 规格 3.5 最狠的一条：脸/服装/年龄漂移超阈 → 整镜重做。

原本只有 asset_hash（结构）+ mock 分，判不出**视觉漂移**。场景内首帧各自独立生成，
同一角色跨镜很容易变成"不同的人"。本模块做真正的跨镜比对：

  每镜抽帧 → 人脸区域签名(embedding) → 同角色跨镜比对 → 偏离质心超阈 = 漂移 → 打回重渲

**FaceEmbedder 可注入**：
  - ProxyFaceEmbedder（默认，numpy+ffmpeg 人脸区域感知签名，is_real=False）：只依赖 numpy，
    能抓**粗漂移**（换人/换装/换性别），但不是真正的人脸ID。
  - 真人脸模型（insightface/face_recognition）实现同接口即插即用，is_real=True。

诚实：默认代理不是像素级人脸识别；接真实模型才是"到最好"。接口与漂移算法都已就绪。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import numpy as np

#: 余弦距离漂移阈值（离本角色质心多远算"不是同一个人"）。代理签名较松，取 0.35。
DRIFT_THRESHOLD = 0.35


@runtime_checkable
class FaceEmbedder(Protocol):
    is_real: bool
    def embed(self, video: Path, *, at_sec: float) -> "np.ndarray | None": ...


class ProxyFaceEmbedder:
    """numpy+ffmpeg 人脸区域感知签名（代理，非真人脸ID）。

    抽一帧 → 取中上部人脸ROI → 32x32 灰度结构 + 肤色/发色/服装色矩 → 归一向量。
    能分辨"明显不是同一个人/换装"，抓不了细微五官漂移（那需要真人脸模型）。
    """

    is_real = False

    def __init__(self, *, ffmpeg: str = "ffmpeg",
                 runner: Callable[[list[str]], bytes] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self._runner = runner or self._default_runner

    def _default_runner(self, argv: list[str]) -> bytes:
        return subprocess.run(argv, capture_output=True, timeout=60).stdout

    def _raw(self, video: Path, at_sec: float, *, w: int, h: int, gray: bool) -> "np.ndarray | None":
        fmt = "gray" if gray else "rgb24"
        # 取中上部人脸ROI（竖屏肖像脸大致在上半居中）再缩放
        vf = (f"crop=iw*0.6:ih*0.45:iw*0.2:ih*0.08,scale={w}:{h},format={fmt}")
        argv = [self.ffmpeg, "-v", "error", "-ss", f"{at_sec}", "-i", str(video),
                "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-"]
        buf = self._runner(argv)
        ch = 1 if gray else 3
        if not buf or len(buf) < w * h * ch:
            return None
        arr = np.frombuffer(buf[:w * h * ch], dtype=np.uint8).astype(np.float32)
        return arr

    def embed(self, video: Path, *, at_sec: float) -> "np.ndarray | None":
        g = self._raw(video, at_sec, w=32, h=32, gray=True)
        c = self._raw(video, at_sec, w=8, h=8, gray=False)
        if g is None or c is None:
            return None
        g = g / 255.0
        g = (g - g.mean())                       # 去光照偏置
        norm = np.linalg.norm(g) or 1.0
        g = g / norm
        # 色矩（肤/发/服装的粗略颜色）：8x8 RGB 的均值 + 标准差
        c = c.reshape(-1, 3) / 255.0
        col = np.concatenate([c.mean(axis=0), c.std(axis=0)])
        return np.concatenate([g, col]).astype(np.float32)


@dataclass
class IdentityConsistencyResult:
    verdict: str                                  # PASS / REPAIR
    drifted_shots: list[str] = field(default_factory=list)
    per_shot: dict[str, dict[str, Any]] = field(default_factory=dict)
    embedder_is_real: bool = False

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def report(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "drifted_shots": self.drifted_shots,
                "embedder_is_real": self.embedder_is_real, "per_shot": self.per_shot}


def _cos_dist(a: "np.ndarray", b: "np.ndarray") -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 1.0
    return float(1.0 - np.dot(a, b) / (na * nb))


class IdentityConsistencyService:
    """跨镜身份一致性硬门：同角色跨镜偏离质心超阈 → 判漂移 → 打回重渲该镜。"""

    def __init__(self, embedder: FaceEmbedder | None = None,
                 threshold: float = DRIFT_THRESHOLD, bundle=None) -> None:
        self.embedder = embedder or ProxyFaceEmbedder()
        self.threshold = threshold
        self.bundle = bundle

    def review(self, items: list[dict[str, Any]], *, story_id: str = ""
               ) -> IdentityConsistencyResult:
        """items = [{shot_id, video: Path, character_id, duration_sec}]。

        对每个主角色收集其所有镜的人脸签名，算质心，标出偏离超阈的镜（=漂移）。
        单角色单镜无法判（无参照）→ 视为通过。
        """
        # 抽签名
        embeds: dict[str, "np.ndarray"] = {}
        by_char: dict[str, list[str]] = {}
        per_shot: dict[str, dict[str, Any]] = {}
        pose_aware = hasattr(self.embedder, "embed_frontal")
        for it in items:
            sid = it["shot_id"]
            cid = it.get("character_id") or "?"
            vid = Path(it["video"])
            dur = float(it.get("duration_sec", 3))
            # 姿态感知:采多帧只用正脸帧判身份 → 治侧脸/小脸签名虚高的假漂移。
            # 无正脸帧 → 身份不可核验(观众也看不出)→ 不判漂移(embedded=False)。
            if pose_aware:
                emb, meta = self.embedder.embed_frontal(vid, duration_sec=dur)
            else:
                emb = self.embedder.embed(vid, at_sec=round(dur * 0.5, 2))
                meta = {}
            if emb is None:
                per_shot[sid] = {"character_id": cid, "embedded": False, **meta}
                continue
            embeds[sid] = emb
            by_char.setdefault(cid, []).append(sid)
            per_shot[sid] = {"character_id": cid, "embedded": True, **meta}

        drifted: list[str] = []
        for cid, sids in by_char.items():
            if len(sids) < 2:                     # 无参照，判不了漂移
                for sid in sids:
                    per_shot[sid]["distance"] = 0.0
                continue
            mat = np.stack([embeds[s] for s in sids])
            centroid = mat.mean(axis=0)
            for sid in sids:
                d = round(_cos_dist(embeds[sid], centroid), 4)
                per_shot[sid]["distance"] = d
                if d > self.threshold:
                    per_shot[sid]["drift"] = True
                    drifted.append(sid)

        result = IdentityConsistencyResult(
            verdict="REPAIR" if drifted else "PASS", drifted_shots=drifted,
            per_shot=per_shot, embedder_is_real=self.embedder.is_real)
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/identity_consistency/{story_id}.json", result.report())
        return result


__all__ = ["IdentityConsistencyService", "IdentityConsistencyResult",
           "ProxyFaceEmbedder", "FaceEmbedder", "DRIFT_THRESHOLD"]
