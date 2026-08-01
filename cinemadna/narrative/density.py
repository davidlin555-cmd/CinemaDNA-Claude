"""戏剧信息密度规格 + DensityQA —— 商业短剧的"紧凑度"硬标准。

用户定义（商业级 30 秒）：
  - 8–12 个有效剧情节拍
  - 8–14 个镜头或明显的镜头内部状态变化
  - 每 2–4 秒发生一次新信息/动作/反应/权力变化
  - 至少一次打断（interruption）
  - 至少一次目标受阻（obstruction）
  - 至少一次主动反击（counterattack）
  - 最后 3–5 秒形成反转/强悬念（reversal/cliffhanger）

商业短剧 ≠ 把长电影压成少量长镜头，而是**单位时间戏剧信息密度更高**。本模块把
它编码成可判定的硬门：节拍/镜头数够不够、平均镜头是否够短（2–4s）、四类结构节拍
是否齐全、结尾是否有反转。不达标 = 不是紧凑短剧 = 不给 PASS。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VERDICT_PASS = "PASS"
VERDICT_REPAIR = "REPAIR"

#: 节拍类型标准词（LLM 产出、director 写进合约 beat_type）
BEAT_TYPES = ("setup", "info", "action", "reaction", "power_shift",
              "interruption", "obstruction", "counterattack",
              "reversal", "cliffhanger")

#: 必须出现的结构节拍
_REQUIRED_STRUCTURAL = {
    "interruption": "至少一次打断",
    "obstruction": "至少一次目标受阻",
    "counterattack": "至少一次主动反击",
}
#: 结尾反转节拍
_ENDING_TYPES = ("reversal", "cliffhanger")

#: 密度阈值
BEATS_PER_SEC_MIN = 1 / 4.0        # 每 ≤4 秒一个节拍
AVG_SHOT_MAX_SEC = 4.0             # 平均镜头 ≤4s
HARD_SHOT_MAX_SEC = 5.0           # 单镜封顶 5s（建立镜可略放宽）
MIN_SHOTS_FLOOR = 8               # ≥30s 至少 8 镜
ENDING_WINDOW_SEC = 5.0           # 结尾 5 秒内需反转


@dataclass
class DensityResult:
    verdict: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    target_stage: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    def report(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "metrics": self.metrics,
                "issue_count": len(self.issues), "issues": self.issues,
                "target_stage": self.target_stage}


class DensityQAService:
    """戏剧信息密度硬门。"""

    def __init__(self, bundle=None) -> None:
        self.bundle = bundle

    @staticmethod
    def _issue(cat: str, msg: str) -> dict[str, Any]:
        return {"category": cat, "message": msg}

    def review(self, contracts: list[dict[str, Any]], *, story_id: str,
               target_duration_sec: float | None = None) -> DensityResult:
        cs = sorted(contracts, key=lambda c: c.get("order", 0))
        issues: list[dict[str, Any]] = []
        n = len(cs)
        total = round(sum(float(c.get("duration_sec", 0)) for c in cs), 1)
        target = target_duration_sec or total or 30.0
        types = [(c.get("beat_type") or "").lower() for c in cs]
        present = set(t for t in types if t)
        avg_shot = round(total / n, 2) if n else 0.0
        # 内容单元 = 镜头数 + 镜内状态变化数（"镜头 或 明显的镜内状态变化"都算一个新发展）
        internal_shifts = sum(1 for c in cs if (c.get("internal_shift") or "").strip())
        content_units = n + internal_shifts

        # 期望内容单元：每 ~3s 一个新发展，且不少于地板
        want_shots = max(MIN_SHOTS_FLOOR, round(target / 3.0))

        # 1) 内容密度够不够（镜头 + 镜内状态变化）
        if content_units < want_shots:
            issues.append(self._issue(
                "shot_count", f"仅 {n} 镜 + {internal_shifts} 镜内变化 = {content_units} "
                f"个内容单元，{target:.0f}s 商业短剧需 ≥{want_shots}（每 2–4s 一个新发展）"))
        # 2) 平均镜头够不够短（紧凑）
        if avg_shot > AVG_SHOT_MAX_SEC:
            issues.append(self._issue(
                "pace", f"平均镜头 {avg_shot}s > {AVG_SHOT_MAX_SEC}s，节奏偏慢、不够紧凑"))
        # 3) 单镜封顶（禁长镜凑数）
        for c in cs:
            d = float(c.get("duration_sec", 0))
            if d > HARD_SHOT_MAX_SEC and c.get("shot_type") != "ESTABLISHING":
                issues.append(self._issue(
                    "long_shot", f"{c['shot_id']} 时长 {d}s > {HARD_SHOT_MAX_SEC}s，长镜凑数"))
        # 4) 四类结构节拍
        for t, label in _REQUIRED_STRUCTURAL.items():
            if t not in present:
                issues.append(self._issue("structural_beat", f"缺{label}（{t}）"))
        # 5) 结尾反转/悬念（最后 5 秒内）
        cum, ending_types = 0.0, []
        for c in reversed(cs):
            cum += float(c.get("duration_sec", 0))
            ending_types.append((c.get("beat_type") or "").lower())
            if cum >= ENDING_WINDOW_SEC:
                break
        if not any(t in _ENDING_TYPES for t in ending_types):
            issues.append(self._issue(
                "ending", f"结尾 {ENDING_WINDOW_SEC:.0f}s 内无反转/强悬念"
                f"（reversal/cliffhanger）"))

        metrics = {"shots": n, "total_sec": total, "avg_shot_sec": avg_shot,
                   "internal_shifts": internal_shifts, "content_units": content_units,
                   "want_shots": want_shots, "beat_types_present": sorted(present),
                   "beats_per_sec": round(content_units / total, 3) if total else 0}
        verdict = VERDICT_REPAIR if issues else VERDICT_PASS
        result = DensityResult(verdict=verdict, issues=issues, metrics=metrics,
                               target_stage="SCRIPT_DONE" if issues else "")
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/density/{story_id}.json", result.report())
        return result


__all__ = ["DensityQAService", "DensityResult", "BEAT_TYPES",
           "VERDICT_PASS", "VERDICT_REPAIR"]
