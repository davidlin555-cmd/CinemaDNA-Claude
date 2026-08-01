"""渲染预算闸测试 (Phase 6) —— 安全关键，覆盖每一条保护

四重保护逐条验证：默认关闭 / 逐次确认 / 硬性上限 / 时长白名单。
不发任何真实请求。
"""

from __future__ import annotations

import pytest

from render.budget import (
    BudgetExceededError,
    BudgetGate,
    BudgetNotArmedError,
    DurationNotAllowedError,
    SubmissionNotConfirmedError,
    confirm_deny,
    confirm_yes,
)


def armed_gate(max_units=100.0, **kw):
    return BudgetGate(max_units=max_units, confirm=confirm_yes, **kw)


class TestDefaultOff:
    def test_disarmed_by_default(self):
        gate = BudgetGate()          # 没给预算
        assert gate.armed is False
        with pytest.raises(BudgetNotArmedError):
            gate.authorize(shot_id="s", duration_sec=5, model_name="kling-v1")

    def test_zero_or_negative_budget_is_disarmed(self):
        assert BudgetGate(max_units=0).armed is False
        assert BudgetGate(max_units=-5).armed is False

    def test_positive_budget_arms(self):
        assert BudgetGate(max_units=10).armed is True


class TestConfirmation:
    def test_no_confirm_callback_denies(self):
        gate = BudgetGate(max_units=100, confirm=None)   # 有预算但没确认回调
        with pytest.raises(SubmissionNotConfirmedError):
            gate.authorize(shot_id="s", duration_sec=5, model_name="kling-v1")

    def test_confirm_deny_blocks(self):
        gate = BudgetGate(max_units=100, confirm=confirm_deny)
        with pytest.raises(SubmissionNotConfirmedError):
            gate.authorize(shot_id="s", duration_sec=5, model_name="kling-v1")

    def test_confirm_yes_allows(self):
        gate = armed_gate()
        est = gate.authorize(shot_id="s", duration_sec=5, model_name="kling-v1")
        assert est > 0

    def test_confirm_receives_estimate_info(self):
        seen = {}
        def spy(info):
            seen.update(info); return True
        gate = BudgetGate(max_units=100, confirm=spy)
        gate.authorize(shot_id="shot_x", duration_sec=5, model_name="kling-v1")
        assert seen["item_id"] == "shot_x"
        assert seen["kind"] == "video"
        assert seen["estimated_units"] > 0
        assert seen["remaining_units"] == 100.0

    def test_authorize_generic_for_images(self):
        seen = {}
        gate = BudgetGate(max_units=10, confirm=lambda i: (seen.update(i), True)[1])
        est = gate.authorize_generic(item_id="char_a", est_units=0.01, kind="image")
        assert est == 0.01 and seen["kind"] == "image"
        gate.record(shot_id="char_a", units=est)
        assert gate.spent_units == 0.01

    def test_authorize_generic_respects_arming_and_cap(self):
        from render.budget import BudgetNotArmedError, BudgetExceededError
        with pytest.raises(BudgetNotArmedError):
            BudgetGate().authorize_generic(item_id="x", est_units=0.01)
        g = BudgetGate(max_units=0.005, confirm=confirm_yes)
        with pytest.raises(BudgetExceededError):
            g.authorize_generic(item_id="x", est_units=0.01)


class TestHardCap:
    def test_within_budget_ok(self):
        gate = armed_gate(max_units=3)    # 够一次（~2）
        est = gate.authorize(shot_id="s1", duration_sec=5, model_name="kling-v1")
        gate.record(shot_id="s1", units=est)
        assert gate.spent_units == est

    def test_second_submit_over_cap_blocked(self):
        """3 units 只够一次 ~2；第二次会超 → 拦下（正是上次误提交要防的）。"""
        gate = armed_gate(max_units=3)
        est = gate.authorize(shot_id="s1", duration_sec=5, model_name="kling-v1")
        gate.record(shot_id="s1", units=est)
        with pytest.raises(BudgetExceededError):
            gate.authorize(shot_id="s2", duration_sec=5, model_name="kling-v1")

    def test_exact_budget_boundary(self):
        gate = BudgetGate(max_units=4, confirm=confirm_yes)  # 正好两次 2
        for i in (1, 2):
            est = gate.authorize(shot_id=f"s{i}", duration_sec=5, model_name="kling-v1")
            gate.record(shot_id=f"s{i}", units=est)
        with pytest.raises(BudgetExceededError):
            gate.authorize(shot_id="s3", duration_sec=5, model_name="kling-v1")

    def test_remaining_tracks(self):
        gate = armed_gate(max_units=100)
        gate.record(shot_id="s", units=30)
        assert gate.remaining_units == 70.0


class TestDurationWhitelist:
    def test_only_5s_allowed_by_default(self):
        gate = armed_gate()
        gate.authorize(shot_id="s", duration_sec=5, model_name="kling-v1")   # ok
        with pytest.raises(DurationNotAllowedError):
            gate.authorize(shot_id="s", duration_sec=10, model_name="kling-v1")

    def test_can_widen_whitelist_explicitly(self):
        gate = BudgetGate(max_units=100, confirm=confirm_yes,
                          allowed_durations_sec=(5, 10))
        gate.authorize(shot_id="s", duration_sec=10, model_name="kling-v1")


class TestEstimateAndRecord:
    def test_estimate_uses_cost_table(self):
        gate = armed_gate()
        assert gate.estimate_units(duration_sec=5, model_name="kling-v1", mode="std") == 2.0
        assert gate.estimate_units(duration_sec=5, model_name="kling-v1", mode="pro") == 5.0

    def test_unknown_model_uses_conservative_fallback(self):
        gate = armed_gate()
        assert gate.estimate_units(duration_sec=5, model_name="mystery") == 5.0

    def test_record_only_after_success(self):
        """authorize 不记账；只有 record 才动 spent（提交失败不该扣）。"""
        gate = armed_gate()
        gate.authorize(shot_id="s", duration_sec=5, model_name="kling-v1")
        assert gate.spent_units == 0.0        # 授权了但没记账
        gate.record(shot_id="s", units=20)
        assert gate.spent_units == 20.0

    def test_summary_shape(self):
        gate = armed_gate()
        gate.record(shot_id="s", units=20)
        s = gate.summary()
        assert s["armed"] and s["submission_count"] == 1
        assert s["spent_units"] == 20.0
