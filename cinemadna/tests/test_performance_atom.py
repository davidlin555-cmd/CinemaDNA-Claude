"""PerformanceAtomDNA 测试 —— 检索/组合/硬门/回流/渲染提示（0 成本）。"""

from __future__ import annotations

from performance_atom.service import PerformanceAtomDNAService, AtomLibrary


def _intent(**kw):
    base = {"shot_id": "S1", "story_function": "揭示", "emotion_target": "压抑→愤怒",
            "intensity": "medium → high", "exaggerate": False,
            "body_focus": "hands", "face_focus": "eyes + jaw",
            "beats": ["接收信息", "情绪转入", "态度外显"],
            "constraints": ["禁止无触发笑", "必须服务账单信息"]}
    base.update(kw)
    return base


class TestComposeAndGate:
    def test_compose_serves_story_function(self):
        svc = PerformanceAtomDNAService()
        r = svc.compose(_intent(story_function="揭示"), target_sec=3.5)
        assert r.passed, r.gate.report()
        # 揭示镜必须有"掏出/撕/看/攥皱"类功能动作
        tags = [a["atom"]["tag"] for a in r.sequence.action_track]
        assert any(any(k in t for k in ("掏出", "撕", "看", "攥皱")) for t in tags)
        assert r.sequence.expression_track and r.sequence.action_track

    def test_render_hint_is_chinese_structured(self):
        svc = PerformanceAtomDNAService()
        r = svc.compose(_intent(), target_sec=3.0)
        hint = r.sequence.render_hint()
        assert "动作：" in hint and "表情：" in hint

    def test_exaggerate_only_bumps_intensity(self):
        svc = PerformanceAtomDNAService()
        r = svc.compose(_intent(exaggerate=True), target_sec=3.0)
        assert r.sequence.exaggerated
        assert all(0 < e["atom"]["intensity"] <= 1.0
                   for e in r.sequence.expression_track)


class TestSpeakingBeats:
    def test_speaking_shot_gets_beats_not_final_lip(self):
        # 第2阶段·表演规划：说话镜产出加重/停顿节拍，且诚实标注非逐帧口型
        svc = PerformanceAtomDNAService()
        r = svc.compose(_intent(is_speaking=True, line="今晚十二点前一分不能少"),
                        target_sec=4.0)
        types = {b["type"] for b in r.sequence.speaking_beats}
        assert "emphasis" in types and "pause" in types
        assert r.sequence.is_final_lip_frames is False

    def test_reversal_line_bursts(self):
        svc = PerformanceAtomDNAService()
        r = svc.compose(_intent(story_function="反转", is_speaking=True,
                                intensity=0.9, line="这钱我不还了"), target_sec=3.5)
        assert any(b["type"] == "burst" for b in r.sequence.speaking_beats)

    def test_silent_shot_has_no_speaking_beats(self):
        svc = PerformanceAtomDNAService()
        r = svc.compose(_intent(is_speaking=False, line=""), target_sec=3.0)
        assert r.sequence.speaking_beats == []

    def test_beats_amplify_prosody(self):
        # 说话节拍折进 TTS 韵律（表演语气→真实语音）
        from performance.direction import amplify_prosody_by_beats
        base = {"rate": 1.0, "stability": 0.4, "style": 0.5}
        burst = [{"t": 1.0, "type": "burst"}]
        p, pause = amplify_prosody_by_beats(base, burst)
        assert p["rate"] > base["rate"] and p["style"] > base["style"]
        assert p["stability"] < base["stability"]
        _, pause2 = amplify_prosody_by_beats(base, [{"type": "pause"}])
        assert pause2 is True and pause is False

    def test_backflow_stores_passed_sequence(self):
        lib = AtomLibrary()
        svc = PerformanceAtomDNAService(library=lib)
        r = svc.compose(_intent(story_function="反转"), target_sec=3.0)
        assert r.passed
        assert lib.composed_sequences.get("反转")            # 回流入库
        assert lib.usage_stats["反转"]["passed"] == 1
        assert lib.hot_sequence("反转") is not None           # 成熟成功链可复用


class TestGateHardChecks:
    def test_padding_repeated_atom_flagged(self):
        # 构造一个原子重复凑秒的序列 → 硬门拦
        from performance_atom.atoms import ComposedSequence
        from performance_atom.service import AtomGate
        seq = ComposedSequence(shot_id="S1", story_function="反应", total_sec=6,
            action_track=[{"t": i, "beat": "x", "atom": {"tag": "后退半步", "intensity": 0.5}}
                          for i in range(3)],
            atoms_used=["act_004"] * 3)
        r = AtomGate().review(seq, {"emotion_target": "无助"})
        assert not r.passed and any(i["category"] == "padding" for i in r.issues)

    def test_missing_function_action_flagged(self):
        from performance_atom.atoms import ComposedSequence
        from performance_atom.service import AtomGate
        # 揭示镜却没有任何掏出/撕/看动作
        seq = ComposedSequence(shot_id="S1", story_function="揭示", total_sec=3,
            action_track=[{"t": 0, "beat": "x", "atom": {"tag": "后退半步", "intensity": 0.5}}],
            atoms_used=["act_004"])
        r = AtomGate().review(seq, {"emotion_target": "绝望"})
        assert not r.passed and any(i["category"] == "serves_function" for i in r.issues)
