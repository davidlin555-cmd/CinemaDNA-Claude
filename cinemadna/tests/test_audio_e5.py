"""AudioDNA E5 测试 —— 内置 BGM 曲库 / 分场景 LUFS / 声纹克隆接口 / 唇形对齐插槽。"""

from __future__ import annotations

from pathlib import Path

from audio import bgm
from audio.clone import ElevenLabsVoiceCloner
from audio.lipsync import LipSyncBackend, PassthroughLipSync
from audio.mixer import AudioMixer, DialogueClip
from audio.registry import CharacterVoiceRegistry


class TestBGMLibrary:
    def test_moods_have_beds(self):
        for mood in bgm.BGM_MOODS:
            frag = bgm.bed_for(mood, 8.0)
            assert frag and frag[-1].endswith("[bgm]")
            assert "duration=8.0" in "".join(frag) or "d=8.0" in "".join(frag)

    def test_beat_maps_to_mood(self):
        assert bgm.mood_for_emotion({"beat": "CONFLICT"}) == bgm.MOOD_TENSION
        assert bgm.mood_for_emotion({"beat": "VICTORY"}) == bgm.MOOD_TRIUMPH
        assert bgm.mood_for_emotion({"beat": "LOSS"}) == bgm.MOOD_SAD
        # 无 beat → 看强度
        assert bgm.mood_for_emotion({"intensity": 0.9}) == bgm.MOOD_TENSION
        assert bgm.mood_for_emotion({"intensity": 0.3}) == bgm.MOOD_NEUTRAL

    def test_mixer_uses_mood_bgm(self, tmp_path):
        mx = AudioMixer(runner=lambda a, c: None)
        argv = mx.build_argv(
            video=tmp_path / "v.mp4",
            clips=[DialogueClip(tmp_path / "d.wav", 0.0)],
            duration=8.0, subtitles_name=None, bgm=True, ambience="room",
            bgm_input_index=None, out_name="o.mp4", bgm_mood="triumph",
            target_lufs=-14)
        fc = argv[argv.index("-filter_complex") + 1]
        assert "277.18" in fc            # 逆袭=A 大三和弦特征音
        assert "loudnorm=I=-14" in fc    # 分场景响度目标

    def test_layered_ambience(self, tmp_path):
        mx = AudioMixer(runner=lambda a, c: None)
        argv = mx.build_argv(
            video=tmp_path / "v.mp4", clips=[], duration=6.0,
            subtitles_name=None, bgm=False, ambience=["rain", "street"],
            bgm_input_index=None, out_name="o.mp4")
        fc = argv[argv.index("-filter_complex") + 1]
        assert "[amb0]" in fc and "[amb1]" in fc   # 两层环境音叠加


class TestVoiceClone:
    def test_clone_pins_into_registry(self, tmp_path):
        sample = tmp_path / "ref.mp3"
        sample.write_bytes(b"ID3" + b"\x00" * 500)
        reg = CharacterVoiceRegistry()

        def fake_uploader(name, samples, key):
            assert samples and key
            return "cloned_ABC123"

        cloner = ElevenLabsVoiceCloner(api_key="k", uploader=fake_uploader)
        res = cloner.clone_for(reg, character_id="char_hero", name="主角",
                               samples=[sample], gender="female")
        assert res["ok"] and res["voice_id"] == "cloned_ABC123"
        b = {"speaker": "char_hero", "voice_gender": "female"}
        reg.apply(b)
        assert b["voice_id"] == "cloned_ABC123" and b["voice_cloned"] is True

    def test_clone_missing_sample(self, tmp_path):
        cloner = ElevenLabsVoiceCloner(api_key="k", uploader=lambda *a: "x")
        res = cloner.clone_for(CharacterVoiceRegistry(), character_id="c",
                               name="n", samples=[tmp_path / "nope.mp3"])
        assert res["ok"] is False


class TestLipSync:
    def test_passthrough_is_honest(self, tmp_path):
        be = PassthroughLipSync()
        assert isinstance(be, LipSyncBackend)      # 满足协议
        v = tmp_path / "face.mp4"
        v.write_bytes(b"\x00" * 1000)
        res = be.sync(face_video=v, audio=tmp_path / "a.mp3",
                      dest=tmp_path / "out.mp4")
        assert res["ok"] and res["lip_synced"] is False   # 诚实：未真正对齐
        assert (tmp_path / "out.mp4").exists()
