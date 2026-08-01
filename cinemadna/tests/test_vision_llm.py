"""LLM 视觉深判测试 —— 判读解析 + 接进 VisionJudge（0 成本，假 client）。"""

from __future__ import annotations

from pathlib import Path

from final_review.vision_llm import AnthropicVisionJudge, parse_vision_verdict


class _Blk:
    def __init__(self, text):
        self.text = text


class _Msg:
    def __init__(self, text):
        self.content = [_Blk(text)]


class _FakeMessages:
    def __init__(self, text):
        self._text = text
    def create(self, **kw):
        return _Msg(self._text)


class _FakeClient:
    def __init__(self, text):
        self.messages = _FakeMessages(text)


class TestParse:
    def test_parses_clean_json(self):
        d = parse_vision_verdict('{"ai_fake": true, "face_swap": false, "reason": "塑料感"}')
        assert d["ai_fake"] is True and d["face_swap"] is False
        assert d["reason"] == "塑料感" and not d.get("parse_error")

    def test_strips_markdown_fence(self):
        d = parse_vision_verdict('```json\n{"garbled_text": true, "reason": "招牌乱码"}\n```')
        assert d["garbled_text"] is True

    def test_unparseable_flags_error_not_pass(self):
        d = parse_vision_verdict("模型今天不想输出 JSON")
        assert d["parse_error"] is True                 # 不假装通过
        assert d["ai_fake"] is False


class TestJudgeFrame:
    def test_judge_frame_with_fake_client(self, tmp_path):
        frame = tmp_path / "f.jpg"
        frame.write_bytes(b"\xff\xd8\xff\x00fakejpg")
        judge = AnthropicVisionJudge(
            client=_FakeClient('{"face_swap": true, "reason": "同框两张脸"}'))
        d = judge.judge_frame(frame)
        assert d["face_swap"] is True and d["reason"] == "同框两张脸"


class TestWiredIntoVisionJudge:
    def test_deep_flags_reach_verdict(self, tmp_path):
        # 深判命中 face_swap → VisionJudge 应判 fake（含变脸杀手）
        from final_review.vision_judge import VisionJudge
        video = tmp_path / "v.mp4"
        video.write_bytes(b"x")

        class FakeDeep:
            def judge_frame(self, frame):
                return {"face_swap": True, "reason": "换脸"}

        # runner 造出"有运动、有动态范围"的像素输出，避免被静帧/纯色先判坏
        def runner(argv):
            return "YAVG=40.0\nYMAX=200.0\nYMIN=10.0\n"

        vj = VisionJudge(runner=runner, llm_backend=FakeDeep())
        # judge 内部会尝试抽帧（runner 被替换，抽帧命令返回同串，不报错）
        res = vj.judge(video)
        assert res.deep_judged and res.fake
        assert any("深判" in r for r in res.reasons)
