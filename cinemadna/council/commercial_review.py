"""商业短剧 AI Council —— 用 LLM 像爆款编辑一样审片，不是 mock 打分。

原 dim_commercial 复用 market_hook_critic（关键词凑的 mock 留存分），不是真正的商业
判断。本模块用 LLM 以"竖屏商业短剧责编"视角审节拍表：前 3 秒钩子是否抓人、信息密度
是否够、冲突是否层层升级、有无情绪爽点、结尾悬念是否够钩人、是否适配竖屏短剧平台。

输出结构化评审（verdict + 各维度分 + 具体改法）。client 可注入（测试假实现）。
文本审，几分钱/次。判负→精准改法（打回剧本层）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import config

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
CallFn = Callable[[str, str], str]

#: 商业过关的综合分下限。责编 LLM 天生挑剔、几乎不给"PASS"字样，故过关只认**分数**
#: （score≥门槛即绿灯），门槛设在"可投产"的合理商业线（严格责编罕给 >0.75）。
MIN_SCORE = 0.68

_SYSTEM = (
    "你是竖屏商业短剧（抖音/快手）资深责编，只按'能不能爆、能不能留住人'审片。"
    "严格、挑剔、只看留存与爽感。按维度打分(0-1)并给**可执行**改法。只输出 JSON。"
)


@dataclass
class CouncilResult:
    verdict: str                       # PASS / REVISE
    score: float
    scores: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    target_stage: str = ""

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def report(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "score": self.score,
                "scores": self.scores, "notes": self.notes,
                "target_stage": self.target_stage}


def _anthropic_call(model: str) -> CallFn:
    import anthropic
    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))

    def call(system: str, user: str) -> str:
        msg = client.messages.create(model=model, max_tokens=1500, system=system,
                                     messages=[{"role": "user", "content": user}])
        return "".join(getattr(b, "text", "") for b in msg.content)
    return call


class CommercialCouncil:
    """LLM 商业短剧责编评审。"""

    def __init__(self, *, call: CallFn | None = None, model: str | None = None,
                 min_score: float = MIN_SCORE) -> None:
        self.model = model or config.get("NARRATIVE_MODEL", "") or _DEFAULT_MODEL
        self._call = call
        self.min_score = min_score

    def available(self) -> bool:
        return self._call is not None or config.has("ANTHROPIC_API_KEY")

    def _ensure_call(self) -> CallFn:
        if self._call is None:
            self._call = _anthropic_call(self.model)
        return self._call

    @staticmethod
    def _beatsheet(shooting_script: dict) -> list[dict]:
        out = []
        for ep in shooting_script.get("episodes") or []:
            for s in ep.get("scenes") or []:
                for b in s.get("beats") or []:
                    out.append({"type": b.get("type"), "sec": b.get("seconds"),
                                "desc": b.get("description"),
                                "line": b.get("line"), "emotion": b.get("emotion")})
        return out

    def review(self, shooting_script: dict, *, story_id: str = "") -> CouncilResult:
        beats = self._beatsheet(shooting_script)
        user = ("按竖屏商业短剧标准审这份节拍表，维度打分(0-1)：hook(前3秒抓人)、"
                "density(信息密度)、escalation(冲突升级)、payoff(情绪爽点)、"
                "cliffhanger(结尾钩人)、platform_fit(竖屏短剧适配)。给综合 score(0-1)、"
                "verdict(PASS/REVISE)、notes(3条以内可执行改法)。\n\n"
                "输出 JSON：{\"score\":0-1,\"verdict\":\"PASS|REVISE\","
                "\"scores\":{...六维...},\"notes\":[...]}\n\n节拍表："
                + json.dumps({"title": shooting_script.get("title", ""),
                              "logline": shooting_script.get("logline", ""),
                              "beats": beats}, ensure_ascii=False))
        text = self._ensure_call()(_SYSTEM, user)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            # 审核器无返回不静默放行：判 REVISE
            return CouncilResult("REVISE", 0.0, notes=["Council 未返回有效评审"],
                                 target_stage="SCRIPT_DONE")
        data = json.loads(m.group(0))
        score = float(data.get("score", 0) or 0)
        # 只认分数（忽略 LLM 的 verdict 字样——它几乎永远说 REVISE）
        verdict = "PASS" if score >= self.min_score else "REVISE"
        return CouncilResult(
            verdict=verdict, score=round(score, 3),
            scores={k: round(float(v), 3) for k, v in
                    (data.get("scores") or {}).items() if _isnum(v)},
            notes=list(data.get("notes") or [])[:3],
            target_stage="" if verdict == "PASS" else "SCRIPT_DONE")


def _isnum(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


__all__ = ["CommercialCouncil", "CouncilResult", "MIN_SCORE"]
