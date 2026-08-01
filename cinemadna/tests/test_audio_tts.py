"""AudioDNA 真实能力 (Phase E2) 测试 —— TTS 后端 / 真实出声 / 口型实测 / 字幕 / 音量

全程 mock HTTP（返回假 MP3 bytes），不发真实请求、不花钱。验证：
- 预算闸前置（未开预算真实提交被拒）
- 女声/男声绑定到具体 voice_id
- 每句台词出真实音频、写进合约、重签
- 口型实测：有声才张嘴、无声必闭嘴
- 字幕 WebVTT 由真实时长驱动
- 简单对白/BGM 音量关系
"""

from __future__ import annotations

import pytest

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from audio.service import AudioDNAService, audio_review, build_dialogue_vtt
from audio.tts import ElevenLabsTTSBackend, TTSError, estimate_duration
from audio import voices
from director.shot_contract import (
    MOUTH_SILENT_CLOSED,
    MOUTH_SPEAKING_LIPSYNC,
    SHOT_MEDIUM,
    STATUS_DRAFT,
    new_shot_contract,
    sign_contract,
)
from render.budget import BudgetGate, BudgetNotArmedError, confirm_yes

# 一段够大的假 MP3（>200 字节，带 ID3 头）
_MP3 = b"ID3\x03\x00\x00\x00" + b"\x00" * 400


class FakeTTSHttp:
    def __init__(self, status=200):
        self.calls = []
        self.status = status

    def __call__(self, method, url, headers, body):
        self.calls.append((url, body))
        if self.status != 200:
            return self.status, b"error"
        return 200, _MP3


def tts_backend(http=None, budget=None):
    return ElevenLabsTTSBackend(
        budget=budget or BudgetGate(max_units=5.0, confirm=confirm_yes),
        api_key="k", http=http or FakeTTSHttp())


def _contract(*, dialogue, mouth, speakers=("char_a",), gender_map=None, shot_id="SH1"):
    c = new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001", order=1,
        shot_type=SHOT_MEDIUM,
        quad=Quad(story_id="drama_0001", bundle_id="b1", task_id="t1", asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "x"},
        duration_sec=5.0, emotion={"beat": "CONFLICT", "intensity": 0.6, "mood": "x"},
        scene_asset={"asset_id": "sc", "asset_hash": "sha256:" + "a"*64,
                     "gate_passed": True, "tags": []},
        character_assets=[{"character_id": s, "master_pack_id": "cmp",
                           "asset_hash": "sha256:" + "b"*64, "gate_passed": True}
                          for s in speakers],
        prop_assets=[], dialogue=dialogue, mouth_policy=mouth)
    c["performance"] = {"acting_beats": [
        {"type": "LINE", "character_id": s, "cue": "", "micro_expression": {}}
        for s in speakers]}
    gm = gender_map or {}
    c["voice_contract"] = {"has_dialogue": bool(dialogue), "bindings": [
        {"speaker": ln["character_id"],
         "voice_gender": gm.get(ln["character_id"], "female"), "line": ln["line"]}
        for ln in dialogue]}
    if c["status"] != STATUS_DRAFT:
        c["status"] = STATUS_DRAFT
        c["contract_hash"] = None
    sign_contract(c)
    return c


class TestTTSBackend:
    def test_requires_budget(self):
        with pytest.raises(TTSError):
            ElevenLabsTTSBackend(budget=None, api_key="k")  # type: ignore

    def test_disarmed_budget_blocks(self, tmp_path):
        http = FakeTTSHttp()
        be = ElevenLabsTTSBackend(budget=BudgetGate(), api_key="k", http=http)
        with pytest.raises(BudgetNotArmedError):
            be.synthesize(text="你好", voice_id="v1",
                          dest=tmp_path / "a.mp3", item_id="x")
        assert http.calls == []          # 没发请求

    def test_synthesize_writes_audio_and_records(self, tmp_path):
        g = BudgetGate(max_units=5.0, confirm=confirm_yes)
        be = tts_backend(budget=g)
        r = be.synthesize(text="钱什么时候还", voice_id="21m00Tcm4TlvDq8ikWAM",
                          dest=tmp_path / "a.mp3", item_id="s1")
        assert (tmp_path / "a.mp3").exists()
        assert r.bytes > 200 and r.is_real_media and r.duration_sec > 0
        assert g.spent_units > 0

    def test_http_error_raises(self, tmp_path):
        be = tts_backend(http=FakeTTSHttp(status=401))
        with pytest.raises(TTSError):
            be.synthesize(text="x", voice_id="v", dest=tmp_path / "a.mp3", item_id="s")


