"""成片观感 (Phase H) 测试 —— 表演导演/对白语气 + ShowQualityDNA + 自动打回。"""

from __future__ import annotations

from audio import voices
from orchestrator.pipeline import PipelineOrchestrator
from performance.direction import (
    TONE_PLEAD,
    TONE_PRESS,
    assign_tone,
    direct_performance,
)
from showquality.service import review_show_quality


def _c(shot_id, order, shot_type, dur, intensity, beat="SETUP",
       dialogue=None, beats=None):
    return {"shot_id": shot_id, "order": order, "shot_type": shot_type,
            "duration_sec": dur, "emotion": {"intensity": intensity, "beat": beat},
            "dialogue": dialogue or [],
            "performance": {"acting_beats": beats or []}}


# ---------------------------------------------------------------------------
# 表演导演 + 对白语气 Agent
# ---------------------------------------------------------------------------


class TestDialogueTone:
    def test_pressure_tone(self):
        t = assign_tone("今晚十二点前必须到账", {"intensity": 0.85}, [])
        assert t["tone"] == TONE_PRESS
        assert t["pause_before"] is True          # 关键台词前停顿
        assert 0 < t["prosody"]["rate"] and t["prosody"]["stability"] < 0.5

    def test_plead_tone(self):
        t = assign_tone("再给我两天，工资一发我立刻还", {"intensity": 0.6}, [])
        assert t["tone"] == TONE_PLEAD
        assert t["prosody"]["rate"] > 1.0          # 哀求急促

    def test_calm_low_intensity(self):
        assert assign_tone("嗯。", {"intensity": 0.2}, [])["tone"] == "克制"


class TestPerformanceDirector:
    def test_directs_tone_pause_prosody(self):
        c = _c("SH1", 0, "CLOSEUP", 5.0, 0.9, beat="CONFLICT",
               dialogue=[{"character_id": "a", "line": "别想赖账"}],
               beats=[{"beat_index": 0, "type": "LINE", "character_id": "a",
                       "cue": "别想赖账", "micro_expression": {"primary": "x"}}])
        c["voice_contract"] = {"bindings": [{"speaker": "a", "voice_gender": "male",
                                             "line": "别想赖账"}]}
        direct_performance([c], {"a": {"personality_keywords": []}})
        beats = c["performance"]["acting_beats"]
        line = [b for b in beats if b["type"] == "LINE"][0]
        assert line.get("tone") and line.get("prosody")     # 挂了语气+韵律
        assert any(b["type"] == "PAUSE" for b in beats)      # 关键台词前插停顿
        # 语气 → 声音绑定（声画一致，喂 AudioDNA）
        assert c["voice_contract"]["bindings"][0].get("prosody")

    def test_audio_uses_director_prosody(self):
        # AudioDNA 的 voice_settings 优先采用表演给的 prosody
        s = voices.voice_settings_for({"intensity": 0.5},
                                      {"rate": 1.2, "stability": 0.2, "style": 0.85})
        assert s["rate"] == 1.2 and s["stability"] == 0.2


# ---------------------------------------------------------------------------
# ShowQualityDNA
# ---------------------------------------------------------------------------


def _good_story():
    beats = [{"type": "LINE", "character_id": "a", "cue": "还钱",
              "tone": "施压", "micro_expression": {"primary": "冷", "physiological": "呼吸"}},
             {"type": "REACTION", "character_id": "b", "cue": "听",
              "micro_expression": {"primary": "怕", "physiological": "吞咽"}}]
    return [
        _c("SH1", 0, "ESTABLISHING", 3.0, 0.5, "CONFLICT",
           dialogue=[{"character_id": "a", "line": "还钱"}], beats=beats),
        _c("SH2", 1, "MEDIUM", 5.0, 0.7, "CONFLICT", beats=beats),
        _c("SH3", 2, "CLOSEUP", 4.0, 0.92, "CRISIS", beats=beats),
        _c("SH4", 3, "REACTION", 3.5, 0.6, "SETUP", beats=beats),
        _c("SH5", 4, "INSERT", 3.0, 0.4, "SETUP", beats=beats),
    ]


