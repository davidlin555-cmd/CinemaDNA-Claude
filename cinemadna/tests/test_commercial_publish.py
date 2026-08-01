"""商业可发布稳定性 (Phase I) 测试 —— 黑屏缺帧探测 / 商业清单 / PASS_FULL / 发布闸。"""

from __future__ import annotations

import pytest

from final_review.checklist import PASS_FULL_CHECKLIST, evaluate_checklist
from final_review.media_probe import MediaProbe
from final_review.service import FinalReviewService
from orchestrator.pipeline import OrchestratorError, PipelineOrchestrator
from orchestrator.story import StoryStage


# ---------------------------------------------------------------------------
# 黑屏/缺帧探测（注入假 ffmpeg 输出）
# ---------------------------------------------------------------------------


class TestMediaProbe:
    def _probe(self, det_text, frames, fps_num=24, dur=6.0):
        def runner(argv):
            if "-count_frames" in argv:
                return f"nb_read_frames={frames}\nr_frame_rate={fps_num}/1\nduration={dur}\n"
            return det_text
        return MediaProbe(runner=runner).probe

    def test_clean_video_ok(self, tmp_path):
        r = self._probe("", 144)(tmp_path / "v.mp4")
        assert r.ok and not r.has_black and not r.has_freeze and r.dropped_frames == 0

    def test_black_and_freeze_flagged(self, tmp_path):
        det = "black_start:0 black_end:5 black_duration:5\nlavfi.freezedetect.freeze_start: 0\n"
        r = self._probe(det, 144)(tmp_path / "v.mp4")
        assert not r.ok and r.has_black and r.has_freeze

    def test_dropped_frames_flagged(self, tmp_path):
        r = self._probe("", 100)(tmp_path / "v.mp4")   # 期望 144，实收 100
        assert not r.ok and r.dropped_frames >= 40


# ---------------------------------------------------------------------------
# 商业可发布清单：5 项真实检查
# ---------------------------------------------------------------------------


def _good_contract(order, shot_type, beat, dur=9.0, dialogue=True):
    rr = {"is_real_media": True, "is_generated_footage": True,
          "resolution": "768x1024", "fps": 24,
          "metrics": {"identity_consistency": 0.9}}
    c = {"shot_id": f"SH{order}", "order": order, "shot_type": shot_type,
         "duration_sec": dur, "emotion": {"beat": beat, "intensity": 0.7},
         "assets": {"scene": {}, "characters": [{"character_id": "a"}], "props": []},
         "render_result": rr, "mouth_policy": "SPEAKING_LIPSYNC" if dialogue else "SILENT_CLOSED",
         "has_real_audio": True}
    if dialogue:
        c["dialogue"] = [{"character_id": "a", "line": "还钱"}]
        c["voice_contract"] = {"bindings": [{"speaker": "a", "voice_gender": "female",
                                             "line": "还钱", "audio_file": "x.mp3"}]}
        c["audio_mix"] = {"dialogue_gain_db": 0.0, "bgm_gain_db": -18.0, "ducking": True}
    else:
        c["dialogue"] = []
        c["voice_contract"] = {"bindings": []}
    return c


def _good_ctx():
    contracts = [
        _good_contract(1, "ESTABLISHING", "HOOK"),
        _good_contract(2, "MEDIUM", "CONFLICT"),
        _good_contract(3, "CLOSEUP", "CRISIS"),
        _good_contract(4, "REACTION", "SETUP", dialogue=False),
        _good_contract(5, "INSERT", "CLIFFHANGER", dialogue=False),
    ]
    return {
        "contracts": contracts, "shooting_script": {},
        "continuity_report": {}, "consistency_report": {},
        "shot_qa": {}, "rough_cut": {"total_duration_sec": 45, "missing_shots": []},
        "media_probe": {"ok": True, "issues": []},
        "safety_violations": [],
    }


class TestChecklist:
    def test_all_pass(self):
        checks = evaluate_checklist(_good_ctx())
        assert {c["name"] for c in checks} == {n for n, _ in PASS_FULL_CHECKLIST}
        assert all(c["ok"] for c in checks), [c for c in checks if not c["ok"]]

    def test_black_frames_fail(self):
        ctx = _good_ctx()
        ctx["media_probe"] = {"ok": False, "issues": ["检出 1 段黑屏"]}
        checks = {c["name"]: c for c in evaluate_checklist(ctx)}
        assert checks["no_black_or_frame_drop"]["ok"] is False

    def test_placeholder_is_fake(self):
        ctx = _good_ctx()
        for c in ctx["contracts"]:
            c["render_result"]["is_generated_footage"] = False   # 占位色板
        checks = {c["name"]: c for c in evaluate_checklist(ctx)}
        assert checks["no_obvious_fake"]["ok"] is False

    def test_no_audio_breaks_lipsync(self):
        ctx = _good_ctx()
        for c in ctx["contracts"]:
            c["has_real_audio"] = False
        checks = {c["name"]: c for c in evaluate_checklist(ctx)}
        assert checks["lip_sync_basic"]["ok"] is False

    def test_weak_hook_fail(self):
        ctx = _good_ctx()
        ctx["contracts"][0]["emotion"] = {"beat": "SETUP", "intensity": 0.3}
        ctx["contracts"][0]["dialogue"] = []
        checks = {c["name"]: c for c in evaluate_checklist(ctx)}
        assert checks["opening_hook"]["ok"] is False