class TestVoiceBinding:
    def test_gender_maps_to_voice_pool(self):
        b = {"speaker": "char_x", "voice_gender": "male"}
        assert voices.apply_voice(b) is True
        male_ids = {v["voice_id"] for v in voices.VOICE_POOLS["male"]}
        assert b["voice_id"] in male_ids            # 男角色 → 男声池

    def test_per_character_distinct_and_stable(self):
        a = {"speaker": "char_a", "voice_gender": "female"}
        b = {"speaker": "char_b", "voice_gender": "female"}
        voices.apply_voice(a); voices.apply_voice(b)
        # 同性别不同角色尽量不同嗓子；同角色跨镜一致
        a2 = {"speaker": "char_a", "voice_gender": "female"}
        voices.apply_voice(a2)
        assert a["voice_id"] == a2["voice_id"]      # 稳定
        assert {a["voice_id"], b["voice_id"]} <= {
            v["voice_id"] for v in voices.VOICE_POOLS["female"]}

    def test_emotion_prosody(self):
        calm = voices.voice_settings_for({"intensity": 0.2})
        intense = voices.voice_settings_for({"intensity": 0.95})
        assert intense["stability"] < calm["stability"]   # 强情绪更不稳（起伏大）
        assert intense["rate"] > calm["rate"]             # 强情绪略快


class TestSynthesisAndReview:
    def _svc(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        return AudioDNAService(bundle, tts_backend=tts_backend()), bundle

    def test_synthesize_fills_bindings_and_resigns(self, tmp_path):
        svc, bundle = self._svc(tmp_path)
        c = _contract(dialogue=[{"character_id": "char_a", "line": "钱什么时候还"}],
                      mouth=MOUTH_SPEAKING_LIPSYNC, gender_map={"char_a": "female"})
        old_hash = c["contract_hash"]
        done = svc.synthesize([c], characters_by_id={
            "char_a": {"character_id": "char_a", "gender": "female"}})
        assert c["shot_id"] in done
        b = c["voice_contract"]["bindings"][0]
        assert b["audio_file"] and bundle.path_for(b["audio_file"]).exists()
        assert b["voice_id"] in {v["voice_id"] for v in voices.VOICE_POOLS["female"]}
        assert b.get("voice_settings")               # 情绪韵律已记账
        assert c["has_real_audio"] is True
        assert c["audio_mix"]["ducking"] is True         # 有对白 → BGM 压低
        assert c["contract_hash"] != old_hash            # 重签

    def test_voiced_needs_audio_realtest(self, tmp_path):
        """有声才张嘴：SPEAKING_LIPSYNC 但没出音频 → 实测判负。"""
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SPEAKING_LIPSYNC, gender_map={"char_a": "female"})
        r = audio_review([c], story_id="d1", require_real_audio=True)
        assert any(i["code"] == "VOICED_NO_AUDIO" for i in r.issues)

    def test_synthesized_voiced_passes_realtest(self, tmp_path):
        svc, _ = self._svc(tmp_path)
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SPEAKING_LIPSYNC, gender_map={"char_a": "female"})
        svc.synthesize([c])
        r = audio_review([c], story_id="d1", require_real_audio=True)
        assert not any(i["code"] == "VOICED_NO_AUDIO" for i in r.issues)

    def test_silent_forbids_audio_realtest(self, tmp_path):
        """无声必闭嘴：SILENT_CLOSED 却挂了说话人音频 → 实测判负。"""
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SPEAKING_LIPSYNC, gender_map={"char_a": "female"})
        # 人为造无声却有音频的矛盾
        AudioDNAService(Bundle(tmp_path, "b1").ensure(),
                        tts_backend=tts_backend()).synthesize([c])
        c["mouth_policy"] = MOUTH_SILENT_CLOSED
        r = audio_review([c], story_id="d1", require_real_audio=True)
        assert any(i["code"] == "SILENT_HAS_AUDIO" for i in r.issues)

    def test_only_missing_no_double_spend(self, tmp_path):
        svc, _ = self._svc(tmp_path)
        c = _contract(dialogue=[{"character_id": "char_a", "line": "你好"}],
                      mouth=MOUTH_SPEAKING_LIPSYNC, gender_map={"char_a": "female"})
        svc.synthesize([c])
        spent = svc.tts_backend.budget.spent_units
        svc.synthesize([c], only_missing=True)           # 已有音频 → 不再出
        assert svc.tts_backend.budget.spent_units == spent


