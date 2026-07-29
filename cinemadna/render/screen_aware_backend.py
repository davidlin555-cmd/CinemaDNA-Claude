"""屏内道具感知的 image2video 后端 —— 真实 Kling 底 + Chrome 屏内UI合成插入。

全片冲 PASS_FULL 的最后一块拼图：把已在资产阶段渲好的屏内道具 PNG
（`04_props/{pid}/{state}.png`，Chrome 矢量渲染、文字清晰）**合成插入**到
Kling image2video 出的真实镜头上。人物镜保持纯真实素材；含屏内道具的镜头
在真实底片上叠加清晰可读的手机/账单界面（验证见 plan_c）。

这是 Registry 扩展点的正确用法：只加一个后端，路由/参考注入/重试/一致性/
预算闸/下载隔离全部复用，orchestrator 一行不改。

合成用 ffmpeg，runner 可注入（测试用假 runner，不真跑、不花钱）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from .backend import MediaSink, RenderRequest
from .kling_backend import KlingImageToVideoBackend

#: ffmpeg 合成 runner：(argv) → None，跑完不抛即成功
CompositeRunner = Callable[[list[str]], None]


def _default_ffmpeg_runner(argv: list[str]) -> None:
    subprocess.run(argv, check=True, timeout=300,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_composite_argv(*, base: Path, screens: list[Path], dest: Path,
                         duration: float, ffmpeg: str = "ffmpeg") -> list[str]:
    """真实底片 + N 张屏内UI PNG → 分段插入的合成片 argv。

    布局：前 ~18% 时长露真人建立场景，其余时长均分给各屏内道具依次插入；
    每张 UI 缩成手机面板（深色描边）居中，加正弦微漂移（活的合成、有运动能量）。
    """
    lead = round(max(0.6, duration * 0.18), 2)      # 真人前导
    span = max(0.5, (duration - lead) / max(1, len(screens)))
    argv = [ffmpeg, "-y", "-loglevel", "error", "-i", str(base)]
    for s in screens:
        argv += ["-i", str(s)]

    parts = ["[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
             "crop=1080:1920,setsar=1,fps=30[base]"]
    prev = "base"
    for i, _s in enumerate(screens):
        idx = i + 1
        t0 = round(lead + i * span, 2)
        t1 = round(lead + (i + 1) * span + 0.05, 2)   # 轻微交叠避免黑缝
        parts.append(f"[{idx}:v]scale=-2:1300,pad=iw+44:ih+44:22:22:"
                     f"color=0x0c0c0c[ui{idx}]")
        out = f"b{idx}"
        parts.append(
            f"[{prev}][ui{idx}]overlay="
            f"x='(W-w)/2+7*sin(2*t)':y='(H-h)/2+5*cos(1.5*t)':"
            f"enable='between(t,{t0},{t1})'[{out}]")
        prev = out
    fc = ";".join(parts)
    argv += ["-filter_complex", fc, "-map", f"[{prev}]",
             "-t", f"{duration}", "-r", "30",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest)]
    return argv


class ScreenAwareImageToVideoBackend(KlingImageToVideoBackend):
    """Kling image2video 真实底 + 屏内道具 PNG 合成。"""

    name = "screen-aware-image2video"

    def __init__(self, *, bundle_root: Path | None = None,
                 composite_runner: CompositeRunner | None = None, **kw: Any) -> None:
        super().__init__(bundle_root=bundle_root, **kw)
        self._composite_runner = composite_runner or _default_ffmpeg_runner

    def _screen_pngs_for(self, request: RenderRequest) -> list[Path]:
        """找本镜引用的屏内道具已渲 PNG（资产阶段 Chrome 出的清晰 UI）。"""
        props = (request.reference_assets or {}).get("props") or {}
        if not props or self._bundle_root is None:
            return []
        root = Path(self._bundle_root) / request.bundle_id / "04_props"
        found: list[Path] = []
        for pid in props:
            d = root / str(pid)
            if not d.is_dir() or not self._is_screen_prop(d):
                continue          # 只合成真·屏内道具，物理道具(FLUX)不当面板叠
            pngs = sorted(p for p in d.glob("*.png") if not p.name.startswith("_"))
            if pngs:
                found.append(pngs[0])   # 每个屏内道具取一态即可
        return found

    @staticmethod
    def _is_screen_prop(prop_dir: Path) -> bool:
        """靠 prop.json 的 screen_kind 区分屏内道具(payment/phone_chat…) vs 物理道具。"""
        import json
        pj = prop_dir / "prop.json"
        if not pj.is_file():
            return False
        try:
            return bool(json.loads(pj.read_text(encoding="utf-8")).get("screen_kind"))
        except (OSError, ValueError):
            return False

    def render(self, request: RenderRequest, *, sink: MediaSink) -> dict[str, Any]:
        # 1) 真实 Kling image2video 出底片（人脸参考驱动，预算闸在父类里前置）
        rendered = super().render(request, sink=sink)
        # 2) 有屏内道具则合成插入（Chrome 清晰 UI 叠到真实底上）
        screens = self._screen_pngs_for(request)
        if screens:
            base_abs = sink.abs_path(rendered["file_relpath"])
            tmp = base_abs.parent / f"_composite_{request.shot_id}.mp4"
            argv = build_composite_argv(
                base=base_abs.resolve(), screens=[s.resolve() for s in screens],
                dest=tmp, duration=float(request.duration_sec))
            self._composite_runner(argv)
            if tmp.is_file() and tmp.stat().st_size > 1000:
                os.replace(tmp, base_abs)
                rendered["screen_composited"] = [s.name for s in screens]
            else:
                # 合成失败不静默：留原真实底片，标注未合成（诚实）
                rendered["screen_composite_failed"] = True
                if tmp.exists():
                    tmp.unlink()
        return rendered


__all__ = ["ScreenAwareImageToVideoBackend", "build_composite_argv"]
