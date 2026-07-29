"""NarrativeContinuityQA —— 成片总审前的叙事连贯硬门。

针对上一轮 55s 样片的失败项逐条设卡（不通过不得 PASS）：
1. 每镜必须有 story_function + 本场 cause/effect/entry_state/exit_state（完整性）
2. 对白属主锁死：说话人必须在本镜在场角色内（禁止错角）
3. 全片台词禁止重复（禁止凑时长）
4. 情绪与剧情不冲突：负面场景（压力/悲伤/紧张…）里禁止正面情绪台词（无故笑）
5. 禁止静态镜凑秒：无台词镜时长超阈值判为凑秒
   外加：跨场因果链相连（本场 entry 承接上场 exit，presence 级）

对 contracts（已带 narrative/dialogue/assets）运行，返回 PASS/REPAIR + 打回目标。
纯结构判定、0 成本、可单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VERDICT_PASS = "PASS"
VERDICT_REPAIR = "REPAIR"

#: 无台词镜超过此时长（秒）视为"静态凑秒"
STATIC_SILENT_MAX_SEC = 6.5

#: 负面/正面情绪词（用于"压力场景无故笑"检测）
_NEGATIVE_TONE = ("压力", "悲伤", "紧张", "绝望", "愤怒", "焦虑", "恐惧", "屈辱",
                  "无助", "痛苦", "沉重", "崩溃", "委屈", "冷", "狠")
_POSITIVE_EMO = ("开心", "轻松", "喜悦", "微笑", "欢快", "愉快", "兴奋", "甜", "笑")

#: 这些景别天然可无台词（不算凑秒）
_SILENT_OK_TYPES = ("ESTABLISHING", "INSERT")


@dataclass
class NarrativeQAResult:
    verdict: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    target_stage: str = ""          # REPAIR 时打回目标（叙事重写）
    checked_shots: int = 0

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS

    def report(self) -> dict[str, Any]:
        by_cat: dict[str, int] = {}
        for i in self.issues:
            by_cat[i["category"]] = by_cat.get(i["category"], 0) + 1
        return {"verdict": self.verdict, "checked_shots": self.checked_shots,
                "issue_count": len(self.issues), "by_category": by_cat,
                "issues": self.issues, "target_stage": self.target_stage}


class NarrativeContinuityQAService:
    """叙事连贯硬门。"""

    def __init__(self, bundle=None) -> None:
        self.bundle = bundle

    @staticmethod
    def _issue(cat: str, msg: str, shot_id: str = "") -> dict[str, Any]:
        return {"category": cat, "message": msg, "shot_id": shot_id}

    def review(self, contracts: list[dict[str, Any]], *, story_id: str
               ) -> NarrativeQAResult:
        cs = sorted(contracts, key=lambda c: c.get("order", 0))
        issues: list[dict[str, Any]] = []

        # 1) 完整性：story_function + narrative 四要素
        for c in cs:
            sid = c["shot_id"]
            if not (c.get("story_function") or "").strip():
                issues.append(self._issue("completeness", "缺 story_function", sid))
            nar = c.get("narrative") or {}
            for f in ("cause", "effect", "entry_state", "exit_state"):
                if not (nar.get(f) or "").strip():
                    issues.append(self._issue("completeness", f"缺 narrative.{f}", sid))

        # 2) 属主锁死：台词说话人必须在场
        for c in cs:
            allowed = {ch.get("character_id")
                       for ch in (c.get("assets", {}).get("characters") or [])}
            allowed |= set(c.get("characters") or [])
            for d in c.get("dialogue") or []:
                cid = d.get("character_id")
                if allowed and cid not in allowed:
                    issues.append(self._issue(
                        "attribution",
                        f"台词属主 {cid} 不在本镜角色 {sorted(x for x in allowed if x)}；"
                        f"「{d.get('line', '')}」", c["shot_id"]))

        # 3) 全片台词禁止重复
        seen: dict[str, str] = {}
        for c in cs:
            for d in c.get("dialogue") or []:
                t = (d.get("line") or "").strip()
                if not t:
                    continue
                if t in seen:
                    issues.append(self._issue(
                        "repeated_line", f"台词重复「{t}」(已在 {seen[t]})", c["shot_id"]))
                else:
                    seen[t] = c["shot_id"]

        # 4) 情绪不冲突：负面场景禁止正面情绪台词
        for c in cs:
            nar = c.get("narrative") or {}
            tone = (nar.get("emotional_tone") or "") + (c.get("emotion", {}).get("mood") or "")
            if any(n in tone for n in _NEGATIVE_TONE):
                for d in c.get("dialogue") or []:
                    emo = d.get("emotion") or ""
                    if any(p in emo for p in _POSITIVE_EMO):
                        issues.append(self._issue(
                            "emotion_conflict",
                            f"负面场景（{tone}）里出现正面情绪台词（{emo}）："
                            f"「{d.get('line', '')}」", c["shot_id"]))

        # 5) 静态镜凑秒：无台词且超时长且非建立/信息镜
        for c in cs:
            if (not (c.get("dialogue"))
                    and float(c.get("duration_sec", 0)) > STATIC_SILENT_MAX_SEC
                    and c.get("shot_type") not in _SILENT_OK_TYPES):
                issues.append(self._issue(
                    "static_padding",
                    f"无台词镜时长 {c['duration_sec']}s 超 {STATIC_SILENT_MAX_SEC}s，"
                    f"疑似静态凑秒", c["shot_id"]))

        # 6) 跨场因果链相连（presence 级：相邻场 entry 承接 exit）
        scenes_order: list[str] = []
        scene_nar: dict[str, dict] = {}
        for c in cs:
            scn = c.get("scene_id")
            if scn not in scene_nar:
                scenes_order.append(scn)
                scene_nar[scn] = c.get("narrative") or {}
        for prev, cur in zip(scenes_order, scenes_order[1:]):
            if not (scene_nar[cur].get("entry_state") or "").strip():
                issues.append(self._issue(
                    "causal_chain", f"场 {cur} 缺 entry_state，因果链断裂"))
            if not (scene_nar[prev].get("exit_state") or "").strip():
                issues.append(self._issue(
                    "causal_chain", f"场 {prev} 缺 exit_state，因果链断裂"))

        verdict = VERDICT_REPAIR if issues else VERDICT_PASS
        result = NarrativeQAResult(
            verdict=verdict, issues=issues, checked_shots=len(cs),
            target_stage="SCRIPT_DONE" if issues else "")
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/narrative/{story_id}.json", result.report())
        return result


__all__ = ["NarrativeContinuityQAService", "NarrativeQAResult",
           "VERDICT_PASS", "VERDICT_REPAIR", "STATIC_SILENT_MAX_SEC"]
