"""AudioDNA (Phase E/E2) —— 声音真实可执行能力 + 专业审核 + 自动修复

从"契约声明"升级为"真实可执行"：
1. 声音性别真实绑定 → 具体音色 voice_id（女主女声 / 男主男声）
2. 真实 TTS 生成（ElevenLabs，预算闸前置）→ 每句台词一条真实音频
3. 口型策略实测：**有声才张嘴（SPEAKING_LIPSYNC 必须有音频）/ 无声必闭嘴
   （SILENT_CLOSED·REACTION 不得有说话人音频）**
4. 基础字幕：由台词 + 真实音频时长出 WebVTT（基本同步）
5. 简单对白/BGM 音量关系：对白 0dB、有对白时 BGM 压低（ducking）

`repair_contracts()` 结构修复（绑定/口型/重签）；`synthesize()` 真实出声；
`audio_review()` 专业审核（PASS / REPAIR）。失败走 Auto-Repair 的 AUDIO_FIX。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.bundle import Bundle
from director.shot_contract import (
    MAX_SHOT_DURATION_SEC,
    MIN_SHOT_DURATION_SEC,
    MOUTH_POLICIES,
    MOUTH_REACTION,
    MOUTH_SILENT_CLOSED,
    MOUTH_SPEAKING_LIPSYNC,
    STATUS_DRAFT,
    STATUS_PERFORMANCE_READY,
    check_mouth_policy,
    sign_contract,
)

from . import voices
from .tts import estimate_duration

VERDICT_PASS = "PASS"
VERDICT_REPAIR = "REPAIR"

_VOICE_GENDERS = ("female", "male")
#: 需要说话人自己出声的口型策略（这些镜头有台词就必须有音频）
_VOICED_POLICIES = frozenset({MOUTH_SPEAKING_LIPSYNC})
#: 必须闭嘴无声的口型策略（这些镜头不得出现说话人自己的音频）
_SILENT_POLICIES = frozenset({MOUTH_SILENT_CLOSED, MOUTH_REACTION})


def _stable_gender(character_id: str) -> str:
    h = int(hashlib.sha1(character_id.encode("utf-8")).hexdigest(), 16)
    return _VOICE_GENDERS[h % 2]


def _re_derive_mouth_policy(contract: dict[str, Any]) -> str:
    from director.shot_contract import _default_mouth_policy
    return _default_mouth_policy(contract["shot_type"], contract.get("dialogue"))


def _timecode(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def build_dialogue_vtt(contracts: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """由台词 + 真实音频时长（无则估算）出 WebVTT。返回 (vtt文本, cue列表)。

    时间线按镜头顺序累加；每句台词一个 cue，时长优先用真实 TTS 时长 → 基本同步。
    """
    cues: list[dict[str, Any]] = []
    lines_out = ["WEBVTT", ""]
    ordered = sorted(contracts, key=lambda c: c.get("order", 0))
    # 台词锚到**镜头在视频里的起点**(累加镜头时长,静默镜也推进) —— 治字幕/音频跑在画面
    # 前面:旧版只累加台词时长,忽略静默镜与"镜头>台词"的余量,导致字幕整体提前、中段就放完
    # (见 [[kling-motion-drift-tail]] 同 session 第三个根因)。视频时间轴=Σ镜头 duration_sec,
    # 装配即按此顺序背靠背铺,故字幕/音频同锚此时基才与画面对齐。
    shot_start = 0.0
    for c in ordered:
        shot_dur = float(c.get("duration_sec") or 0.0)
        vc = c.get("voice_contract") or {}
        bindings = {b.get("line"): b for b in (vc.get("bindings") or [])}
        t = shot_start                        # 本镜台词从镜头起点开始铺
        for line in c.get("dialogue") or []:
            txt = line.get("line", "")
            b = bindings.get(txt) or {}
            dur = float(b.get("duration_sec") or estimate_duration(txt))
            start, end = t, t + dur
            cue = {"shot_id": c["shot_id"], "speaker": line.get("character_id"),
                   "text": txt, "start": round(start, 2), "end": round(end, 2)}
            cues.append(cue)
            lines_out.append(f"{_timecode(start)} --> {_timecode(end)}")
            # 专业短剧字幕：只烧台词本身，不带说话人前缀（绝不泄露 char_id）；
            # 需要显示名时用 speaker_name（导演已按角色名回填），但默认干净无前缀。
            lines_out.append(txt)
            lines_out.append("")
            t = end + 0.15   # 句间留白
        # 视频按镜头时长推进(静默镜也占时间);缺时长则退回按台词累加(旧行为兜底)
        shot_start += shot_dur if shot_dur > 0 else max(0.0, t - shot_start)
    return "\n".join(lines_out), cues


@dataclass
class AudioReviewResult:
    story_id: str
    verdict: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    subtitle_cues: int = 0
    dialogue_lines: int = 0
    bindings_total: int = 0
    auto_assigned: int = 0
    audio_files: int = 0

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": schemas.SCHEMA_AUDIO_REVIEW,
            "story_id": self.story_id, "verdict": self.verdict,
            "passed": self.passed, "issue_count": len(self.issues),
            "issues": self.issues, "subtitle_cues": self.subtitle_cues,
            "dialogue_lines": self.dialogue_lines, "bindings_total": self.bindings_total,
            "auto_assigned_voices": self.auto_assigned, "audio_files": self.audio_files,
            "checked_at": schemas.utc_now_iso(),
        }


def _issue(shot_id: str, code: str, msg: str) -> dict[str, Any]:
    return {"shot_id": shot_id, "code": code, "message": msg, "agent": "audio"}


def audio_review(
    contracts: list[dict[str, Any]],
    *,
    story_id: str,
    characters_by_id: dict[str, dict[str, Any]] | None = None,
    rough_cut: dict[str, Any] | None = None,
    require_real_audio: bool = False,
) -> AudioReviewResult:
    """声音专业审核：性别绑定 / 口型实测 / 真实音频 / 字幕覆盖。

    require_real_audio=True 时（配了 TTS 后端）额外实测：有声才张嘴、无声必闭嘴、
    有台词镜头必须有对应真实音频。
    """
    characters_by_id = characters_by_id or {}
    issues: list[dict[str, Any]] = []
    dialogue_lines = bindings_total = auto_assigned = audio_files = 0

    for c in contracts:
        sid = c["shot_id"]
        lines = c.get("dialogue") or []
        dialogue_lines += len(lines)
        vc = c.get("voice_contract") or {}
        bindings = vc.get("bindings") or []
        bindings_total += len(bindings)
        audio_files += sum(1 for b in bindings if b.get("audio_file"))

        bound_speakers = {b.get("speaker") for b in bindings}
        for line in lines:
            if line["character_id"] not in bound_speakers:
                issues.append(_issue(sid, "VOICE_UNBOUND",
                    f"台词说话人 {line['character_id']} 没有声音绑定"))

        for b in bindings:
            g = b.get("voice_gender")
            if g in (None, "", "unspecified"):
                issues.append(_issue(sid, "VOICE_GENDER_UNSPECIFIED",
                    f"说话人 {b.get('speaker')} 声音性别未定"))
            elif g not in _VOICE_GENDERS:
                issues.append(_issue(sid, "VOICE_GENDER_INVALID",
                    f"说话人 {b.get('speaker')} 声音性别非法：{g}"))
            else:
                ch_gender = (characters_by_id.get(b.get("speaker")) or {}).get("gender")
                if ch_gender in _VOICE_GENDERS and ch_gender != g:
                    issues.append(_issue(sid, "VOICE_GENDER_MISMATCH",
                        f"说话人 {b.get('speaker')} 声音性别 {g} 与人物 {ch_gender} 不符"))
                if b.get("auto_assigned"):
                    auto_assigned += 1

        # 口型铁律（合约声明层）
        policy = c.get("mouth_policy")
        if policy not in MOUTH_POLICIES:
            issues.append(_issue(sid, "MOUTH_POLICY_MISSING", "缺合法口型策略"))
        for m in check_mouth_policy(c):
            issues.append(_issue(sid, "MOUTH_POLICY_VIOLATION", m))

        # 口型实测（接了真实音频后）：有声才张嘴 / 无声必闭嘴
        if require_real_audio:
            has_speaker_audio = any(b.get("audio_file") for b in bindings)
            if policy in _VOICED_POLICIES and lines:
                for b in bindings:
                    if b.get("line") and not b.get("audio_file"):
                        issues.append(_issue(sid, "VOICED_NO_AUDIO",
                            f"张嘴说话镜头但台词「{b.get('line')}」缺真实音频（会无声张嘴）"))
            if policy in _SILENT_POLICIES and has_speaker_audio:
                issues.append(_issue(sid, "SILENT_HAS_AUDIO",
                    f"{policy} 无声镜头却挂了说话人音频（会闭嘴出声）"))
            if lines and not c.get("audio_mix"):
                issues.append(_issue(sid, "AUDIO_MIX_MISSING",
                    "有对白但缺对白/BGM 音量关系（audio_mix）"))

    subtitle_cues = len((rough_cut or {}).get("subtitles") or [])
    if dialogue_lines > 0 and rough_cut is not None and subtitle_cues < dialogue_lines:
        issues.append(_issue("*", "SUBTITLE_COVERAGE",
            f"字幕 cue {subtitle_cues} 少于台词 {dialogue_lines}，字幕不全"))

    verdict = VERDICT_PASS if not issues else VERDICT_REPAIR
    return AudioReviewResult(
        story_id=story_id, verdict=verdict, issues=issues,
        subtitle_cues=subtitle_cues, dialogue_lines=dialogue_lines,
        bindings_total=bindings_total, auto_assigned=auto_assigned,
        audio_files=audio_files)


class AudioDNAService:
    """声音模块：绑定 / 真实 TTS / 口型实测 / 字幕 / 音量关系 的审核与修复。"""

    def __init__(self, bundle: Bundle | None = None, *, tts_backend=None,
                 voice_registry=None) -> None:
        self.bundle = bundle
        #: 可选真实 TTS 后端（ElevenLabs / Edge / SAPI）。None=只做契约层（不出声）。
        self.tts_backend = tts_backend
        #: 可选角色声音登记表（每角色固定嗓子/克隆音色）。None=按性别池派生。
        self.voice_registry = voice_registry

    def _apply_voice(self, binding: dict[str, Any]) -> bool:
        if self.voice_registry is not None:
            return self.voice_registry.apply(binding)
        return voices.apply_voice(binding)

    # -- 结构修复（绑定 + 口型 + 音色 + 重签）----------------------------

    def repair_contracts(
        self, contracts: list[dict[str, Any]], *,
        characters_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        characters_by_id = characters_by_id or {}
        fixed: list[str] = []
        for c in contracts:
            changed = self._bind_voices(c, characters_by_id)
            changed = self._fix_mouth_policy(c) or changed
            if changed:
                self._resign(c)
                fixed.append(c["shot_id"])
        return fixed

    def _bind_voices(self, contract, characters_by_id) -> bool:
        vc = contract.get("voice_contract") or {}
        changed = False
        for b in vc.get("bindings") or []:
            if b.get("voice_gender") not in _VOICE_GENDERS:
                spk = b.get("speaker", "")
                ch_gender = (characters_by_id.get(spk) or {}).get("gender")
                if ch_gender in _VOICE_GENDERS:
                    b["voice_gender"] = ch_gender
                else:
                    b["voice_gender"] = _stable_gender(spk)
                    b["auto_assigned"] = True
                changed = True
            changed = self._apply_voice(b) or changed   # 补音色 voice_id（含登记表/克隆）
        return changed

    def _fix_mouth_policy(self, contract) -> bool:
        if contract.get("mouth_policy") in MOUTH_POLICIES and not check_mouth_policy(contract):
            return False
        contract["mouth_policy"] = _re_derive_mouth_policy(contract)
        return True

    def _resign(self, contract) -> None:
        if contract.get("status") == STATUS_PERFORMANCE_READY:
            contract["status"] = STATUS_DRAFT
            contract["contract_hash"] = None
        if contract.get("status") == STATUS_DRAFT:
            sign_contract(contract)

    # -- 真实 TTS 出声 + 音量关系 --------------------------------------

    def synthesize(
        self, contracts: list[dict[str, Any]], *,
        characters_by_id: dict[str, dict[str, Any]] | None = None,
        only_missing: bool = True,
    ) -> list[str]:
        """给每句台词生成真实音频，写进合约绑定并重签。返回出声的 shot_id。

        only_missing=True 只补缺失音频（重跑不重复扣费）。无 TTS 后端/无 Bundle → 跳过。
        """
        if self.tts_backend is None or self.bundle is None:
            return []
        characters_by_id = characters_by_id or {}
        done: list[str] = []
        for c in contracts:
            vc = c.get("voice_contract") or {}
            bindings = vc.get("bindings") or []
            changed = False
            for i, b in enumerate(bindings):
                line = b.get("line")
                if not line:
                    continue
                if only_missing and b.get("audio_file"):
                    continue
                if b.get("voice_gender") not in _VOICE_GENDERS:
                    self._bind_voices(c, characters_by_id)
                self._apply_voice(b)
                # 表演语气 Agent 给的 prosody 优先（声画一致），否则退到情绪推导
                settings = voices.voice_settings_for(c.get("emotion"), b.get("prosody"))
                b["voice_settings"] = settings           # 情绪韵律记账
                ext = getattr(self.tts_backend, "ext", ".mp3")
                rel = f"05_performance/audio/{c['shot_id']}/{i:02d}_{b['speaker']}{ext}"
                try:
                    r = self.tts_backend.synthesize(
                        text=line, voice_id=b["voice_id"],
                        dest=self.bundle.path_for(rel),
                        item_id=f"tts_{c['shot_id']}_{i}", settings=settings)
                except Exception as e:  # noqa: BLE001 单句失败不崩，审核会如实拦
                    b["audio_error"] = str(e)[:200]
                    continue
                b["audio_file"] = rel
                b["audio_abspath"] = str(self.bundle.path_for(rel).resolve())
                b["duration_sec"] = r.duration_sec
                b["audio_meta"] = r.to_dict()
                changed = True
            if changed or (c.get("dialogue") and not c.get("audio_mix")):
                c["has_real_audio"] = any(b.get("audio_file") for b in bindings)
                # 口型绑定**真实音频时序**：口型开合窗口来自真实音频时长，不是估算。
                # 唇形模型（接入后）据此对齐；QA 据此验证"说话镜有真实音频支撑"。
                c["mouth_timing"] = [
                    {"speaker": b.get("speaker"), "audio_file": b["audio_file"],
                     "duration_sec": float(b["duration_sec"])}
                    for b in bindings if b.get("audio_file") and b.get("duration_sec")]
                c["mouth_bound_real_audio"] = (
                    bool(c["mouth_timing"]) if c.get("dialogue") else True)
                self._build_mix(c)
                self._resign(c)
                if changed:
                    done.append(c["shot_id"])
        return done

    #: 语音驱动时长的短停顿（秒）——呼吸/留白，故意保守
    _LEAD_PAUSE = 0.3
    _TAIL_PAUSE = 0.5
    _INTER_LINE_PAUSE = 0.3

    def reconcile_durations(self, contracts: list[dict[str, Any]]) -> list[str]:
        """语音驱动镜头时长回写（因果链核心）。

        - 有对白镜：`duration_sec = max(真实语音总时长 + 短停顿, 导演最低时长)`
          → 语音是驱动源，导演时长只作最低地板，绝不用固定时长反裁语音。
        - 无对白反应镜：保留导演时长（没有语音依据，节奏由导演定）。

        改动后重签合约（hash 随新时长更新）。返回被改时长的 shot_id。
        必须在 synthesize() 之后、渲染/PreRender 之前调用。
        """
        changed: list[str] = []
        for c in contracts:
            bindings = (c.get("voice_contract") or {}).get("bindings") or []
            voiced = [b for b in bindings
                      if b.get("audio_file") and b.get("duration_sec")]
            director_min = round(float(c.get("duration_sec") or 0), 2)
            if not voiced:
                # 无对白：语音无从驱动，保留导演时长，如实标注来源
                c["duration_provenance"] = {
                    "source": "director_no_dialogue", "director_min_sec": director_min}
                self._resign(c)     # 改了合约就必须重签，否则 hash 失配被拒渲
                continue
            voice_total = sum(float(b["duration_sec"]) for b in voiced)
            pause = (self._LEAD_PAUSE + self._TAIL_PAUSE
                     + self._INTER_LINE_PAUSE * max(0, len(voiced) - 1))
            voice_driven = round(voice_total + pause, 2)
            want = round(max(voice_driven, director_min), 2)
            # 单镜时长硬边界 [1,12]：超长台词应由导演拆镜，这里 clamp 并如实标注
            final = round(min(max(want, MIN_SHOT_DURATION_SEC),
                              MAX_SHOT_DURATION_SEC), 2)
            c["duration_provenance"] = {
                "source": "voice_driven" if voice_driven >= director_min
                          else "director_min_floor",
                "voice_total_sec": round(voice_total, 2),
                "pause_sec": round(pause, 2),
                "director_min_sec": director_min,
                "final_sec": final,
                "clamped": final != want,
            }
            if abs(final - director_min) > 0.01:
                c["duration_sec"] = final
                changed.append(c["shot_id"])
            self._resign(c)     # 无论时长是否变，都重签以纳入 provenance（防 hash 失配）
        return changed

    def _build_mix(self, contract) -> None:
        """简单对白/BGM 音量关系：对白 0dB；有对白时 BGM 压低（ducking）。"""
        voiced = bool(contract.get("dialogue"))
        contract["audio_mix"] = {
            "dialogue_gain_db": 0.0 if voiced else None,
            "bgm_gain_db": -18.0 if voiced else -12.0,
            "ducking": voiced,
            "note": "对白 0dB；有对白时 BGM 自动压低到 -18dB（ducking）",
        }

    def write_subtitles(self, contracts: list[dict[str, Any]], *, story_id: str
                        ) -> list[dict[str, Any]]:
        """出 WebVTT 字幕（真实音频时长驱动时基本同步）并落 Bundle。返回 cue 列表。"""
        vtt, cues = build_dialogue_vtt(contracts)
        if self.bundle is not None and cues:
            dest = self.bundle.path_for(f"10_outputs/{story_id}_dialogue.vtt")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(vtt, encoding="utf-8")
        return cues

    # -- 真实音画合成（ffmpeg mux + ducking + 烧录字幕）------------------

    def compose_final(
        self, contracts: list[dict[str, Any]], *, story_id: str,
        video_relpath: str, mixer, dest_relpath: str = "",
        bgm: bool = True, ambience="room", bgm_mood: str = "",
        bgm_path=None, loudnorm: bool = True, target_lufs: int = 0,
    ) -> dict[str, Any]:
        """把对白音频 + BGM(ducking) + 环境音 + 字幕合进底片视频，出最终 MP4。

        对白按字幕 cue 的起点铺到时间线（起点重叠即抢白）。返回结果字典。
        """
        from .mixer import DialogueClip
        if self.bundle is None:
            return {"ok": False, "error": "无 Bundle"}
        if mixer is None or not mixer.available():
            return {"ok": False, "error": "无可用 ffmpeg 混流器"}
        video = self.bundle.path_for(video_relpath)
        if not video.is_file():
            return {"ok": False, "error": f"底片不存在: {video_relpath}"}

        _, cues = build_dialogue_vtt(contracts)
        # cue.text → 找到对应绑定的真实音频
        audio_by_text: dict[str, str] = {}
        for c in contracts:
            for b in (c.get("voice_contract") or {}).get("bindings") or []:
                if b.get("audio_file"):
                    audio_by_text[b.get("line")] = b["audio_file"]
        clips: list[DialogueClip] = []
        end = 0.0
        for cue in cues:
            end = max(end, cue["end"])
            rel = audio_by_text.get(cue["text"])
            if rel:
                clips.append(DialogueClip(
                    path=self.bundle.path_for(rel), start=cue["start"]))

        import subprocess
        try:
            video_dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=nw=1:nk=1", str(video)],
                capture_output=True, text=True, timeout=30).stdout.strip() or 0.0)
        except Exception:
            video_dur = 0.0
        # 主时间轴 = max(视频, 语音末端)：语音更长则视频补帧，语音绝不被裁
        dur = round(max(video_dur, end, 1.0), 2)

        # 未指定 BGM 情绪/响度目标 → 由整片主导情绪自动选（对白多→-16，强情绪→-14）
        from . import bgm as bgm_lib
        if not bgm_mood:
            moods = [bgm_lib.mood_for_emotion(c.get("emotion")) for c in contracts]
            bgm_mood = max(set(moods), key=moods.count) if moods else "neutral"
        if not target_lufs:
            avg_intensity = sum(
                float((c.get("emotion") or {}).get("intensity", 0.5) or 0.5)
                for c in contracts) / max(1, len(contracts))
            target_lufs = -14 if avg_intensity >= 0.75 else -16

        vtt_path = self.bundle.path_for(f"10_outputs/{story_id}_dialogue.vtt")
        dest_rel = dest_relpath or f"10_outputs/{story_id}_final.mp4"
        dest = self.bundle.path_for(dest_rel)
        from pathlib import Path as _P
        try:
            mixer.compose(
                video=video, clips=clips, dest=dest, duration=round(dur, 2),
                subtitles=vtt_path if vtt_path.is_file() else None,
                bgm=bgm, bgm_path=_P(bgm_path) if bgm_path else None,
                bgm_mood=bgm_mood, ambience=ambience,
                loudnorm=loudnorm, target_lufs=target_lufs)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        return {"ok": True, "relpath": dest_rel,
                "abspath": str(dest.resolve()), "bytes": dest.stat().st_size,
                "dialogue_clips": len(clips), "duration_sec": round(dur, 2),
                "bgm": bgm, "bgm_real": bool(bgm_path), "bgm_mood": bgm_mood,
                "ambience": ambience, "target_lufs": target_lufs,
                "loudnorm": loudnorm, "burned_subtitles": vtt_path.is_file()}

    # -- 专业审核 --------------------------------------------------------

    def review(
        self, contracts: list[dict[str, Any]], *, story_id: str,
        characters_by_id: dict[str, dict[str, Any]] | None = None,
        require_real_audio: bool = False,
    ) -> AudioReviewResult:
        # 出字幕并作为覆盖依据喂进审核
        cues = self.write_subtitles(contracts, story_id=story_id)
        result = audio_review(
            contracts, story_id=story_id, characters_by_id=characters_by_id,
            rough_cut={"subtitles": cues}, require_real_audio=require_real_audio)
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/audio/{story_id}.json", result.report())
        return result
