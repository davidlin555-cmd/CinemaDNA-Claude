"""AudioDNA E4 测试 —— 免费中文 Edge TTS / 角色声音登记表（克隆就绪）/ 环境音库

Edge 后端注入假 synth（不联网、不产声）；登记表验证克隆音色可插入。
"""

from __future__ import annotations

from audio import voices
from audio.mixer import AMBIENCE_LIBRARY
from audio.registry import CharacterVoiceRegistry
from audio.tts_edge import EdgeTTSBackend, edge_voice_for


class TestEdgeBackend:
    def test_voice_id_maps_to_chinese_neural(self):
        fem = voices.VOICE_POOLS["female"][0]["voice_id"]
        male = voices.VOICE_POOLS["male"][0]["voice_id"]
        assert edge_voice_for(fem).startswith("zh-CN") and "Xiao" in edge_voice_for(fem)
        assert edge_voice_for(male).startswith("zh-CN") and "Yun" in edge_voice_for(male)

    def test_synthesize_with_fake_synth(self, tmp_path):
        seen = {}

        def fake_synth(text, voice, rate, dest):
            seen.update(text=text, voice=voice, rate=rate)
            dest.write_bytes(b"ID3" + b"\x00" * 400)

        be = EdgeTTSBackend(synth=fake_synth)
        assert be.ext == ".mp3"
        r = be.synthesize(text="再给我两天", dest=tmp_path / "a.mp3",
                          voice_id=voices.VOICE_POOLS["female"][1]["voice_id"],
                          item_id="s1", settings={"rate": 1.2})
        assert (tmp_path / "a.mp3").exists() and r.is_real_media
        assert seen["voice"].startswith("zh-CN")
        assert seen["rate"] == "+20%"          # 情绪韵律 → 语速


class TestVoiceRegistry:
    def test_default_uses_pool(self):
        reg = CharacterVoiceRegistry()
        v = reg.resolve("char_a", "female")
        assert v["voice_id"] in {x["voice_id"] for x in voices.VOICE_POOLS["female"]}
        assert v["cloned"] is False

    def test_pin_cloned_voice(self):
        reg = CharacterVoiceRegistry()
        reg.pin("char_hero", voice_id="cloned_xyz123", voice_name="主角克隆音",
                gender="female", cloned=True)
        b = {"speaker": "char_hero", "voice_gender": "female"}
        reg.apply(b)
        assert b["voice_id"] == "cloned_xyz123"      # 用克隆音色
        assert b["voice_cloned"] is True

    def test_stable_across_shots(self):
        reg = CharacterVoiceRegistry()
        a = reg.resolve("char_x", "male")["voice_id"]
        b = reg.resolve("char_x", "male")["voice_id"]
        assert a == b

    def test_roundtrip_serialize(self):
        reg = CharacterVoiceRegistry()
        reg.pin("c1", voice_id="v1", cloned=True)
        reg2 = CharacterVoiceRegistry.from_dict(reg.to_dict())
        assert reg2.resolve("c1", "female")["voice_id"] == "v1"


class TestAmbienceLibrary:
    def test_presets_exist(self):
        assert {"room", "rain", "street", "night"} <= set(AMBIENCE_LIBRARY)

    def test_preset_formats_with_duration(self):
        f = AMBIENCE_LIBRARY["rain"].format(d=8.0)
        assert "duration=8.0" in f and "anoisesrc" in f
