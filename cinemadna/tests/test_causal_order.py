"""因果链修复回归测试 —— 语音驱动时长 / mux 不裁语音 / 剧情锁道具状态。

守住三条：
1. 语音时长回写镜头时长（有对白按语音，无对白留导演），绝不固定时长反裁语音
2. mux 去 -shortest：视频定格补帧、音频补静音，语音永不被裁
3. 信息镜状态由剧情锁定（催债/逾期 → 账单显示"已逾期"，不是"支付成功"）
"""

from __future__ import annotations

from audio.mixer import AudioMixer, DialogueClip
from audio.service import AudioDNAService
from asset_brain.prop_dna.service import PropDNAService

from tests.test_director import ready_contract


# ---------------------------------------------------------------------------
# 1. 语音驱动时长回写
# ---------------------------------------------------------------------------


class TestReconcileDurations:
    @staticmethod
    def _voiced(c, durs):
        c["voice_contract"] = {"bindings": [
            {"speaker": "char_a", "line": f"L{i}", "audio_file": f"a{i}.mp3",
             "duration_sec": d} for i, d in enumerate(durs)]}
        return c

    def test_long_voice_extends_shot(self):
        c = self._voiced(ready_contract(duration_sec=5.0), [4.0, 3.0])  # 语音 7s
        old_hash = c["contract_hash"]
        changed = AudioDNAService(None).reconcile_durations([c])
        assert c["shot_id"] in changed
        assert c["duration_sec"] > 7.0                      # 语音驱动，超过导演 5s
        assert c["duration_provenance"]["source"] == "voice_driven"
        assert c["contract_hash"] and c["contract_hash"] != old_hash   # 已重签

    def test_short_voice_keeps_director_floor(self):
        c = self._voiced(ready_contract(duration_sec=8.0), [1.5])       # 语音 1.5s
        changed = AudioDNAService(None).reconcile_durations([c])
        assert c["shot_id"] not in changed
        assert c["duration_sec"] == 8.0                     # 不低于导演最低时长
        assert c["duration_provenance"]["source"] == "director_min_floor"

    def test_no_dialogue_reaction_keeps_director(self):
        c = ready_contract(duration_sec=4.3)
        c["voice_contract"] = {"bindings": []}
        changed = AudioDNAService(None).reconcile_durations([c])
        assert not changed
        assert c["duration_sec"] == 4.3
        assert c["duration_provenance"]["source"] == "director_no_dialogue"

    def test_overlong_voice_clamped_to_max(self):
        c = self._voiced(ready_contract(duration_sec=5.0), [11.0, 6.0])  # 语音 17s
        AudioDNAService(None).reconcile_durations([c])
        assert c["duration_sec"] == 12.0                    # clamp 到单镜上限
        assert c["duration_provenance"]["clamped"] is True


# ---------------------------------------------------------------------------
# 2. mux 不裁语音（去 -shortest，视频 tpad、音频 apad，-t 主时长）
# ---------------------------------------------------------------------------


class TestMixerNoHardCut:
    def _argv(self):
        m = AudioMixer(runner=lambda a, c: None)
        return m.build_argv(
            video="/v.mp4", clips=[DialogueClip("/a.mp3", 0.0)], duration=9.0,
            subtitles_name=None, bgm=False, ambience="none",
            bgm_input_index=None, out_name="out.mp4")

    def test_no_shortest(self):
        assert "-shortest" not in self._argv()

    def test_master_duration_enforced(self):
        argv = self._argv()
        assert "-t" in argv and "9.0" in argv

    def test_video_holds_and_audio_pads(self):
        fc = " ".join(self._argv())
        assert "tpad=stop_mode=clone:stop_duration=9.0" in fc   # 视频定格补帧
        assert "apad" in fc                                     # 音频补静音


# ---------------------------------------------------------------------------
# 3. 剧情锁定信息镜状态
# ---------------------------------------------------------------------------


class TestStoryLockedScreenState:
    def test_debt_bill_forced_overdue(self):
        svc = PropDNAService(None)
        req = {"name": "医院缴费单/催债单", "description": "高利贷逾期催债",
               "story_function": "逼债"}
        state = svc._story_screen_state("payment", req, "完整")
        assert "逾期" in state                       # 剧情锁定 → 已逾期，不许支付成功

    def test_hospital_bill_pressure_forces_overdue(self):
        # 真实剧本用例：名叫"医院缴费单"、功能"建立女主经济压力" → 必须逾期
        svc = PropDNAService(None)
        req = {"name": "医院缴费单", "description": "旧的纸质医院缴费单",
               "story_function": "建立女主经济压力"}
        assert "逾期" in svc._story_screen_state("payment", req, "完整")

    def test_plain_bill_becomes_pending_not_success(self):
        svc = PropDNAService(None)
        req = {"name": "水电费账单", "description": "", "story_function": ""}
        assert "待支付" in svc._story_screen_state("payment", req, "完整")

    def test_non_bill_receipt_unchanged(self):
        svc = PropDNAService(None)
        req = {"name": "超市小票", "description": "买菜", "story_function": "日常"}
        assert svc._story_screen_state("payment", req, "完整") == "完整"

    def test_state_with_explicit_semantics_untouched(self):
        svc = PropDNAService(None)
        req = {"name": "催债单", "description": "逾期", "story_function": ""}
        # 已含语义（成功）就不叠加，避免自相矛盾
        assert svc._story_screen_state("payment", req, "支付成功") == "支付成功"

    def test_non_payment_kind_unchanged(self):
        svc = PropDNAService(None)
        req = {"name": "催债短信", "description": "逾期"}
        assert svc._story_screen_state("phone_chat", req, "未读") == "未读"
