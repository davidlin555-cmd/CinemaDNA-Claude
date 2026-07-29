"""镜头功能表达检查测试 —— 动作镜近乎静止判未表达（注入假 VisionJudge）。"""

from __future__ import annotations

from pathlib import Path

from final_review.function_expression import FunctionExpressionService


class FakeVJ:
    def __init__(self, motion_by_shot):
        self.m = motion_by_shot
    def judge(self, video):
        motion = self.m.get(Path(video).stem, 1.0)
        class R:
            def to_dict(self):
                return {"motion": motion}
        return R()


def _items(rows, tmp_path):
    out = []
    for sid, fn in rows:
        p = tmp_path / f"{sid}.mp4"; p.write_bytes(b"\x00" * 100)
        out.append({"shot_id": sid, "video": p, "story_function": fn})
    return out


def test_static_action_shot_flagged(tmp_path):
    vj = FakeVJ({"S1": 0.02, "S2": 0.5})     # S1 施压镜几乎静止
    svc = FunctionExpressionService(vision_judge=vj)
    r = svc.review(_items([("S1", "施压"), ("S2", "反转")], tmp_path))
    assert not r.passed and r.flagged[0]["shot_id"] == "S1"


def test_non_action_shots_not_checked(tmp_path):
    vj = FakeVJ({"S1": 0.0})                  # 揭示镜(信息)不查运动
    svc = FunctionExpressionService(vision_judge=vj)
    r = svc.review(_items([("S1", "揭示"), ("S2", "反应")], tmp_path))
    assert r.passed and r.checked == 0


def test_moving_action_shots_pass(tmp_path):
    vj = FakeVJ({"S1": 0.6, "S2": 0.4})
    svc = FunctionExpressionService(vision_judge=vj)
    r = svc.review(_items([("S1", "反击"), ("S2", "钩子")], tmp_path))
    assert r.passed and r.checked == 2