class TestShowQuality:
    def test_good_story_passes(self):
        r = review_show_quality(_good_story(), story_id="d1")
        assert r.passed and r.score >= 0.8

    def test_no_variety_flagged(self):
        mono = [_c(f"SH{i}", i, "MEDIUM", 5.0, 0.5, "SETUP") for i in range(5)]
        r = review_show_quality(mono, story_id="d1")
        assert not r.passed
        codes = {i["code"] for i in r.issues}
        assert "NO_SHOT_VARIETY" in codes and "MONOTONE_DURATION" in codes
        assert "FLAT_EMOTION" in codes
        assert r.target_stage == "DIRECTING"      # 精准打回导演
        assert all(i.get("fix") for i in r.issues)  # 每条都有可执行建议

    def test_weak_hook_flagged(self):
        # 开场全是低情绪空镜、无台词 → 无钩子
        flat = [_c(f"SH{i}", i,
                   ["ESTABLISHING", "MEDIUM", "CLOSEUP", "INSERT"][i % 4],
                   [2.0, 4.0, 6.0, 3.0][i % 4], 0.3, "SETUP") for i in range(5)]
        r = review_show_quality(flat, story_id="d1")
        assert any(i["code"] == "WEAK_HOOK" for i in r.issues)

    def test_dragging_flagged(self):
        drag = [_c(f"SH{i}", i, ["MEDIUM", "CLOSEUP"][i % 2], 12.0,
                   [0.4, 0.8][i % 2], "CONFLICT",
                   dialogue=[{"character_id": "a", "line": "x"}]) for i in range(4)]
        r = review_show_quality(drag, story_id="d1")
        assert any(i["code"] == "DRAGGING" for i in r.issues)

    def test_no_reaction_flagged(self):
        talk = [_c(f"SH{i}", i, "MEDIUM", 5.0, 0.6, "CONFLICT",
                   dialogue=[{"character_id": "a", "line": "还钱"}],
                   beats=[{"type": "LINE", "character_id": "a", "cue": "还钱",
                           "tone": "施压", "micro_expression": {"primary": "x"}}])
                for i in range(4)]
        # 变化景别避免其它命中，只留"无反应"
        for i, c in enumerate(talk):
            c["shot_type"] = ["MEDIUM", "CLOSEUP", "MEDIUM", "CLOSEUP"][i]
            c["duration_sec"] = [4.0, 5.0, 3.5, 6.0][i]
            c["emotion"]["intensity"] = [0.5, 0.7, 0.9, 0.6][i]
        r = review_show_quality(talk, story_id="d1")
        assert any(i["code"] == "NO_REACTION" for i in r.issues)
        assert r.target_stage == "PERFORMANCE"


# ---------------------------------------------------------------------------
# 编排：自动打回，不停机等人
# ---------------------------------------------------------------------------


class TestOrchestratorIntegration:
    def test_monotone_triggers_auto_repair(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        # 注入单调合约 + 推进到有合约的运行态
        orc._contracts[s.story_id] = [
            _c(f"SH{i}", i, "MEDIUM", 5.0, 0.5, "SETUP") for i in range(5)]
        out = orc.run_show_quality(s.story_id)
        assert out["verdict"] == "REPAIR"
        story = orc.get_story(s.story_id)
        assert story["status"] == "AUTO_REPAIRING"          # 自动修复，不停机
        assert story["repair_directive"]["code"] == "DIRECTOR_FIX"
        assert orc.showquality_for(s.story_id) is not None

    def test_good_story_passes_through(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="B")
        orc._contracts[s.story_id] = _good_story()
        out = orc.run_show_quality(s.story_id)
        assert out["verdict"] == "PASS"
        assert orc.get_story(s.story_id)["status"] == "RUNNING"
