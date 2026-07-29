"""InsightFace 真人脸嵌入 —— 把 IdentityConsistency 的代理判官升级成真判脸（⑤·0 API 费）。

代理 ProxyFaceEmbedder（numpy 肤色/结构签名）会**过度标记**：换个景别/光线就误判"漂移"。
InsightFace 用 ArcFace 512 维人脸嵌入，跨镜同脸判读**可信**——同人余弦距离小、不同人大，
才是真正的"跨镜是不是同一张脸"。

实现同一个 FaceEmbedder 接口（is_real=True），即插即用替换代理。模型 buffalo_l 首次使用
时自动下载(~300MB，一次性)。FaceAnalysis app 可注入 → 0 成本单测不下模型。
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np


class InsightFaceEmbedder:
    """ArcFace 真人脸嵌入（is_real=True）。取最大人脸的归一化 512 维向量。"""

    is_real = True
    #: ArcFace 余弦距离阈值：同人通常 < 0.5，不同人 > 0.6。比代理(0.35)更准更宽。
    recommended_threshold = 0.5

    def __init__(self, *, ffmpeg: str = "ffmpeg", model_name: str = "buffalo_l",
                 app: Any = None, det_size: int = 640,
                 runner: Callable[[list[str]], int] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self.model_name = model_name
        self.det_size = det_size
        self._app = app                       # 可注入 FaceAnalysis（测试用）
        self._runner = runner or self._default_runner

    def _default_runner(self, argv: list[str]) -> int:
        return subprocess.run(argv, capture_output=True, timeout=120).returncode

    def _get_app(self) -> Any:
        if self._app is None:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name=self.model_name,
                               providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=-1, det_size=(self.det_size, self.det_size))
            self._app = app
        return self._app

    def _frame(self, video: Path, at_sec: float) -> "np.ndarray | None":
        """抽一帧成 BGR ndarray（insightface 吃 cv2 的 BGR）。"""
        import cv2
        with tempfile.TemporaryDirectory() as td:
            fp = Path(td) / "f.png"
            rc = self._runner([self.ffmpeg, "-v", "error", "-ss", f"{at_sec}",
                               "-i", str(video), "-frames:v", "1", str(fp)])
            if rc != 0 or not fp.is_file():
                return None
            return cv2.imread(str(fp))

    def _embed_bgr(self, img: "np.ndarray | None") -> "np.ndarray | None":
        if img is None:
            return None
        faces = self._get_app().get(img)
        if not faces:
            return None                       # 没检到脸 → 无法判（服务视为不比对）
        # 取最大人脸（画面主角）
        face = max(faces, key=lambda f: float(
            (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])))
        emb = getattr(face, "normed_embedding", None)
        if emb is None:
            emb = face.embedding / (np.linalg.norm(face.embedding) or 1.0)
        return np.asarray(emb, dtype=np.float32)

    def embed(self, video: Path, *, at_sec: float) -> "np.ndarray | None":
        return self._embed_bgr(self._frame(Path(video), at_sec))

    def _face_meta(self, img: "np.ndarray | None"):
        """返回最大人脸的 (签名, 脸占画面%, |yaw|)；无脸→None。

        yaw(偏航/侧脸角)是关键:InsightFace 拿侧脸比正脸锚会**虚高**距离,同一个人也误判
        漂移(见 [[kling-motion-drift-tail]]: SH001 侧脸帧yaw52°→0.72误判,正脸帧yaw7°→0.42真值)。
        """
        if img is None:
            return None
        faces = self._get_app().get(img)
        if not faces:
            return None
        h, w = img.shape[:2]
        face = max(faces, key=lambda f: float(
            (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])))
        area = float((face.bbox[2] - face.bbox[0]) * (face.bbox[3] - face.bbox[1]))
        area_pct = 100.0 * area / float(w * h or 1)
        pose = getattr(face, "pose", None)
        yaw = abs(float(pose[1])) if pose is not None and len(pose) > 1 else 0.0
        emb = getattr(face, "normed_embedding", None)
        if emb is None:
            emb = face.embedding / (np.linalg.norm(face.embedding) or 1.0)
        return np.asarray(emb, dtype=np.float32), area_pct, yaw

    def embed_frontal(self, video: Path, *, duration_sec: float, samples: int = 6,
                      max_yaw: float = 30.0, min_area_pct: float = 0.4):
        """采多帧,只用**正脸帧**(|yaw|≤max_yaw 且脸够大)判身份，返回 (最正脸帧签名, meta)。

        单中帧判身份太脆:恰好取到侧脸/小脸就误判漂移。改采多帧,只在签名可靠(正脸+够大)
        的帧上判 → 治侧脸虚高假阳性,又不放过真换脸(真换脸正脸也远)。无任何正脸帧 →
        (None, reliable=False):该镜身份**不可由签名核验**(观众也看不出)→ 服务不判漂移。
        """
        dur = max(0.5, float(duration_sec))
        fracs = (0.1, 0.25, 0.4, 0.55, 0.7, 0.85)[:max(1, samples)]
        reliable: list[tuple[float, float, "np.ndarray"]] = []
        best_yaw: float | None = None
        for f in fracs:
            m = self._face_meta(self._frame(Path(video), round(dur * f, 2)))
            if m is None:
                continue
            emb, area_pct, yaw = m
            if best_yaw is None or yaw < best_yaw:
                best_yaw = yaw
            if yaw <= max_yaw and area_pct >= min_area_pct:
                reliable.append((yaw, area_pct, emb))
        if not reliable:
            return None, {"reliable": False, "reason": "no_frontal_frame",
                          "best_yaw": round(best_yaw, 1) if best_yaw is not None
                          else None}
        reliable.sort(key=lambda r: r[0])                 # 最正脸优先
        yaw, area_pct, emb = reliable[0]
        return emb, {"reliable": True, "yaw": round(yaw, 1),
                     "area_pct": round(area_pct, 2), "frontal_frames": len(reliable)}

    def embed_image(self, image: Path) -> "np.ndarray | None":
        """对 PNG/JPG 首帧图直接抽脸签名（首帧素材闸用，不经 ffmpeg 抽帧）。"""
        import cv2
        p = Path(image)
        if not p.is_file():
            return None
        return self._embed_bgr(cv2.imread(str(p)))


__all__ = ["InsightFaceEmbedder"]
