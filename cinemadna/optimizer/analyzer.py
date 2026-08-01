"""复盘分析 (Phase G) —— 把工厂遥测翻译成排序好的优化建议。

输入是 orchestrator.telemetry()（全厂聚合）。输出是 Recommendation 列表：
每条带证据、建议动作、置信度、是否可自动应用。

当前自动落地一类安全旋钮：**自适应重试上限**——
  - 某修复码"总在耗尽后判失败"（recovery_rate 低）→ 建议降低上限，快速失败少浪费
  - 某修复码"多试一次就常能好"（尾部有恢复）→ 建议适当放宽
其余（提示词 A/B、场景 source_kind、成本-质量、人工偏好）先出建议、上观察名单，
不擅自改高风险参数——诚实区分"已自动"与"仅建议"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orchestrator import repair as repair_policy

#: 触发"降低上限、快速失败"的恢复率阈值：修复 N 次里救回不到这个比例就别硬试
LOW_RECOVERY = 0.34
#: 触发"上观察名单"的热点占比：某码占全部修复的比例超过它就重点关注
HOTSPOT_SHARE = 0.30
#: 人工驳回率超过它 → 收紧对应模块前置（多修一次再交人，减少重复打回）
HIGH_REJECT = 0.4

#: 修复码 → 所属模块（热点按模块聚合用）
_CODE_MODULE: dict[str, str] = {
    "SCENE_FIX": "scene", "PROP_FIX": "prop",
    "IDENTITY_MISSING": "identity", "IDENTITY_REDLINE": "identity",
    "CONTINUITY": "director", "DIRECTOR_FIX": "director",
    "PERFORMANCE_FIX": "performance", "AUDIO_FIX": "audio",
    "RENDER_RETRY": "render", "RENDER_RESIGN": "render",
    "ASSET_REGEN": "asset", "QA_REPAIR": "qa",
    "FINAL_REPAIR": "final", "COMMERCIAL_FIX": "final",
}

#: 人工终确认闸门 → 喂它的修复码（驳回率高就多修这些一次再交人）
GATE_FEEDING_CODES: dict[str, list[str]] = {
    "identity_review": ["IDENTITY_MISSING", "ASSET_REGEN"],
    "final_review_critical": ["FINAL_REPAIR", "COMMERCIAL_FIX"],
    "script_gate3": [],   # 剧本人写，无自动修复码可调（只出建议）
}

#: 所有"喂人工闸门"的码：这些码不做 fail-fast 降档（质量优先于省事）
_PROTECTED_CODES = {c for codes in GATE_FEEDING_CODES.values() for c in codes}


@dataclass
class Recommendation:
    kind: str                 # "repair_cap" | "hotspot" | "human_pref" | "cost"
    target: str               # 修复码 / 模块名
    action: str               # 人话建议
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    auto: bool = False        # 是否可自动应用（只有低风险可逆项为 True）
    #: 自动项：对 repair_cap_override 的具体改动
    cap_delta: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind, "target": self.target, "action": self.action,
            "evidence": self.evidence, "confidence": round(self.confidence, 3),
            "auto": self.auto, "cap_delta": self.cap_delta,
        }


def analyze(telemetry: dict[str, Any]) -> list[Recommendation]:
    """由遥测产出优化建议（不改任何状态，纯分析）。"""
    recs: list[Recommendation] = []
    repairs = telemetry.get("repairs") or {}      # code -> {attempts, recovered, failed}
    total_attempts = sum(r.get("attempts", 0) for r in repairs.values())

    for code, r in sorted(repairs.items(),
                          key=lambda kv: kv[1].get("attempts", 0), reverse=True):
        attempts = r.get("attempts", 0)
        if attempts < 2:
            continue
        recovered = r.get("recovered", 0)
        recovery = recovered / attempts if attempts else 0.0
        share = attempts / total_attempts if total_attempts else 0.0
        default_cap = repair_policy.POLICY[code].max_attempts if code in repair_policy.POLICY else 2

        # 恢复率低 + 有一定量 → 降低上限快速失败（安全、可逆、可自动）
        # 但"喂人工闸门"的码不降档：那关系到少让人重复打回，质量优先于省事
        if recovery < LOW_RECOVERY and default_cap > 1 and code not in _PROTECTED_CODES:
            recs.append(Recommendation(
                kind="repair_cap", target=code, auto=True,
                action=f"{code} 恢复率仅 {recovery:.0%}，建议上限 {default_cap}→{default_cap-1}，快速失败少浪费",
                evidence={"attempts": attempts, "recovery_rate": round(recovery, 3)},
                confidence=min(0.9, 0.5 + attempts / 20),
                cap_delta={code: default_cap - 1}))

        # 热点：占比高 → 上观察名单（不自动改，提示优化对应模块）
        if share >= HOTSPOT_SHARE:
            recs.append(Recommendation(
                kind="hotspot", target=code,
                action=f"{code} 占全部修复的 {share:.0%}，是最大热点，建议优化 {repair_policy.POLICY.get(code).agent if code in repair_policy.POLICY else code} 的产出质量",
                evidence={"attempts": attempts, "share": round(share, 3)},
                confidence=0.7))

    # 人工偏好学习：某人工点驳回率高 → 让喂它的模块**多修一次再交人**（反校准）
    for gate, h in (telemetry.get("human") or {}).items():
        decided = h.get("approved", 0) + h.get("rejected", 0)
        if decided < 3:
            continue
        reject = h["rejected"] / decided
        if reject < HIGH_REJECT:
            continue
        feeding = GATE_FEEDING_CODES.get(gate, [])
        if feeding:
            # 把喂该闸门的修复码上限 +1（有界），前置多修一次，减少重复打回
            bump = {c: min(CAP_MAX_BUMP,
                           (repair_policy.POLICY[c].max_attempts
                            if c in repair_policy.POLICY else 2) + 1)
                    for c in feeding}
            recs.append(Recommendation(
                kind="human_pref", target=gate, auto=True,
                action=f"{gate} 驳回率 {reject:.0%}：{feeding} 上限+1，前置多修一次再交人，减少重复打回",
                evidence={"decided": decided, "reject_rate": round(reject, 3),
                          "feeding": feeding},
                confidence=min(0.85, 0.5 + decided / 20), cap_delta=bump))
        else:
            recs.append(Recommendation(
                kind="human_pref", target=gate,
                action=f"{gate} 驳回率 {reject:.0%}：该闸门无自动修复码可调，建议人工优化其前置模块",
                evidence={"decided": decided, "reject_rate": round(reject, 3)},
                confidence=0.6))

    return recs


#: 人工偏好加档的硬上界（与 profile.CAP_MAX 一致）
CAP_MAX_BUMP = 5


def hotspot_report(telemetry: dict[str, Any]) -> dict[str, Any]:
    """修复热点报告：按**模块**聚合最常失败的修复 + 按**闸门**聚合人工驳回。"""
    repairs = telemetry.get("repairs") or {}
    by_module: dict[str, dict[str, int]] = {}
    for code, r in repairs.items():
        mod = _CODE_MODULE.get(code, code)
        m = by_module.setdefault(mod, {"attempts": 0, "failed": 0, "codes": []})
        m["attempts"] += r.get("attempts", 0)
        m["failed"] += r.get("failed", 0)
        if code not in m["codes"]:
            m["codes"].append(code)
    modules = sorted(
        ({"module": mod, "attempts": v["attempts"], "failed": v["failed"],
          "fail_rate": round(v["failed"] / v["attempts"], 3) if v["attempts"] else 0.0,
          "codes": v["codes"]}
         for mod, v in by_module.items()),
        key=lambda x: (x["attempts"], x["failed"]), reverse=True)

    gates = []
    for gate, h in (telemetry.get("human") or {}).items():
        decided = h.get("approved", 0) + h.get("rejected", 0)
        if decided:
            gates.append({"gate": gate, "decided": decided,
                          "rejected": h.get("rejected", 0),
                          "reject_rate": round(h.get("rejected", 0) / decided, 3)})
    gates.sort(key=lambda g: g["reject_rate"], reverse=True)

    return {
        "top_modules": modules[:5],
        "top_gates": gates,
        "worst_module": modules[0]["module"] if modules else None,
        "worst_gate": gates[0]["gate"] if gates else None,
        "total_repair_attempts": sum(m["attempts"] for m in modules),
    }


def target_metrics(telemetry: dict[str, Any]) -> dict[str, float]:
    """自我验收核心指标（越低越好）：单故事平均修复 + 失败率 + **人工驳回率**。"""
    stories = max(1, telemetry.get("stories_total", 0))
    repairs = telemetry.get("repairs") or {}
    total_attempts = sum(r.get("attempts", 0) for r in repairs.values())
    failed = telemetry.get("stories_failed", 0)
    human = telemetry.get("human") or {}
    decided = sum(h.get("approved", 0) + h.get("rejected", 0) for h in human.values())
    rejected = sum(h.get("rejected", 0) for h in human.values())
    return {
        "avg_repair_attempts": round(total_attempts / stories, 4),
        "fail_rate": round(failed / stories, 4),
        "human_reject_rate": round(rejected / decided, 4) if decided else 0.0,
    }


def is_better(before: dict[str, float], after: dict[str, float]) -> bool:
    """after 是否不劣于 before（修复次数/失败率/人工驳回率都不显著变差）。"""
    tol = 1e-9
    return (after.get("avg_repair_attempts", 9) <= before.get("avg_repair_attempts", 9) + tol
            and after.get("fail_rate", 9) <= before.get("fail_rate", 9) + tol
            and after.get("human_reject_rate", 9) <= before.get("human_reject_rate", 9) + tol)
