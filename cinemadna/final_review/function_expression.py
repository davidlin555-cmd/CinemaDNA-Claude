"""镜头功能表达检查 —— 规格 9：技术通过但**未表达 story_function** 仍算失败。

VisionQA 原本只查技术（黑屏/冻帧/运动有无）。但一个 `story_function=施压/反转/反击`
的动作镜，如果画面几乎静止（i2v 只让人物眨眼），就是"技术合格但没演出功能"。

本模块对**动作类功能**的镜逐个用 VisionJudge 量运动能量：动作镜却近乎静止 → 判
"功能未表达" → 打回重渲该镜。信息镜（揭示）则要求屏内可读（由屏内合成保证，这里
只标注）。runner 可注入，测试不跑 ffmpeg。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

#: 需要"演出来"的动作类功能（静止=没表达）
_ACTION_FUNCS = ("钩子", "施压", "反转", "反击", "决策", "动作")
#: 动作镜的最低运动能量（低于此=几乎静止=功能没演出）
MIN_ACTION_MOTION = 0.15


@dataclass
class FunctionExpressionResult:
    verdict: str
    flagged: list[dict[str, Any]] = field(default_factory=list)
    checked: int = 0

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def report(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "checked": self.checked, "flagged": self.flagged}


class FunctionExpressionService:
    """动作类镜头的功能表达硬门（复用 VisionJudge 的运动能量）。"""

    def __init__(self, vision_judge=None, min_motion: float = MIN_ACTION_MOTION) -> None:
        self._vj = vision_judge
        self.min_motion = min_motion

    def _judge(self, video: Path) -> dict[str, Any]:
        if self._vj is None:
            from final_review.vision_judge import VisionJudge
            self._vj = VisionJudge()
        return self._vj.judge(video).to_dict()

    def review(self, items: list[dict[str, Any]]) -> FunctionExpressionResult:
        """items = [{shot_id, video: Path, story_function}]。只查动作类功能镜。"""
        flagged: list[dict[str, Any]] = []
        checked = 0
        for it in items:
            fn = it.get("story_function", "")
            vid = Path(it["video"])
            if fn not in _ACTION_FUNCS or not vid.is_file():
                continue
            checked += 1
            res = self._judge(vid)
            motion = float(res.get("motion", 1.0))
            if motion < self.min_motion:
                flagged.append({"shot_id": it["shot_id"], "function": fn,
                                "motion": motion,
                                "reason": f"{fn}镜近乎静止(motion={motion})，未演出功能"})
        return FunctionExpressionResult(
            verdict="REPAIR" if flagged else "PASS", flagged=flagged, checked=checked)


__all__ = ["FunctionExpressionService", "FunctionExpressionResult",
           "MIN_ACTION_MOTION"]
