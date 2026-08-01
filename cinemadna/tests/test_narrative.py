"""叙事层测试 —— NarrativeContinuityQA 硬门 + LLM 作者化（假 client，不联网）。"""

from __future__ import annotations

import json

from narrative.continuity_qa import (
    NarrativeContinuityQAService,
    VERDICT_PASS,
    VERDICT_REPAIR,
)
from scriptbrain.llm_author import LLMNarrativeAuthor, NarrativeAuthorError


def _shot(sid, order, *, chars, lines, dur=5.0, stype="CLOSEUP", tone="压力"):
    return {
        "shot_id": sid, "scene_id": sid.rsplit("_", 1)[0], "order": order,
        "shot_type": stype, "duration_sec": dur, "story_function": "推进对白",
        "emotion": {"beat": "CONFLICT", "mood": tone},
        "assets": {"characters": [{"character_id": c} for c in chars], "props": []},
        "characters": chars,
        "narrative": {"cause": "上一场逼债", "effect": "她被逼到墙角",
                      "entry_state": "疲惫", "exit_state": "崩溃", "emotional_tone": tone},
        "dialogue": lines,
    }


class TestNarrativeQA:
    def test_clean_passes(self):
        cs = [_shot("S1_SH1", 1, chars=["a"], lines=[{"character_id": "a", "line": "钱我会还", "emotion": "疲惫"}]),
              _shot("S1_SH2", 2, chars=["a", "b"], lines=[{"character_id": "b", "line": "今晚就要", "emotion": "威胁"}])]
        r = NarrativeContinuityQAService().review(cs, story_id="s")
        assert r.verdict == VERDICT_PASS and not r.issues

    def test_wrong_attribution_blocked(self):
        cs = [_shot("S1_SH1", 1, chars=["a"], lines=[{"character_id": "zzz", "line": "台词", "emotion": "疲惫"}])]
        r = NarrativeContinuityQAService().review(cs, story_id="s")
        assert r.verdict == VERDICT_REPAIR
        assert any(i["category"] == "attribution" for i in r.issues)

    def test_repeated_line_blocked(self):
        cs = [_shot("S1_SH1", 1, chars=["a"], lines=[{"character_id": "a", "line": "还钱", "emotion": "疲惫"}]),
              _shot("S1_SH2", 2, chars=["a"], lines=[{"character_id": "a", "line": "还钱", "emotion": "疲惫"}])]
        r = NarrativeContinuityQAService().review(cs, story_id="s")
        assert any(i["category"] == "repeated_line" for i in r.issues)

    def test_emotion_conflict_blocked(self):
        # 压力场景里出现"开心"情绪台词
        cs = [_shot("S1_SH1", 1, chars=["a"], tone="压力",
                    lines=[{"character_id": "a", "line": "太好了", "emotion": "开心"}])]
        r = NarrativeContinuityQAService().review(cs, story_id="s")
        assert any(i["category"] == "emotion_conflict" for i in r.issues)

    def test_static_padding_blocked(self):
        cs = [_shot("S1_SH1", 1, chars=["a"], lines=[], dur=8.0, stype="MEDIUM")]
        r = NarrativeContinuityQAService().review(cs, story_id="s")
        assert any(i["category"] == "static_padding" for i in r.issues)

    def test_missing_narrative_field_blocked(self):
        c = _shot("S1_SH1", 1, chars=["a"], lines=[{"character_id": "a", "line": "x", "emotion": "疲惫"}])
        c["narrative"]["cause"] = ""
        r = NarrativeContinuityQAService().review([c], story_id="s")
        assert any(i["category"] == "completeness" for i in r.issues)