# ---------------------------------------------------------------------------
# 裁决：区分"结构通过"与"商业可发布"
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_pass_full_when_checklist_all_pass(self):
        r = FinalReviewService().run(_good_ctx(), story_id="d1")
        assert r.verdict == "PASS_FULL"
        assert r.commercial_ready is True
        assert r.failed_checklist == []

    def test_pass_structural_when_checklist_fails(self):
        ctx = _good_ctx()
        ctx["media_probe"] = {"ok": False, "issues": ["检出 2 段黑屏"]}
        r = FinalReviewService().run(ctx, story_id="d1")
        assert r.verdict == "PASS_STRUCTURAL"
        assert r.commercial_ready is False
        assert r.structural_passed is True          # 结构通过
        # 精准打回 + 可执行原因
        assert r.repair_plan["target_stage"] == "RENDERING"
        assert "no_black_or_frame_drop" in r.repair_plan["failed_checks"]

    def test_report_lists_checklist(self):
        rep = FinalReviewService().run(_good_ctx(), story_id="d1").report()
        assert len(rep["checklist"]) == len(PASS_FULL_CHECKLIST)
        assert rep["commercial_ready"] is True


# ---------------------------------------------------------------------------
# 发布闸：只有 PASS_FULL 才进发布包
# ---------------------------------------------------------------------------


class TestPublishGate:
    def _story_at_final(self, tmp_path, ctx):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        s = orc.create_story(title="A")
        # 直接塞入总审结果 + 推到 FINAL_REVIEW
        from final_review.service import FinalReviewService as FRS
        review = FRS().run(ctx, story_id=s.story_id)
        orc._final_reviews[s.story_id] = review
        s.stage = StoryStage.FINAL_REVIEW
        return orc, s

    def test_pass_full_can_publish(self, tmp_path):
        orc, s = self._story_at_final(tmp_path, _good_ctx())
        pub = orc.publish_story(s.story_id, decided_by="human")
        assert pub.stage is StoryStage.PUBLISHED

    def test_structural_publish_refused(self, tmp_path):
        ctx = _good_ctx()
        ctx["media_probe"] = {"ok": False, "issues": ["黑屏"]}
        orc, s = self._story_at_final(tmp_path, ctx)
        with pytest.raises(OrchestratorError):
            orc.publish_story(s.story_id)      # 未达 PASS_FULL → 拒绝


# ---------------------------------------------------------------------------
# 工厂主循环稳定性（Phase J）：多故事并行自愈 + 自动修复耗尽→人工终确认
# ---------------------------------------------------------------------------

_THEME = "县城女护士深夜被高利贷追债，最后逆袭翻身"


class TestFactoryLoop:
    def test_commercial_fix_escalates_to_final_human(self):
        from orchestrator import repair as rp
        a = rp.POLICY["COMMERCIAL_FIX"]
        assert a.escalation == rp.ESC_FINAL         # 修不动 → 成片人工终确认（点3）

    def test_multi_story_all_reach_quiescent(self, tmp_path):
        orc = PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260724")
        ids = []
        for i in range(3):
            s = orc.create_story(title=f"S{i}")
            orc.run_scriptbrain(s.story_id, _THEME)
            ids.append(s.story_id)
        out = orc.run_until_quiescent()             # 一把驱动到全部静止
        assert out["rounds"] < 200                  # 不空转/不死循环
        for sid in ids:
            st = orc.get_story(sid)
            # 每部都落到"人工终确认 / 成片待发布 / 终态"，无一卡死在自修复
            quiescent = (st["status"] in ("WAITING_HUMAN", "CLOSED")
                         or st["stage"] in ("FINAL_REVIEW", "FAILED", "ARCHIVED"))
            assert quiescent, (sid, st["stage"], st["status"])
            # 人工挂起只允许 3 个终确认点
            if st["status"] == "WAITING_HUMAN":
                assert st["hold"]["reason"] in (
                    "script_gate3", "identity_review", "final_review_critical")
