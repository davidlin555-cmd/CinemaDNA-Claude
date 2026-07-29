"""PerformanceDrivingLibrary —— 表演驱动素材库（工厂级资产，跨剧累积/复用）。

每条 = 一段**驱动表演**（motion 源视频）+ 元数据，按(情绪,强度)键。库越用越全，
以后越省（贵的云端首采只花一次，之后本地 $0 复用）。文件级持久化：JSON 索引 +
驱动视频文件。默认落在共享资产目录（跨故事复用），可注入根目录做测试。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

#: 情绪归一（同义收敛，提高命中）
_EMO_ALIAS = {
    "隐忍": "隐忍", "压抑": "隐忍", "克制": "隐忍",
    "爆发": "爆发", "决绝": "爆发", "愤怒": "爆发",
    "哀求": "哀求", "无助": "哀求", "绝望": "哀求",
    "施压": "施压", "威胁": "施压", "冷硬": "施压",
    "冷静": "冷静", "平静": "冷静",
}
_INTENSITY = ("low", "mid", "high")


def norm_emotion(e: str) -> str:
    return _EMO_ALIAS.get((e or "").strip(), (e or "").strip() or "冷静")


def norm_intensity(i: str) -> str:
    i = (i or "").strip().lower()
    return i if i in _INTENSITY else "mid"


def take_key(emotion: str, intensity: str) -> str:
    return f"{norm_emotion(emotion)}::{norm_intensity(intensity)}"


@dataclass
class DrivingTake:
    take_id: str
    emotion: str
    intensity: str
    beat_type: str = ""
    driving_video: str = ""        # 驱动表演视频路径（motion 源）
    source: str = ""               # runway_act_one | recorded | ...
    uses: int = 0                  # 复用次数（越高越"经过考验"）
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return take_key(self.emotion, self.intensity)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PerformanceDrivingLibrary:
    """驱动表演素材库：检索/回流/持久化。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index = self.root / "index.json"
        self._takes: dict[str, list[DrivingTake]] = {}
        self._load()

    def _load(self) -> None:
        if self._index.is_file():
            data = json.loads(self._index.read_text("utf-8"))
            for d in data.get("takes", []):
                t = DrivingTake(**d)
                self._takes.setdefault(t.key, []).append(t)

    def _save(self) -> None:
        flat = [t.to_dict() for lst in self._takes.values() for t in lst]
        self._index.write_text(
            json.dumps({"takes": flat}, ensure_ascii=False, indent=2), "utf-8")

    # -- 检索 -----------------------------------------------------------

    def find(self, emotion: str, intensity: str, *, beat_type: str = "") -> DrivingTake | None:
        """按(情绪,强度)取最优驱动take（优先同beat_type，再按复用次数）。库缺→None。"""
        cands = list(self._takes.get(take_key(emotion, intensity), []))
        # 只保留驱动视频文件仍在的（防悬空引用）
        cands = [t for t in cands if t.driving_video
                 and (self.root / t.driving_video).is_file()]
        if not cands:
            return None
        if beat_type:
            exact = [t for t in cands if t.beat_type == beat_type]
            if exact:
                cands = exact
        return max(cands, key=lambda t: t.uses)

    def has(self, emotion: str, intensity: str) -> bool:
        return self.find(emotion, intensity) is not None

    # -- 回流（首采后存库） --------------------------------------------

    def add(self, take: DrivingTake) -> DrivingTake:
        self._takes.setdefault(take.key, []).append(take)
        self._save()
        return take

    def mark_used(self, take: DrivingTake) -> None:
        take.uses += 1
        self._save()

    def import_driving_video(self, src: str | Path, *, take_id: str, emotion: str,
                             intensity: str, beat_type: str = "",
                             source: str = "runway_act_one",
                             meta: dict[str, Any] | None = None) -> DrivingTake:
        """把一段驱动视频复制进库并登记（首采回流）。"""
        rel = f"takes/{take_key(emotion, intensity)}/{take_id}.mp4".replace("::", "_")
        dst = self.root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(Path(src).read_bytes())
        return self.add(DrivingTake(
            take_id=take_id, emotion=norm_emotion(emotion),
            intensity=norm_intensity(intensity), beat_type=beat_type,
            driving_video=rel, source=source, meta=meta or {}))

    def stats(self) -> dict[str, Any]:
        return {"keys": len(self._takes),
                "takes": sum(len(v) for v in self._takes.values()),
                "root": str(self.root)}


__all__ = ["PerformanceDrivingLibrary", "DrivingTake", "take_key",
           "norm_emotion", "norm_intensity"]
