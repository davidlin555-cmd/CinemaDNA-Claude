"""渲染预算闸 (Phase 6) —— 真实付费提交前的最后一道硬性保护

背景：一次探测失误曾误提交 4 个任务、烧掉约 73 units。这个模块把那个教训
做进代码：**任何真实付费提交都必须先过预算闸**，否则宁可不提交。

## 四重保护（全部满足才允许提交）

1. **默认关闭**：`max_units=0` → 未开预算 → 任何真实提交都被 `BudgetNotArmedError` 拦。
   要真实提交，必须显式给一个正的 `max_units`（"预算闸先上"）。
2. **逐次确认**：每次提交前调用 `confirm(info)`，返回 True 才放行。
   `confirm=None`（默认）视为拒绝——不给"忘了确认就自动花钱"留后门。
3. **硬性上限**：累计消耗 + 本次预估 > `max_units` → `BudgetExceededError`。
   即使确认回调有 bug，也花不超过预算。
4. **时长白名单**：当前只允许 5 秒（`allowed_durations_sec=(5,)`），
   其它时长 `DurationNotAllowedError`——"先只支持 5 秒测试，成功后再扩展"。

## 用法

    gate = BudgetGate(max_units=25, confirm=confirm_yes)   # 显式开预算
    est = gate.authorize(shot_id="x", duration_sec=5, model_name="kling-v1")  # 过闸
    ... 真实 POST 提交 ...
    gate.record(shot_id="x", units=est)                    # 提交成功后记账

预估单价是**保守值**（宁可高估，让上限早触发而不是晚触发）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------


class BudgetError(RuntimeError):
    """预算闸拦截的基类。"""


class BudgetNotArmedError(BudgetError):
    """预算闸未开启（max_units<=0）——默认状态，真实提交被拒。"""


class BudgetExceededError(BudgetError):
    """本次提交会超出预算上限。"""


class DurationNotAllowedError(BudgetError):
    """时长不在白名单（当前只允许 5 秒）。"""


class SubmissionNotConfirmedError(BudgetError):
    """提交前确认未通过（confirm 返回 False 或未提供）。"""


# ---------------------------------------------------------------------------
# 单价表（保守估计；真实单价以平台账单为准）
# ---------------------------------------------------------------------------

#: (model_name, mode, duration_sec) → 预估 units。
#: 实测校准：kling-v1 std 5s **实际约 1 unit**（一次生成套餐 26.5→25.5）。
#: 这里按 2 记（约 2× 真实），留安全余量但不过度限制；真实控制靠 max_units。
_COST_TABLE: dict[tuple[str, str, int], float] = {
    ("kling-v1", "std", 5): 2.0,
    ("kling-v1", "pro", 5): 5.0,
    ("kling-v1-6", "std", 5): 2.0,
    ("kling-v1-6", "pro", 5): 5.0,
}
#: 表里查不到时的兜底（未知模型可能是更贵的 v2/pro，保守高估）
_FALLBACK_UNIT_COST = 5.0


# ---------------------------------------------------------------------------
# 预算闸
# ---------------------------------------------------------------------------


@dataclass
class BudgetGate:
    """一次会话的渲染预算闸。"""

    #: 预算上限（units）。<=0 表示未开启 → 拒绝一切真实提交。
    max_units: float = 0.0
    #: 允许的时长白名单（秒）。默认只允许 5 秒。
    allowed_durations_sec: tuple[int, ...] = (5,)
    #: 逐次确认回调：收到 info dict，返回 True 才放行。None=拒绝。
    confirm: Callable[[dict[str, Any]], bool] | None = None
    #: 单价表（可覆盖，便于按真实账单校准）
    cost_table: dict[tuple[str, str, int], float] = field(
        default_factory=lambda: dict(_COST_TABLE)
    )

    spent_units: float = 0.0
    submissions: list[dict[str, Any]] = field(default_factory=list)

    # -- 状态 -----------------------------------------------------------

    @property
    def armed(self) -> bool:
        """是否已开启预算（有正的上限）。"""
        return self.max_units > 0

    @property
    def remaining_units(self) -> float:
        return round(self.max_units - self.spent_units, 4)

    # -- 预估 -----------------------------------------------------------

    def estimate_units(
        self, *, duration_sec: float, model_name: str, mode: str = "std"
    ) -> float:
        d = round(float(duration_sec))
        return self.cost_table.get((model_name, mode, d), _FALLBACK_UNIT_COST)

    # -- 授权核心（任何付费动作共用）-----------------------------------

    def _authorize_core(self, est: float, info: dict[str, Any]) -> float:
        """开没开 → 预算够不够 → 逐次确认。三关全过才返回预估，否则抛异常。"""
        if not self.armed:
            raise BudgetNotArmedError(
                "预算闸未开启：真实提交被拒。请显式设置 max_units 后再试"
                "（安全默认：不花钱）。"
            )
        if self.spent_units + est > self.max_units:
            raise BudgetExceededError(
                f"超预算：已用 {self.spent_units} units，本次约需 {est}，"
                f"上限 {self.max_units}，剩余 {self.remaining_units}。"
            )
        info = {**info, "estimated_units": est, "spent_units": self.spent_units,
                "max_units": self.max_units, "remaining_units": self.remaining_units}
        if self.confirm is None or not self.confirm(info):
            raise SubmissionNotConfirmedError(
                f"提交未确认（{info.get('item_id')}，约 {est} units）。真实提交需逐次确认。"
            )
        return est

    def authorize(
        self,
        *,
        shot_id: str,
        duration_sec: float,
        model_name: str,
        mode: str = "std",
    ) -> float:
        """视频付费提交前的授权（含 5 秒白名单）。"""
        d = round(float(duration_sec))
        if d not in self.allowed_durations_sec:
            raise DurationNotAllowedError(
                f"时长 {d}s 不在白名单 {self.allowed_durations_sec}"
                f"（当前只支持 5 秒测试）。"
            )
        est = self.estimate_units(
            duration_sec=duration_sec, model_name=model_name, mode=mode)
        return self._authorize_core(est, {
            "item_id": shot_id, "kind": "video", "duration_sec": d,
            "model_name": model_name, "mode": mode})

    def authorize_generic(
        self, *, item_id: str, est_units: float, kind: str = "generic",
        model: str = "",
    ) -> float:
        """通用付费授权（图像/音频等非视频动作）。不受时长白名单约束。"""
        return self._authorize_core(float(est_units), {
            "item_id": item_id, "kind": kind, "model": model})

    # -- 记账（提交成功后）---------------------------------------------

    def record(self, *, shot_id: str, units: float) -> None:
        """真实提交成功后记账。只在拿到 task_id 后调用。"""
        self.spent_units = round(self.spent_units + units, 4)
        self.submissions.append({"shot_id": shot_id, "units": units,
                                 "cumulative": self.spent_units})

    # -- 汇总 -----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        return {
            "armed": self.armed,
            "max_units": self.max_units,
            "spent_units": self.spent_units,
            "remaining_units": self.remaining_units,
            "allowed_durations_sec": list(self.allowed_durations_sec),
            "submission_count": len(self.submissions),
            "submissions": list(self.submissions),
        }


# ---------------------------------------------------------------------------
# 确认回调
# ---------------------------------------------------------------------------


def confirm_deny(info: dict[str, Any]) -> bool:
    """永远拒绝（最安全的默认）。"""
    return False


def confirm_yes(info: dict[str, Any]) -> bool:
    """无条件确认——仅用于操作者显式授权的自动化测试（如 --yes）。"""
    return True


def confirm_interactive(info: dict[str, Any]) -> bool:
    """命令行逐次确认：打印将花多少，等用户输入 y。"""
    print(
        f"\n⚠ 即将真实提交 {info['shot_id']}："
        f"约消耗 {info['estimated_units']} units"
        f"（已用 {info['spent_units']}/{info['max_units']}，"
        f"剩余 {info['remaining_units']}）。"
    )
    try:
        return input("  确认提交？输入 y 继续，其它取消：").strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


__all__ = [
    "BudgetGate",
    "BudgetError",
    "BudgetNotArmedError",
    "BudgetExceededError",
    "DurationNotAllowedError",
    "SubmissionNotConfirmedError",
    "confirm_deny",
    "confirm_yes",
    "confirm_interactive",
]
