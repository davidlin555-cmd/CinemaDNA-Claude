"""ScriptBrain Supervisor — 编剧工厂总调度 (Phase 2 最小可运行版)

对应多智能体文档 §2.2 的 ScriptBrain Supervisor：决定谁写、谁审、何时锁定，
本身不写剧本内容（内容在 agents.py + library.py）。

流水线：

    Idea Miner → [Gate 1 题材] → Showrunner → Plot Architect → [Gate 2 大纲]
      → Episode Writer → Dialogue Master → 组装 + 结构校验
      → Script Critic + Market Hook Critic → [Gate 3 拍摄版]

**与资产 Gate 的本质区别**：ScriptBrain 的三道闸门是"编辑质量"闸门，
人工可以放行（剧本好不好是审美判断）。IdentityDNA 的硬性拦截是法律红线，
人工不可放行。两者不要混为一谈。

只有 Gate 3 通过（或人工放行）才允许把剧本提交给下游资产阶段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinemadna.asset_brain.common import schemas

from . import agents
from .brief import ScriptBrief
from .script import (
    assemble_shooting_script,
    build_character_list,
    build_scene_export,
    validate_shooting_script,
)

GATE_CONCEPT = "G1_CONCEPT"
GATE_OUTLINE = "G2_OUTLINE"
GATE_SHOOTING_SCRIPT = "G3_SHOOTING_SCRIPT"


@dataclass
class ScriptResult:
    """一次剧本生产的全部产物。"""

    brief: ScriptBrief
    concept: dict[str, Any]
    show_bible: dict[str, Any]
    outline: dict[str, Any]
    shooting_script: dict[str, Any]
    scene_export: dict[str, Any]
    character_list: dict[str, Any]
    script_review: dict[str, Any]
    market_review: dict[str, Any]
    gates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def script_id(self) -> str:
        return self.shooting_script["script_id"]

    @property
    def gate3(self) -> dict[str, Any]:
        return next(g for g in self.gates if g["gate"] == GATE_SHOOTING_SCRIPT)

    @property
    def needs_human_review(self) -> bool:
        """Gate 3 未自动通过 → 必须人工看一眼才能进入资产阶段。"""
        return not self.gate3["passed"]

    @property
    def scene_count(self) -> int:
        return len(self.scene_export["scenes"])

    def artifacts(self) -> dict[str, Any]:
        """产物 → Bundle 相对路径映射，供 Orchestrator 落盘。"""
        sid = self.script_id
        return {
            "01_script/concept_brief.json": self.concept,
            "01_script/show_bible.json": self.show_bible,
            "01_script/episode_outline.json": self.outline,
            "01_script/shooting_script.json": self.shooting_script,
            "01_script/scene_export.json": self.scene_export,
            "01_script/character_list.json": self.character_list,
            f"11_review/script_review/{sid}.json": self.script_review,
            f"11_review/market_review/{sid}.json": self.market_review,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "script_id": self.script_id,
            "title": self.shooting_script["title"],
            "genre": self.shooting_script["genre"],
            "genre_label": self.shooting_script["genre_label"],
            "episodes": len(self.shooting_script["episodes"]),
            "scenes": self.scene_count,
            "characters": len(self.scene_export["characters"]),
            "props": len(self.scene_export["props"]),
            "script_review_passed": self.script_review["passed"],
            "retention_score": self.market_review["retention_score"],
            "needs_human_review": self.needs_human_review,
            "gate3_issues": self.gate3["issues"],
        }


class ScriptBrainService:
    """ScriptBrain 最小可运行版：一句题材进，拍摄版 JSON 出。

    配了 `narrative_author`（LLMNarrativeAuthor）时，用 LLM **作者化**写台词与场景
    因果（连贯、不重复、贴情绪），取代模板挑句；LLM 不可用或校验失败则降级回模板
    并如实标记，绝不静默用坏叙事。
    """

    def __init__(self, *, narrative_author: Any = None,
                 commercial_council: Any = None, max_revise_iters: int = 3) -> None:
        self.narrative_author = narrative_author
        self.commercial_council = commercial_council
        self.max_revise_iters = max(1, int(max_revise_iters))
        self.narrative_mode = "template"
        self.council_result = None      # 最终采用稿的责编评审

    def _author_dialogue(self, episodes, outline, show_bible, b):
        """台词/叙事：LLM 作者化 + 商业责编 Council 自我改进闭环（判 REVISE 就带意见
        重写，直到过审或用尽轮数取最佳稿）。不可用/失败降级模板并如实标记。"""
        author = self.narrative_author
        if author is None or not getattr(author, "available", lambda: False)():
            self.narrative_mode = "template"
            return agents.dialogue_master(episodes, outline, b)

        theme = getattr(b, "theme", "") or getattr(b, "topic", "") or ""
        chars = show_bible.get("characters") or outline.get("characters") or []
        logline = outline.get("logline", "") or show_bible.get("logline", "")
        genre = str(outline.get("genre", ""))
        est = sum(float(s.get("estimated_duration_sec") or 0)
                  for ep in episodes for s in ep.get("scenes") or [])
        target_sec = min(45.0, max(30.0, est or 35.0))
        council = self.commercial_council

        notes: list[str] = []
        best_ep = None
        best_score = -1.0
        last_err = None
        for it in range(self.max_revise_iters):
            try:
                authored = author.author_episodes(
                    episodes, outline, theme=theme, logline=logline, genre=genre,
                    characters=chars, target_sec=target_sec, feedback=notes)
            except Exception as e:  # noqa: BLE001 —— 单轮失败不终止闭环，跳过继续
                last_err = e
                continue
            if council is None or not getattr(council, "available",
                                              lambda: False)():
                self.narrative_mode = "llm"
                return authored
            try:
                cres = council.review(
                    {"episodes": authored, "title": getattr(b, "theme", ""),
                     "logline": logline}, story_id="scriptbrain")
            except Exception:  # noqa: BLE001 —— 责编失败：该稿视为可用，退出闭环
                self.narrative_mode = "llm+council_unavailable"
                return authored
            self.council_result = cres
            if cres.passed:
                self.narrative_mode = f"llm+council_pass@iter{it + 1}"
                return authored
            if cres.score > best_score:
                best_ep, best_score = authored, cres.score
            notes = cres.notes               # 带责编意见重写
        # 用尽轮数仍未过审：采用**最佳作者化稿**（不退回模板）；实在没有才降级模板
        if best_ep is not None:
            self.narrative_mode = f"llm+council_revise_best@{best_score}"
            return best_ep
        self.narrative_mode = (f"template_fallback:{type(last_err).__name__}"
                               if last_err else "template")
        return agents.dialogue_master(episodes, outline, b)

    def run(self, brief: ScriptBrief | str | dict[str, Any]) -> ScriptResult:
        b = ScriptBrief.coerce(brief)
        gates: list[dict[str, Any]] = []

        # --- Idea Miner → Gate 1 -----------------------------------------
        concept = agents.idea_miner(b)
        gates.append(
            self._gate(
                GATE_CONCEPT,
                passed=bool(concept["hook_map"]),
                issues=[] if concept["hook_map"] else ["题材没有产出任何钩子"],
            )
        )

        # --- Showrunner → Plot Architect → Gate 2 -------------------------
        show_bible = agents.showrunner(concept, b)
        outline = agents.plot_architect(show_bible, concept, b)
        outline_issues = [
            f"{ep['episode_id']} 没有节拍" for ep in outline["episodes"] if not ep["beats"]
        ]
        gates.append(
            self._gate(GATE_OUTLINE, passed=not outline_issues, issues=outline_issues)
        )

        # --- Episode Writer → 台词/叙事（LLM 作者化，失败降级模板）→ 组装 ------
        episodes = agents.episode_writer(outline, show_bible, b)
        episodes = self._author_dialogue(episodes, outline, show_bible, b)
        shooting_script = assemble_shooting_script(b, concept, show_bible, episodes)
        # 把责编 Council 评审随剧本带下去（下游硬门直接读，不再二次调 LLM）
        shooting_script["narrative_mode"] = self.narrative_mode
        if self.council_result is not None:
            shooting_script["commercial_council"] = self.council_result.report()

        # 结构契约：ScriptBrain 永远不向下游吐出接不了单的剧本
        validate_shooting_script(shooting_script)

        scene_export = build_scene_export(shooting_script)
        character_list = build_character_list(shooting_script)

        # --- 双 Critic → Gate 3 -------------------------------------------
        script_review = agents.script_critic(shooting_script)
        market_review = agents.market_hook_critic(shooting_script)
        g3_issues = list(script_review["issues"]) + list(market_review["issues"])
        if not market_review["passed"] and not market_review["issues"]:
            g3_issues.append(
                f"留存分 {market_review['retention_score']} 低于及格线 "
                f"{agents.MIN_RETENTION_SCORE}"
            )
        gates.append(
            self._gate(
                GATE_SHOOTING_SCRIPT,
                passed=script_review["passed"] and market_review["passed"],
                issues=g3_issues,
            )
        )

        return ScriptResult(
            brief=b,
            concept=concept,
            show_bible=show_bible,
            outline=outline,
            shooting_script=shooting_script,
            scene_export=scene_export,
            character_list=character_list,
            script_review=script_review,
            market_review=market_review,
            gates=gates,
        )

    @staticmethod
    def _gate(name: str, *, passed: bool, issues: list[str]) -> dict[str, Any]:
        return {
            "gate": name,
            "passed": passed,
            "issues": issues,
            # 编辑质量闸门：人工可放行（与 IdentityDNA 的法律红线不同）
            "human_overridable": True,
            "decided_at": schemas.utc_now_iso() if passed else None,
            "decided_by": "auto_gate" if passed else None,
        }
