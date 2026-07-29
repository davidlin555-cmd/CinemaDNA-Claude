"""本地免费 TTS 后端 (Phase E2) —— Windows SAPI（System.Speech）。

不联网、不花钱、无需 API Key。作为 ElevenLabs 的兜底：工厂不应只依赖一个付费
密钥。按声音性别选 SAPI 音色（女声 Zira / 男声 David），输出真实可播放 WAV。

注意：中文发音需系统装有中文 SAPI 音色（如 Huihui）；只装英文音色时也能出声，
但中文会读得不准——这是系统音色库的限制，不是流水线问题。
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from audio import voices

#: 性别 → SAPI 音色名（英文系统预置；中文另需装 Huihui 等）
_SAPI_VOICE = {
    "female": "Microsoft Zira Desktop",
    "male": "Microsoft David Desktop",
}


class LocalTTSError(RuntimeError):
    pass


@dataclass
class LocalTTSResult:
    text: str
    voice_id: str
    model: str
    file_relpath: str
    bytes: int
    duration_sec: float
    is_real_media: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "voice_id": self.voice_id, "model": self.model,
                "file_relpath": self.file_relpath, "bytes": self.bytes,
                "duration_sec": self.duration_sec, "is_real_media": True,
                "generation_method": "windows_sapi"}


def _gender_of(voice_id: str) -> str:
    for g, prof in voices.VOICE_PROFILES.items():
        if prof["voice_id"] == voice_id:
            return g
    return "female"


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 2)
    except Exception:
        return 0.0


class SapiTTSBackend:
    """本地 Windows SAPI TTS。budget 可选（本地免费，不强制预算闸）。"""

    ext = ".wav"

    def __init__(self, *, budget=None,
                 runner: Callable[[list[str]], None] | None = None) -> None:
        self.budget = budget
        self._runner = runner or self._default_runner

    def _default_runner(self, argv: list[str]) -> None:
        subprocess.run(argv, check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def synthesize(self, *, text: str, voice_id: str, dest: Path,
                   item_id: str, settings: dict[str, float] | None = None) -> LocalTTSResult:
        if self.budget is not None:
            self.budget.authorize_generic(item_id=item_id, est_units=0.0,
                                          kind="audio", model="sapi")
        dest.parent.mkdir(parents=True, exist_ok=True)
        sapi_voice = _SAPI_VOICE[_gender_of(voice_id)]
        safe = text.replace("'", "''")
        # 情绪韵律 → SAPI 语速（-10..10）：rate>1 略快，rate<1 略慢
        rate = int(round((float((settings or {}).get("rate", 1.0)) - 1.0) * 20))
        rate = max(-10, min(10, rate))
        # 写成 UTF-8(BOM) 的 .ps1 再 -File 执行：避开 subprocess 传中文/长命令的编码坑
        script = dest.parent / f".sapi_{dest.stem}.ps1"
        ps = (
            "Add-Type -AssemblyName System.Speech\r\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\r\n"
            f"try {{ $s.SelectVoice('{sapi_voice}') }} catch {{}}\r\n"
            f"$s.Rate = {rate}\r\n"
            f"$s.SetOutputToWaveFile('{dest}')\r\n"
            f"$s.Speak('{safe}')\r\n$s.Dispose()\r\n"
        )
        script.write_bytes(b"\xef\xbb\xbf" + ps.encode("utf-8"))
        try:
            self._runner(["powershell", "-NoProfile", "-NonInteractive",
                          "-ExecutionPolicy", "Bypass", "-File", str(script)])
        finally:
            try:
                script.unlink()
            except OSError:
                pass
        if not dest.is_file() or dest.stat().st_size < 200:
            raise LocalTTSError(f"{item_id}: SAPI 未产出有效音频")
        if self.budget is not None:
            self.budget.record(shot_id=item_id, units=0.0)
        return LocalTTSResult(
            text=text, voice_id=voice_id, model="windows_sapi",
            file_relpath=dest.name, bytes=dest.stat().st_size,
            duration_sec=_wav_duration(dest))


__all__ = ["SapiTTSBackend", "LocalTTSResult", "LocalTTSError"]
