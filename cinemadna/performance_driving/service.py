"""PerformanceDrivingService —— 云端首采 / 本地复用 编排。

给一镜的表演需求(情绪/强度/节拍) + 角色脸：
  库里有该表演 → **E2 LivePortrait 本地 $0** 复用（库中驱动 take 套到角色脸）
  库里缺       → **E1 Runway 首采**（角色 + 驱动参考 → 商业级表演）→ 回流存库供下次
贵的云端每种表演只花一次，之后本地无限 $0 复用。诚实：无 E2 装且库有 take 时，如实
标记本地复用不可用（可退 E1 或降级），绝不冒充。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .library import PerformanceDrivingLibrary, DrivingTake


def _tid(emotion: str, intensity: str, seed: str) -> str:
    h = hashlib.sha1(f"{emotion}{intensity}{seed}".encode()).hexdigest()[:8]
    return f"take_{h}"


class PerformanceDrivingService:
    def __init__(self, library: PerformanceDrivingLibrary, *,
                 runway: Any = None, liveportrait: Any = None) -> None:
        self.library = library
        self.runway = runway            # E1（云首采）
        self.lp = liveportrait          # E2（本地复用）

    def perform(self, *, character_image: str | Path, emotion: str, intensity: str,
                dest: Path, beat_type: str = "", driving_ref: str | Path | None = None
                ) -> dict[str, Any]:
        """产出该角色演出该表演的视频。返回 {ok, path, route, take_id, ...}。"""
        take = self.library.find(emotion, intensity, beat_type=beat_type)

        # 路线①：库有 take + E2 本地可用 → $0 复用
        if take is not None and self.lp is not None and self.lp.available():
            driving = self.library.root / take.driving_video
            r = self.lp.animate(source=Path(character_image), driving=driving, dest=dest)
            if r.get("ok"):
                self.library.mark_used(take)
                return {"ok": True, "path": str(dest), "route": "reuse_liveportrait",
                        "take_id": take.take_id, "cost": "local_$0"}
            # 本地失败 → 落到首采/降级
        # 路线②：库缺（或本地不可用）+ E1 可用 + 有驱动参考 → 云首采 + 回流
        if self.runway is not None and self.runway.available() and driving_ref:
            item = _tid(emotion, intensity, str(dest))
            self.runway.perform(character_image=character_image, reference=driving_ref,
                                dest=dest, item_id=item)
            new_take = self.library.import_driving_video(
                dest, take_id=item, emotion=emotion, intensity=intensity,
                beat_type=beat_type, source="runway_act_one")
            return {"ok": True, "path": str(dest), "route": "acquire_runway",
                    "take_id": new_take.take_id, "cost": "cloud_paid"}
        # 都不可用 → 诚实标记（交上游降级到方案A起末帧 / 直通）
        return {"ok": False,
                "route": "unavailable",
                "reason": ("库有 take 但 E2 未装" if take is not None
                           else "库缺 take 且无 E1/驱动参考"),
                "note": "表演驱动不可用，降级（方案A起末帧/Kling自由生成）"}

    def stats(self) -> dict[str, Any]:
        return self.library.stats()


__all__ = ["PerformanceDrivingService"]
