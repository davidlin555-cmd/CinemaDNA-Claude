"""调参档 TuningProfile (Phase G) —— OptimizerDNA 学到的、被各模块消费的可逆参数。

设计铁律：所有调参**有界、可回滚**。工厂读它来改行为（当前落地一个安全旋钮：
自适应重试上限——对"总是修不好"的修复码降低上限，快速失败少浪费；对"多试一次
就能好"的适当放宽）。变差就整档回滚，绝不越优化越差。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 重试上限的硬边界（任何自适应都不得越界）
CAP_MIN = 1
CAP_MAX = 5


@dataclass
class TuningProfile:
    """一份可逆调参档。空档 = 用各模块的出厂默认。"""

    #: 修复码 → 重试上限覆盖（对齐 orchestrator.repair.POLICY 的 max_attempts）
    repair_cap_override: dict[str, int] = field(default_factory=dict)
    #: 观察名单：高频失败但尚不自动改的热点（供控制台展示 + 人工关注）
    watch: list[str] = field(default_factory=list)
    #: 生成本档时的基线指标（自我验收用：之后比它好才保留）
    baseline: dict[str, float] = field(default_factory=dict)
    #: 版本号，每 apply 自增，便于回滚定位
    version: int = 0
    #: 上一版档（用于一键回滚）
    _prev: dict[str, Any] | None = field(default=None, repr=False)

    def cap_for(self, code: str, default: int) -> int:
        """取某修复码的有效重试上限（优先用学到的覆盖，再夹到硬边界）。"""
        cap = self.repair_cap_override.get(code, default)
        return max(CAP_MIN, min(CAP_MAX, int(cap)))

    def snapshot(self) -> dict[str, Any]:
        return {
            "repair_cap_override": dict(self.repair_cap_override),
            "watch": list(self.watch),
            "baseline": dict(self.baseline),
            "version": self.version,
        }

    def apply(self, *, cap_override: dict[str, int], watch: list[str],
              baseline: dict[str, float]) -> None:
        """应用一版新调参（先存旧档以便回滚）。"""
        self._prev = self.snapshot()
        self.repair_cap_override = {
            k: max(CAP_MIN, min(CAP_MAX, int(v))) for k, v in cap_override.items()}
        self.watch = list(watch)
        self.baseline = dict(baseline)
        self.version += 1

    def rollback(self) -> bool:
        """回滚到上一版档。成功返回 True（无上一版返回 False）。"""
        if self._prev is None:
            return False
        p = self._prev
        self.repair_cap_override = dict(p["repair_cap_override"])
        self.watch = list(p["watch"])
        self.baseline = dict(p["baseline"])
        self.version = p["version"]
        self._prev = None
        return True

    def to_dict(self) -> dict[str, Any]:
        return {**self.snapshot(), "has_prev": self._prev is not None}
