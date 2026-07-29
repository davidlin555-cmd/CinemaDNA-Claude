"""口型对齐边界 (Phase E5) —— 音频 → 画面唇形，接口就绪、留视频模型插槽。

诚实边界：把音频对齐到嘴型是**视频模型侧**的事（Wav2Lip / SadTalker / D-ID / HeyGen 等），
不在音频链内。本模块只定义统一接口 + 直通默认，让渲染侧有一个明确插槽：
配了真实唇形模型就在 mux 之前把"人脸镜头视频 + 说话人音频"过一遍，产出对口型的视频。

`PassthroughLipSync`：不改视频（默认）。真实模型实现 `LipSyncBackend` 协议即插入。
口型策略实测（有声才张嘴/无声必闭嘴）已在 AudioDNA 审核层把关；这里是"让嘴动真对上声"。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class LipSyncBackend(Protocol):
    """把一段人脸视频按给定音频对口型，产出新视频。"""

    def sync(self, *, face_video: Path, audio: Path, dest: Path) -> dict[str, Any]:
        ...

    def available(self) -> bool:
        ...


class PassthroughLipSync:
    """直通：不做唇形对齐（默认）。真实模型未接入时的诚实占位。"""

    #: 是否真的做了对齐（直通恒 False，供下游/审核如实标记）
    real = False
    name = "passthrough"

    def available(self) -> bool:
        return True

    def sync(self, *, face_video: Path, audio: Path, dest: Path) -> dict[str, Any]:
        if not face_video.is_file():
            return {"ok": False, "error": f"人脸视频不存在: {face_video}"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest != face_video:
            dest.write_bytes(face_video.read_bytes())
        return {"ok": True, "relpath": dest.name, "lip_synced": False,
                "note": "直通：未接真实唇形模型（Wav2Lip/SadTalker/…），嘴型未真正对齐"}


class Wav2LipBackend:
    """真实唇形后端适配器（Wav2Lip 等 CLI）。检测到可执行命令才 available。

    通过环境变量 WAV2LIP_CMD 指向推理脚本（如 `python inference.py`）。命令模板里
    的 {face}/{audio}/{out} 会被替换。**未检测到命令时不冒充完成**——由 resolve() 退回直通。
    """

    real = True
    name = "wav2lip"

    def __init__(self, *, cmd: str | None = None,
                 runner: Callable[[list[str]], None] | None = None) -> None:
        self.cmd = cmd or os.environ.get("WAV2LIP_CMD", "")
        self._runner = runner or (lambda argv: subprocess.run(
            argv, check=True, timeout=600))

    def available(self) -> bool:
        if not self.cmd:
            return False
        exe = self.cmd.split()[0]
        return bool(shutil.which(exe) or Path(exe).is_file())

    def sync(self, *, face_video: Path, audio: Path, dest: Path) -> dict[str, Any]:
        if not self.available():
            return {"ok": False, "lip_synced": False, "error": "未配置 WAV2LIP_CMD"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = (self.cmd.format(face=str(face_video), audio=str(audio),
                                out=str(dest)).split())
        try:
            self._runner(argv)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "lip_synced": False, "error": str(e)[:160]}
        ok = dest.is_file() and dest.stat().st_size > 1000
        return {"ok": ok, "relpath": dest.name, "lip_synced": ok,
                "backend": "wav2lip", "note": "Wav2Lip 唇形对齐已应用" if ok else "唇形对齐失败"}


class LatentSyncBackend:
    """LatentSync 本地唇形后端（ByteDance，SOTA）——④真口型，$0 API，本机 GPU 跑。

    隔离环境：LatentSync 有自己的 Python 3.10 venv（不污染主环境），通过 subprocess
    调其 `scripts.inference`。模型/venv 就绪才 available（缺则 resolve() 退直通，不冒充）。
    路径可用环境变量覆盖：LATENTSYNC_ROOT。
    """

    real = True
    name = "latentsync"

    def __init__(self, *, root: str | Path | None = None,
                 python: str | Path | None = None,
                 unet_config: str = "configs/unet/stage2_512.yaml",
                 ckpt: str = "checkpoints/latentsync_unet.pt",
                 steps: int = 20, guidance: float = 1.5,
                 runner: Callable[[list[str], Path], int] | None = None) -> None:
        self.root = Path(root or os.environ.get(
            "LATENTSYNC_ROOT", r"E:/CinemaDNA/tools/LatentSync"))
        self.python = Path(python) if python else self.root / ".venv/Scripts/python.exe"
        self.unet_config = unet_config
        self.ckpt = ckpt
        self.steps = steps
        self.guidance = guidance
        self._runner = runner or self._default_runner

    def _default_runner(self, argv: list[str], cwd: Path) -> int:
        return subprocess.run(argv, cwd=str(cwd), timeout=1800).returncode

    def available(self) -> bool:
        return (self.python.is_file()
                and (self.root / self.ckpt).is_file()
                and (self.root / self.unet_config).is_file())

    def sync(self, *, face_video: Path, audio: Path, dest: Path) -> dict[str, Any]:
        if not self.available():
            return {"ok": False, "lip_synced": False,
                    "error": "LatentSync 未就绪（venv/模型缺失）"}
        if not Path(face_video).is_file():
            return {"ok": False, "lip_synced": False,
                    "error": f"人脸视频不存在: {face_video}"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = [str(self.python), "-m", "scripts.inference",
                "--unet_config_path", self.unet_config,
                "--inference_ckpt_path", self.ckpt,
                "--inference_steps", str(self.steps),
                "--guidance_scale", str(self.guidance), "--enable_deepcache",
                "--video_path", str(Path(face_video).resolve()),
                "--audio_path", str(Path(audio).resolve()),
                "--video_out_path", str(Path(dest).resolve())]
        try:
            rc = self._runner(argv, self.root)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "lip_synced": False, "error": str(e)[:200]}
        ok = rc == 0 and dest.is_file() and dest.stat().st_size > 1000
        return {"ok": ok, "relpath": dest.name, "lip_synced": ok,
                "backend": "latentsync",
                "note": "LatentSync 唇形对齐已应用" if ok else f"唇形对齐失败(rc={rc})"}


def resolve_lipsync(explicit: Any = None) -> Any:
    """选唇形后端：显式 > LatentSync(本地真口型) > Wav2Lip CLI > 直通（诚实占位）。"""
    if explicit is not None:
        return explicit
    ls = LatentSyncBackend()
    if ls.available():
        return ls
    w2l = Wav2LipBackend()
    return w2l if w2l.available() else PassthroughLipSync()


__all__ = ["LipSyncBackend", "PassthroughLipSync", "Wav2LipBackend",
           "LatentSyncBackend", "resolve_lipsync"]
