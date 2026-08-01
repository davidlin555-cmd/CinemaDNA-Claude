"""真实音画合成 (Phase E3/E4) —— ffmpeg 多轨精混 + 烧录字幕。

把独立音频/字幕真正合进 MP4，且做到"能听"的多轨混音：
- 对白按各自起点铺时间线（起点重叠 = 抢白）
- BGM：可接**真实音乐文件**（bgm_path）；无则合成一段**和弦垫底**（不再只单音正弦）
- BGM 用 sidechaincompress 在对白处**自动压低（ducking）**
- 环境音**分层库**（room/rain/street/night…）按场景选，低电平铺底
- 最终 **loudnorm（EBU R128）** 统一响度到广播级 -16 LUFS
- 字幕 subtitles 滤镜**烧录**进画面

ffmpeg 命令可注入 runner（测试用假 runner）。字幕复制成 cwd 内简单名，避开路径转义。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: 分层环境音库：名字 → 生成滤镜（{d}=时长）。用 ffmpeg 噪声 + 滤波塑形。
AMBIENCE_LIBRARY: dict[str, str] = {
    "none": "",
    "room": "anoisesrc=color=brown:duration={d}:sample_rate=44100,lowpass=f=500,volume=0.015",
    "rain": "anoisesrc=color=white:duration={d}:sample_rate=44100,highpass=f=500,lowpass=f=6500,volume=0.035",
    "street": "anoisesrc=color=brown:duration={d}:sample_rate=44100,lowpass=f=900,volume=0.03",
    "night": "anoisesrc=color=brown:duration={d}:sample_rate=44100,lowpass=f=280,volume=0.012",
    "office": "anoisesrc=color=pink:duration={d}:sample_rate=44100,lowpass=f=1200,volume=0.02",
}

#: 目标响度（EBU R128 LUFS）——竖屏短剧手机外放，-16 较稳
_TARGET_LUFS = -16


class MixError(RuntimeError):
    pass


@dataclass
class DialogueClip:
    path: Path
    start: float


class AudioMixer:
    ext = ".mp4"

    def __init__(self, *, ffmpeg: str = "ffmpeg",
                 runner: Callable[[list[str], Path], None] | None = None) -> None:
        self.ffmpeg = ffmpeg
        self._runner = runner or self._default_runner

    def available(self) -> bool:
        return shutil.which(self.ffmpeg) is not None

    def _default_runner(self, argv: list[str], cwd: Path) -> None:
        subprocess.run(argv, cwd=str(cwd), check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def build_argv(self, *, video: Path, clips: list[DialogueClip], duration: float,
                   subtitles_name: str | None, bgm: bool, ambience,
                   bgm_input_index: int | None, out_name: str,
                   bgm_mood: str = "neutral", loudnorm: bool = True,
                   target_lufs: int = _TARGET_LUFS) -> list[str]:
        argv = [self.ffmpeg, "-y", "-i", str(video)]
        for c in clips:
            argv += ["-i", str(c.path)]
        # bgm 真实文件作为最后一个输入（若有）
        # （调用方负责把 -i bgm 加进 argv 前先算好 index；这里只用 index 引用）

        fc: list[str] = []
        dlabels: list[str] = []
        for i, c in enumerate(clips, start=1):
            ms = max(0, int(round(c.start * 1000)))
            fc.append(f"[{i}:a]adelay={ms}|{ms},"
                      f"aformat=sample_rates=44100:channel_layouts=stereo[d{i}]")
            dlabels.append(f"[d{i}]")
        if dlabels:
            fc.append("".join(dlabels)
                      + f"amix=inputs={len(dlabels)}:normalize=0[dlg]")
        else:
            fc.append(f"anullsrc=r=44100:cl=stereo:d={duration}[dlg]")

        mix_inputs = ["[dlgm]"]
        fc.append("[dlg]asplit=2[dlgm][dlgsc]")
        if bgm:
            if bgm_input_index is not None:
                # 真实音乐文件：裁到时长 + 压小
                fc.append(f"[{bgm_input_index}:a]aformat=sample_rates=44100:"
                          f"channel_layouts=stereo,atrim=0:{duration},"
                          "volume=0.35[bgm]")
            else:
                from . import bgm as bgm_lib   # 内置情绪曲库合成垫底
                fc += bgm_lib.bed_for(bgm_mood, duration)
            fc.append("[bgm][dlgsc]sidechaincompress="
                      "threshold=0.015:ratio=10:attack=5:release=350[bgmduck]")
            mix_inputs.append("[bgmduck]")
        else:
            fc.append("[dlgsc]anull[_drop]")
        # 分层环境音：ambience 可为单个名或多个名的列表 → 各出一层再混
        amb_names = [ambience] if isinstance(ambience, str) else list(ambience or [])
        for j, name in enumerate(amb_names):
            tmpl = AMBIENCE_LIBRARY.get(name, "")
            if tmpl:
                fc.append(tmpl.format(d=duration) + f"[amb{j}]")
                mix_inputs.append(f"[amb{j}]")
        chain = "".join(mix_inputs) + f"amix=inputs={len(mix_inputs)}:normalize=0"
        if loudnorm:
            chain += f",loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
        else:
            chain += ",dynaudnorm"
        # 音频迁就总时长用补静音（apad），绝不用 -shortest 反裁语音
        fc.append(chain + ",apad[mix]")

        # 视频迁就语音：不足则定格末帧补到总时长，超出由 -t 裁**视频**（语音永不裁）
        vf: list[str] = []
        if subtitles_name:
            style = ("FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,"
                     "BorderStyle=1,Outline=2,Shadow=0")
            vf.append(f"subtitles={subtitles_name}:force_style='{style}'")
        vf.append(f"tpad=stop_mode=clone:stop_duration={duration}")
        fc.append(f"[0:v]{','.join(vf)}[v]")
        vmap = "[v]"

        argv += ["-filter_complex", ";".join(fc),
                 "-map", vmap, "-map", "[mix]",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-t", f"{duration}", out_name]
        return argv

    def compose(self, *, video: Path, clips: list[DialogueClip], dest: Path,
                duration: float, subtitles: Path | None = None,
                bgm: bool = True, bgm_path: Path | None = None,
                bgm_mood: str = "neutral", ambience="room",
                loudnorm: bool = True, target_lufs: int = _TARGET_LUFS) -> Path:
        """合成最终 MP4（对白 + 情绪 BGM ducking + 分层环境音 + 烧录字幕 + loudnorm）。"""
        if not self.available():
            raise MixError("未找到 ffmpeg，无法音画合成")
        if not video.is_file():
            raise MixError(f"底片不存在: {video}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        subs_name = None
        subs_copy: Path | None = None
        if subtitles is not None and subtitles.is_file():
            subs_name = "_mix_subs.vtt"
            subs_copy = dest.parent / subs_name
            subs_copy.write_bytes(subtitles.read_bytes())

        # 真实 BGM 文件作为额外输入（放在 video + 对白之后）
        bgm_idx = None
        extra_inputs: list[str] = []
        if bgm and bgm_path is not None and bgm_path.is_file():
            bgm_idx = 1 + len(clips)
            extra_inputs = ["-i", str(bgm_path.resolve())]
        try:
            # ffmpeg 以 dest.parent 为 cwd（规避字幕路径转义），所以所有 -i 输入
            # 必须用**绝对路径**，否则相对路径会相对 cwd 解析失败
            abs_video = video.resolve()
            abs_clips = [DialogueClip(c.path.resolve(), c.start) for c in clips]
            argv = self.build_argv(
                video=abs_video, clips=abs_clips, duration=duration,
                subtitles_name=subs_name, bgm=bgm, ambience=ambience,
                bgm_input_index=bgm_idx, out_name=dest.name, bgm_mood=bgm_mood,
                loudnorm=loudnorm, target_lufs=target_lufs)
            # 把真实 BGM 的 -i 插到 video/对白输入之后、-filter_complex 之前
            if extra_inputs:
                fc_pos = argv.index("-filter_complex")
                argv = argv[:fc_pos] + extra_inputs + argv[fc_pos:]
            self._runner(argv, dest.parent)
        finally:
            if subs_copy is not None:
                try:
                    subs_copy.unlink()
                except OSError:
                    pass
        if not dest.is_file() or dest.stat().st_size < 1000:
            raise MixError("ffmpeg 未产出有效 MP4")
        return dest


__all__ = ["AudioMixer", "DialogueClip", "MixError", "AMBIENCE_LIBRARY"]