class TestLLMAuthor:
    def _episodes(self):
        return [{"episode_id": "EP1", "scenes": [
            {"scene_id": "EP1_SC1", "beat_type": "SETUP", "beat_summary": "逼债",
             "mood": "压力", "characters": ["linwan", "collector"]},
        ]}]

    def _beats(self, ghost=False):
        cid = "GHOST" if ghost else "collector"
        return [
            {"type": "setup", "seconds": 3, "description": "深夜下班", "character_id": None, "line": "", "emotion": "疲惫"},
            {"type": "info", "seconds": 3, "description": "催债短信", "character_id": "linwan", "line": "十万块", "emotion": "绝望"},
            {"type": "interruption", "seconds": 3, "description": "被拦", "character_id": cid, "line": "今晚就还", "emotion": "威胁"},
            {"type": "obstruction", "seconds": 3, "description": "拿不出", "character_id": "linwan", "line": "我没有钱", "emotion": "无助"},
            {"type": "reaction", "seconds": 2, "description": "冷笑", "character_id": None, "line": "", "emotion": "轻蔑"},
            {"type": "power_shift", "seconds": 3, "description": "掏协议", "character_id": "collector", "line": "签了它", "emotion": "压迫"},
            {"type": "counterattack", "seconds": 3, "description": "撕协议", "character_id": "linwan", "line": "想都别想", "emotion": "决绝"},
            {"type": "cliffhanger", "seconds": 3, "description": "亮证据", "character_id": "linwan", "line": "这次换我", "emotion": "冷冽"},
        ]

    def _json(self, ghost=False):
        return json.dumps({"scenes": [{
            "scene_id": "EP1_SC1", "cause": "欠债", "effect": "被逼",
            "entry_state": "疲惫", "exit_state": "绝望", "emotional_tone": "压力",
            "beats": self._beats(ghost=ghost)}]})

    def test_authors_dense_beatsheet(self):
        author = LLMNarrativeAuthor(call=lambda sys, usr: self._json())
        out = author.author_episodes(self._episodes(), {"characters": []},
                                     theme="催债", characters=[])
        sc = out[0]["scenes"][0]
        assert sc["narrative"]["authored_by"] == "llm"
        assert len(sc["beats"]) == 8                       # 高密度节拍
        types = {b["type"] for b in sc["beats"]}
        assert {"interruption", "obstruction", "counterattack"} <= types  # 结构齐全
        assert sc["beats"][-1]["type"] == "cliffhanger"    # 结尾悬念
        assert all(2.0 <= b["seconds"] <= 4.0 for b in sc["beats"])  # 每拍 2-4s

    def test_rejects_bad_attribution(self):
        author = LLMNarrativeAuthor(call=lambda sys, usr: self._json(ghost=True))
        try:
            author.author_episodes(self._episodes(), {"characters": []},
                                   theme="催债", characters=[])
            assert False, "应因属主非法抛错"
        except NarrativeAuthorError as e:
            assert "属主" in str(e) or "GHOST" in str(e)

    def test_available_with_injected_call(self):
        assert LLMNarrativeAuthor(call=lambda s, u: "").available() is True


class TestDensityQA:
    def _shot(self, sid, order, btype, dur):
        return {"shot_id": sid, "order": order, "shot_type": "MEDIUM",
                "duration_sec": dur, "beat_type": btype,
                "narrative": {}, "dialogue": []}

    def test_dense_beatsheet_passes(self):
        from narrative.density import DensityQAService, VERDICT_PASS
        types = ["setup", "info", "interruption", "obstruction", "reaction",
                 "power_shift", "counterattack", "info", "cliffhanger"]
        cs = [self._shot(f"S_SH{i}", i, t, 3.0) for i, t in enumerate(types, 1)]
        r = DensityQAService().review(cs, story_id="s")
        assert r.verdict == VERDICT_PASS, r.issues

    def test_too_few_shots_and_missing_structure_blocked(self):
        from narrative.density import DensityQAService, VERDICT_REPAIR
        cs = [self._shot("S_SH1", 1, "setup", 8.0),
              self._shot("S_SH2", 2, "info", 8.0)]
        r = DensityQAService().review(cs, story_id="s")
        assert r.verdict == VERDICT_REPAIR
        cats = {i["category"] for i in r.issues}
        assert "shot_count" in cats and "structural_beat" in cats
        assert "ending" in cats and "long_shot" in cats


class TestEditingRhythm:
    def test_rhythm_accelerates_and_holds_cliffhanger(self):
        from postbrain.rhythm import apply_editing_rhythm, MIN_SHOT, MAX_SHOT
        cs = [{"shot_id": f"S{i}", "order": i, "beat_type": bt, "duration_sec": 3.5}
              for i, bt in enumerate(
                  ["setup", "info", "interruption", "reaction", "counterattack",
                   "power_shift", "cliffhanger"], 1)]
        out = apply_editing_rhythm(cs)
        durs = {c["shot_id"]: c["duration_sec"] for c in out}
        assert all(MIN_SHOT <= d <= MAX_SHOT for d in durs.values())
        # 打断/反应镜比结尾悬念短（节奏：快切→悬停）
        assert durs["S3"] < durs["S7"] and durs["S4"] < durs["S7"]


class TestCommercialCouncil:
    def _script(self):
        return {"title": "催债", "logline": "护士逆袭", "episodes": [{"scenes": [
            {"beats": [{"type": "setup", "seconds": 3, "description": "深夜",
                        "line": "", "emotion": "疲惫"},
                       {"type": "cliffhanger", "seconds": 3, "description": "警笛",
                        "line": "游戏结束了", "emotion": "胜利"}]}]}]}

    def test_pass_when_high_score(self):
        from council.commercial_review import CommercialCouncil
        good = '{"score":0.8,"verdict":"PASS","scores":{"hook":0.8},"notes":["ok"]}'
        c = CommercialCouncil(call=lambda s, u: good)
        r = c.review(self._script())
        assert r.passed and r.score == 0.8

    def test_revise_when_low_score(self):
        from council.commercial_review import CommercialCouncil
        bad = '{"score":0.4,"verdict":"PASS","scores":{},"notes":["钩子太弱","加快节奏"]}'
        c = CommercialCouncil(call=lambda s, u: bad)   # 分低于门槛→即使 verdict PASS 也判 REVISE
        r = c.review(self._script())
        assert not r.passed and r.target_stage == "SCRIPT_DONE"
        assert "钩子太弱" in r.notes
