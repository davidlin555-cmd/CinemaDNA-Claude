"""多模态视觉判断 (Phase J) —— 把 vision_judge 从插槽变成可运行检查。

分两层，诚实区分"真跑了"和"待接模型"：

1. **可运行层（ffmpeg 像素分析，本次真跑）**：对成片视频做像素级判断，抓"明显假感/
   严重崩坏"的确定性征兆——
     - 画面几乎无运动（静帧）：占位色板/渲染崩坏/冻帧
     - 画面近乎纯色（空白）：未生成/黑屏/糊成一片
   这类"素材不是真拍出来的"征兆，不需要重型模型就能真判，且立刻可跑。

2. **深判层（可选 LLM 视觉后端，插槽）**：明显 AI 假感、手部/面部崩坏、表演僵硬——
   这些要多模态视觉模型。给一个 `llm_backend` 插槽：配了就对抽帧真调，
   没配就 `deep_judged=False` **如实标记，不假装判过**。

结果写进 ctx["vision_judge"]，被 Final Review 的 no_obvious_fake 与 ShowQuality 消费；
判为假 → 走自动打回（RENDERING/PERFORMANCE），不停机。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

#: 运动能量下限：低于它视为静帧/占位/崩坏（testsrc≈0.41，纯色=0）
MIN_MOTION = 0.05
#: 亮度动态范围下限：低于它视为近纯色/空白（真画面通常几十~两百）
MIN_LUMA_SPREAD = 22.0


@runtime_checkable
class VisionLLMBackend(Protocol):
    """深判层插槽：对一帧图判 AI 假感/手脸崩坏/表演僵硬。"""

    def judge_frame(self, frame: Path) -> dict[str, Any]:
        ...


@dataclass
class VisionJudgeResult:
    judged: bool
    fake: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    motion: float = 0.0
    luma_spread: float = 0.0
    deep_judged: bool = False          # 是否接了 LLM 视觉深判
    deep: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "judged": self.judged, "fake": self.fake, "reason": "；".join(self.reasons),
            "reasons": self.reasons, "checks": self.checks,
            "motion": self.motion, "luma_spread": self.luma_spread,
            "deep_judged": self.deep_judged, "deep": self.deep,
        }


class VisionJudge:
    """成片视觉判断：ffmpeg 像素分析（真跑）+ 可选 LLM 深判（插槽）。"""

    def __init__(self, *, ffmpeg: str = "ffmpeg",
                 runner: Callable[[list[str]], str] | None = None,
                 llm_backend: VisionLLMBackend | None = None) -> None:
        self.ffmpeg = ffmpeg
        self._runner = runner or self._default_runner
        self.llm_backend = llm_backend

    def available(self) -> bool:
        return shutil.which(self.ffmpeg) is not None

    def _default_runner(self, argv: list[str]) -> str:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        return (r.stderr or "") + (r.stdout or "")

    def judge(self, video: Path) -> VisionJudgeResult:
        # 运动能量：相邻帧差的平均亮度
        mtext = self._runner([
            self.ffmpeg, "-i", str(video),
            "-vf", "tblend=all_mode=difference,signalstats,metadata=print:file=-",
            "-an", "-f", "null", "-"])
        motions = [float(x) for x in re.findall(r"YAVG=([\d.]+)", mtext)]
        motion = round(sum(motions) / len(motions), 4) if motions else 0.0

        # 亮度动态范围：每帧 (YMAX-YMIN) 的平均
        stext = self._runner([
            self.ffmpeg, "-i", str(video),
            "-vf", "signalstats,metadata=print:file=-", "-an", "-f", "null", "-"])
        ymax = [float(x) for x in re.findall(r"YMAX=([\d.]+)", stext)]
        ymin = [float(x) for x in re.findall(r"YMIN=([\d.]+)", stext)]
        spreads = [a - b for a, b in zip(ymax, ymin)]
        spread = round(sum(spreads) / len(spreads), 2) if spreads else 0.0

        reasons: list[str] = []
        checks: dict[str, bool] = {"static": False, "blank": False}
        if motion < MIN_MOTION:
            checks["static"] = True
            reasons.append(f"画面几乎无运动（motion={motion}），疑似静帧/占位/渲染崩坏")
        if spread < MIN_LUMA_SPREAD:
            checks["blank"] = True
            reasons.append(f"画面近乎纯色（动态范围={spread}），疑似空白/未生成")

        deep: dict[str, Any] = {}
        deep_judged = False
        if self.llm_backend is not None:
            frame = video.parent / "_vj_frame.jpg"
            try:
                self._runner([self.ffmpeg, "-y", "-ss", "1", "-i", str(video),
                              "-frames:v", "1", str(frame)])
                deep = self.llm_backend.judge_frame(frame)
                deep_judged = True
                # 深判命中任一缺陷（含变脸/乱码这两个短剧杀手）→ 判坏
                _flags = ("ai_fake", "hand_face_broken", "face_swap",
                          "garbled_text", "stiff")
                if any(deep.get(f) for f in _flags):
                    reasons.append("深判：" + str(deep.get("reason", "AI假感/崩坏/变脸/乱码/僵硬")))
            except Exception as e:  # noqa: BLE001
                deep = {"error": str(e)[:150]}
            finally:
                try:
                    frame.unlink()
                except OSError:
                    pass

        return VisionJudgeResult(
            judged=True, fake=bool(reasons), reasons=reasons, checks=checks,
            motion=motion, luma_spread=spread, deep_judged=deep_judged, deep=deep)


__all__ = ["VisionJudge", "VisionJudgeResult", "VisionLLMBackend",
           "MIN_MOTION", "MIN_LUMA_SPREAD"]