class TestLocalBackend:
    def test_sapi_backend_writes_audio(self, tmp_path):
        """本地 SAPI 后端（注入假 runner，写一段假 WAV）→ 出真实文件、按性别选音色。"""
        from audio.tts_local import SapiTTSBackend

        def fake_runner(argv):
            # -File <script.ps1>：从脚本里抠出 SetOutputToWaveFile 的目标路径写假 WAV
            script = argv[-1]
            import re
            from pathlib import Path as _P
            txt = _P(script).read_text(encoding="utf-8")
            m = re.search(r"SetOutputToWaveFile\('([^']+)'\)", txt)
            _P(m.group(1)).write_bytes(b"RIFF" + b"\x00" * 500 + b"WAVEfmt ")
            assert "Zira" in txt or "David" in txt          # 按性别选了音色

        be = SapiTTSBackend(runner=fake_runner)
        assert be.ext == ".wav"
        r = be.synthesize(text="hello", voice_id=voices.VOICE_PROFILES["male"]["voice_id"],
                          dest=tmp_path / "05_performance" / "a.wav", item_id="s1")
        assert (tmp_path / "05_performance" / "a.wav").exists()
        assert r.bytes > 200 and r.is_real_media


class TestMixer:
    def test_build_argv_has_ducking_burn_loudnorm(self, tmp_path):
        from audio.mixer import AudioMixer, DialogueClip
        mx = AudioMixer(runner=lambda argv, cwd: None)
        argv = mx.build_argv(
            video=tmp_path / "v.mp4",
            clips=[DialogueClip(tmp_path / "d1.wav", 0.5),
                   DialogueClip(tmp_path / "d2.wav", 5.8)],
            duration=11.0, subtitles_name="_mix_subs.vtt",
            bgm=True, ambience="night", bgm_input_index=None,
            out_name="final.mp4", loudnorm=True)
        fc = argv[argv.index("-filter_complex") + 1]
        assert "sidechaincompress" in fc          # BGM ducking
        assert "subtitles=_mix_subs.vtt" in fc    # 烧录字幕
        assert "adelay=500" in fc and "adelay=5800" in fc   # 抢白/起点偏移
        assert "anoisesrc" in fc                  # 分层环境音（night 预设）
        assert "loudnorm" in fc                   # EBU R128 响度统一
        assert "261.63" in fc                     # 和弦垫底（不再单音）

    def test_real_bgm_file_input(self, tmp_path):
        from audio.mixer import AudioMixer, DialogueClip
        (tmp_path / "v.mp4").write_bytes(b"\x00" * 5000)
        (tmp_path / "bgm.mp3").write_bytes(b"\x00" * 3000)
        seen = {}

        def fake_runner(argv, cwd):
            seen["argv"] = argv
            (cwd / "out.mp4").write_bytes(b"\x00" * 5000)

        mx = AudioMixer(runner=fake_runner)
        object.__setattr__(mx, "available", lambda: True)
        mx.compose(video=tmp_path / "v.mp4",
                   clips=[DialogueClip(tmp_path / "d.wav", 0.0)],
                   dest=tmp_path / "out.mp4", duration=6.0,
                   bgm_path=tmp_path / "bgm.mp3", ambience="rain")
        argv = seen["argv"]
        assert str(tmp_path / "bgm.mp3") in argv          # 真实 BGM 作为输入
        fc = argv[argv.index("-filter_complex") + 1]
        assert "atrim=0:6.0" in fc                         # BGM 裁到时长

    def test_compose_runs_and_writes(self, tmp_path):
        from audio.mixer import AudioMixer, DialogueClip
        (tmp_path / "v.mp4").write_bytes(b"\x00" * 5000)
        (tmp_path / "subs.vtt").write_text("WEBVTT\n", encoding="utf-8")
        seen = {}

        def fake_runner(argv, cwd):
            seen["argv"] = argv
            (cwd / "out.mp4").write_bytes(b"\x00" * 5000)   # 假装 ffmpeg 出片

        mx = AudioMixer(runner=fake_runner)
        mx.ffmpeg = "ffmpeg"
        # 绕过 available()（假 runner 场景）
        object.__setattr__(mx, "available", lambda: True)
        out = mx.compose(
            video=tmp_path / "v.mp4",
            clips=[DialogueClip(tmp_path / "d.wav", 0.0)],
            dest=tmp_path / "out.mp4", duration=6.0,
            subtitles=tmp_path / "subs.vtt")
        assert out.exists()
        # 字幕中转文件用完即删
        assert not (tmp_path / "_mix_subs.vtt").exists()


class TestSubtitles:
    def test_vtt_from_real_duration(self):
        c = _contract(dialogue=[{"character_id": "char_a", "line": "钱什么时候还"}],
                      mouth=MOUTH_SPEAKING_LIPSYNC, gender_map={"char_a": "female"})
        c["voice_contract"]["bindings"][0]["duration_sec"] = 2.5
        vtt, cues = build_dialogue_vtt([c])
        assert "WEBVTT" in vtt and len(cues) == 1
        assert cues[0]["text"] == "钱什么时候还"
        assert abs(cues[0]["end"] - cues[0]["start"] - 2.5) < 0.01   # 真实时长驱动

    def test_estimate_duration_scales_with_length(self):
        assert estimate_duration("短") < estimate_duration("这是一句比较长的台词内容")
