"""成片视频探测 (Phase I) —— 用 ffmpeg 真实检查黑屏/冻帧/缺帧。

判断层里"黑屏缺帧"这一项不需要重型视觉模型：ffmpeg 的 blackdetect/freezedetect +
ffprobe 帧数就能真实判定。这让 Final Review 从"结构合格"往"商业可发布"推进一格。

runner 可注入（测试返回预置文本，不真跑 ffmpeg）。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class MediaProbeResult:
    ok: bool
    duration: float
    frames: int
    expected_frames: int
    fps: float
    black_events: int
    freeze_events: int
    dropped_frames: int
    issues: list[str]

    @property
    def has_black(self) -> bool:
        return self.black_events > 0

    @property
    def has_freeze(self) -> bool:
        return self.freeze_events > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "duration": self.duration, "frames": self.frames,
            "expected_frames": self.expected_frames, "fps": self.fps,
            "black_events": self.black_events, "freeze_events": self.freeze_events,
            "dropped_frames": self.dropped_frames,
            "has_black": self.has_black, "has_freeze": self.has_freeze,
            "issues": self.issues,
        }


class MediaProbe:
    """ffmpeg 黑屏/冻帧/缺帧探测。"""

    def __init__(self, *, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 runner: Callable[[list[str]], str] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self._runner = runner or self._default_runner

    def available(self) -> bool:
        return shutil.which(self.ffmpeg) is not None

    def _default_runner(self, argv: list[str]) -> str:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=180)
        return (r.stderr or "") + (r.stdout or "")

    def probe(self, video: Path) -> MediaProbeResult:
        # 1) blackdetect + freezedetect（一趟）
        det = self._runner([
            self.ffmpeg, "-i", str(video),
            "-vf", "blackdetect=d=0.1:pix_th=0.10,freezedetect=n=-60dB:d=0.5",
            "-an", "-f", "null", "-"])
        black_events = len(re.findall(r"black_start", det))
        freeze_events = len(re.findall(r"freeze_start", det))

        # 2) 帧数/时长/帧率
        meta = self._runner([
            self.ffprobe, "-v", "error", "-select_streams", "v:0",
            "-count_frames",
            "-show_entries", "stream=nb_read_frames,r_frame_rate,duration",
            "-of", "default=nw=1", str(video)])
        frames = _int(re.search(r"nb_read_frames=(\d+)", meta))
        dur = _float(re.search(r"duration=([\d.]+)", meta))
        fps = _fps(re.search(r"r_frame_rate=(\d+)/(\d+)", meta))
        expected = int(round(dur * fps)) if dur and fps else frames
        dropped = max(0, expected - frames) if expected else 0

        issues: list[str] = []
        if black_events:
            issues.append(f"检出 {black_events} 段黑屏")
        if freeze_events:
            issues.append(f"检出 {freeze_events} 段冻帧")
        if expected and dropped > max(2, 0.02 * expected):
            issues.append(f"缺帧 {dropped}/{expected}（可能丢帧/损坏）")
        ok = not issues
        return MediaProbeResult(
            ok=ok, duration=round(dur, 2), frames=frames, expected_frames=expected,
            fps=round(fps, 2), black_events=black_events, freeze_events=freeze_events,
            dropped_frames=dropped, issues=issues)


def _int(m) -> int:
    return int(m.group(1)) if m else 0


def _float(m) -> float:
    return float(m.group(1)) if m else 0.0


def _fps(m) -> float:
    if not m:
        return 0.0
    num, den = int(m.group(1)), int(m.group(2))
    return num / den if den else 0.0


__all__ = ["MediaProbe", "MediaProbeResult"]
