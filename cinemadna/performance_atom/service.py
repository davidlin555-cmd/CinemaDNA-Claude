"""PerformanceAtomDNA 服务 —— 检索/组合/校验/回流表情·动作原子。

多智能体（Grok 7 个 + 我补的 2 个音画/渲染接口）：
  AtomIndexer(AtomLibrary) 管库 · AtomRetriever 按意图检索 · SequenceComposer 组合时间轴
  (含 ExaggerateStyler 子步骤) · CoherenceJudge+AtomGate 硬门 · BackflowWriter 回流
  + AudioTimelineAligner 对齐语音驱动时长 · render_hint 提示词级驱动接口

导演只下 performance_intent（功能/情绪目标/强度/夸张/节拍/约束），本模块负责
"用哪些原子拼成连贯、服务 story_function、无凑秒、情绪变化有触发的表演"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .atoms import (
    ActionAtom, ComposedSequence, ExpressionAtom,
    seed_action_atoms, seed_expression_atoms,
)

#: story_function → 必须出现的"功能动作"标签关键词（信息镜要掏/看，施压要逼近…）
_FUNCTION_ACTION = {
    "揭示": ("掏出", "撕", "攥皱", "看"), "钩子": ("攥皱", "掏出", "后退"),
    "施压": ("逼近", "打断", "砸", "指向"), "反击": ("掏出", "撕", "指向", "起身"),
    "反转": ("撕", "掏出", "起身", "指向"), "决策": ("起身", "掏出", "撕"),
    "反应": (), "收束": ("起身", "转身"), "铺垫": (),
}
#: 正面情绪词（负面场景禁止无触发出现）
_POSITIVE = ("开心", "轻松", "喜悦", "欢快", "笑")


# ---------------------------------------------------------------------------
# AtomIndexer —— 原子库 + 组合回流 + 使用统计
# ---------------------------------------------------------------------------


class AtomLibrary:
    def __init__(self, *, seed: bool = True) -> None:
        self.expression: list[ExpressionAtom] = seed_expression_atoms() if seed else []
        self.action: list[ActionAtom] = seed_action_atoms() if seed else []
        self.composed_sequences: dict[str, list[dict[str, Any]]] = {}  # story_function → seqs
        self.usage_stats: dict[str, dict[str, int]] = {}

    def hot_sequence(self, story_function: str) -> dict[str, Any] | None:
        """同类 story_function 的成熟成功链（越用越稳，减少从零乱拼）。"""
        seqs = self.composed_sequences.get(story_function) or []
        return seqs[-1] if seqs else None

    def backflow(self, seq: ComposedSequence) -> None:
        """成功且过门的组合回流入库 + 记使用率。"""
        self.composed_sequences.setdefault(seq.story_function, []).append(seq.to_dict())
        st = self.usage_stats.setdefault(seq.story_function, {"passed": 0})
        st["passed"] += 1


# ---------------------------------------------------------------------------
# AtomRetriever —— 按导演意图检索候选原子
# ---------------------------------------------------------------------------


class AtomRetriever:
    def retrieve(self, lib: AtomLibrary, intent: dict[str, Any]
                 ) -> tuple[list[ExpressionAtom], list[ActionAtom]]:
        emos = _emotion_set(intent.get("emotion_target", ""))
        face = set((intent.get("face_focus") or "").replace("+", " ").split())
        body = set((intent.get("body_focus") or "").replace("+", " ").split())
        exp = [a for a in lib.expression if a.emotion in emos]
        if face:
            exp = [a for a in exp if a.face_region in face or a.face_region == "whole"] or exp
        act = [a for a in lib.action if a.emotion in emos]
        if body:
            act = [a for a in act if a.body_part in body or a.body_part == "whole"] or act
        # 功能动作按 story_function 检索（服务功能，与情绪无关，必须进候选池）
        want = _FUNCTION_ACTION.get(intent.get("story_function", ""), ())
        for a in lib.action:
            if any(k in a.tag for k in want) and a not in act:
                act.append(a)
        return exp or lib.expression, act or lib.action


# ---------------------------------------------------------------------------
# SequenceComposer（含 ExaggerateStyler + AudioTimelineAligner）
# ---------------------------------------------------------------------------


class SequenceComposer:
    def compose(self, intent: dict[str, Any], exp: list[ExpressionAtom],
                act: list[ActionAtom], *, target_sec: float) -> ComposedSequence:
        beats = intent.get("beats") or ["接收信息", "情绪转入", "态度外显"]
        fn = intent.get("story_function", "")
        exaggerate = bool(intent.get("exaggerate"))
        emos = list(_emotion_set(intent.get("emotion_target", "")))
        # 情绪弧：从起始情绪 → 目标情绪（"压抑→愤怒"这类）
        start_emo = emos[0] if emos else ""
        end_emo = emos[-1] if emos else start_emo

        # 功能动作优先：这一镜必须有的服务 story_function 的动作
        want = _FUNCTION_ACTION.get(fn, ())
        func_act = next((a for a in act if any(k in a.tag for k in want)), None)

        # 按节拍铺原子（表情链 + 动作链），对齐到 target_sec（AudioTimelineAligner）
        n = max(1, len(beats))
        step = round(target_sec / n, 2)
        expr_track, act_track, used = [], [], []
        for i, beat in enumerate(beats):
            # 沿情绪弧推进（压抑→绝望→愤怒），每拍换情绪 → 换原子，天然不重复凑秒
            emo_i = emos[min(i, len(emos) - 1)] if emos else end_emo
            # 循环取候选（避免同一原子重复凑秒）
            e = _pick(exp, emo_i, i)
            if e:
                expr_track.append({"t": round(i * step, 2), "beat": beat, "atom": e.to_dict()})
                used.append(e.atom_id)
            # 动作：转折/外显拍放功能动作，其余放情绪动作（同样循环取）
            a = func_act if (i >= n - 1 and func_act) else _pick(act, emo_i, i)
            if a:
                act_track.append({"t": round(i * step, 2), "beat": beat, "atom": a.to_dict()})
                used.append(a.atom_id)
        # 功能动作必须在场（信息/施压/反转类）
        if func_act and func_act.atom_id not in used:
            act_track.append({"t": round((n - 1) * step, 2), "beat": "功能",
                              "atom": func_act.to_dict()})
            used.append(func_act.atom_id)

        seq = ComposedSequence(
            shot_id=intent.get("shot_id", ""), story_function=fn,
            expression_track=expr_track, action_track=act_track,
            total_sec=target_sec, atoms_used=used)
        # 说话节拍（有台词才有）：停顿→加重→（强情绪）爆发；供 AudioDNA 语气，非逐帧口型
        if intent.get("is_speaking") or intent.get("line"):
            inten = _as_float(intent.get("intensity"), 0.6)
            sb: list[dict[str, Any]] = []
            if n >= 2:
                sb.append({"t": round((n - 1) * step * 0.4, 2), "type": "pause",
                           "note": "情绪转入前停顿"})
            sb.append({"t": round((n - 1) * step, 2), "type": "emphasis",
                       "note": "态度外显·关键词加重"})
            if inten >= 0.8 or fn in ("反转", "反击"):
                sb.append({"t": round((n - 1) * step, 2), "type": "burst",
                           "note": "强情绪爆发"})
            seq.speaking_beats = sb
        if exaggerate:
            self._exaggerate(seq)          # ExaggerateStyler：只改强度/顺序/反转
        return seq

    @staticmethod
    def _exaggerate(seq: ComposedSequence) -> None:
        """相似桥段做升级夸张：加强度、把功能动作提前一拍，不抄整段外部表演。"""
        for tr in (seq.expression_track, seq.action_track):
            for e in tr:
                e["atom"]["intensity"] = round(min(1.0, e["atom"]["intensity"] * 1.2), 3)
        seq.exaggerated = True


# ---------------------------------------------------------------------------
# CoherenceJudge + AtomGate —— 硬门
# ---------------------------------------------------------------------------


@dataclass
class AtomGateResult:
    verdict: str                       # PASS / REPAIR
    issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    def report(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "issues": self.issues}


class AtomGate:
    """硬门：无触发情绪变化 / 凑秒 / 与 story_function 无关的组合 → 打回。"""

    def review(self, seq: ComposedSequence, intent: dict[str, Any]) -> AtomGateResult:
        issues: list[dict[str, Any]] = []
        fn = seq.story_function
        emos = _emotion_set(intent.get("emotion_target", ""))
        neg_scene = bool(set(emos) & _NEG_EMO)

        # 1) 服务 story_function：需要功能动作的镜必须有对应功能动作
        want = _FUNCTION_ACTION.get(fn, ())
        if want:
            tags = [a["atom"]["tag"] for a in seq.action_track]
            if not any(any(k in t for k in want) for t in tags):
                issues.append(_i("serves_function",
                                 f"{fn}镜缺服务功能的动作（需含 {want}）"))
        # 2) 情绪变化必须有触发：情绪弧有变化 → 必须有"信息/动作结果"触发拍
        if len(emos) >= 2 and not seq.action_track:
            issues.append(_i("no_trigger", "情绪有转折却无动作触发"))
        # 3) 禁止无意义重复原子凑时长
        used = seq.atoms_used
        for aid in set(used):
            if used.count(aid) >= 3:
                issues.append(_i("padding", f"原子 {aid} 重复 {used.count(aid)} 次凑秒"))
        # 4) 负面场景禁止无触发正面情绪
        if neg_scene:
            for e in seq.expression_track:
                if any(p in e["atom"]["tag"] for p in _POSITIVE):
                    issues.append(_i("emotion_jump",
                                     f"负面场景出现正面表情：{e['atom']['tag']}"))
        # 5) 表情/动作链时间对齐（都在 [0,total] 内）
        for tr in (seq.expression_track, seq.action_track):
            for e in tr:
                if not (0 <= e["t"] <= seq.total_sec + 0.01):
                    issues.append(_i("timing", f"原子时刻 {e['t']} 越界"))
        return AtomGateResult("REPAIR" if issues else "PASS", issues)


# ---------------------------------------------------------------------------
# PerformanceAtomDNAService —— 编排
# ---------------------------------------------------------------------------


@dataclass
class AtomComposeResult:
    sequence: ComposedSequence
    gate: AtomGateResult

    @property
    def passed(self) -> bool:
        return self.gate.passed


class PerformanceAtomDNAService:
    """检索 → 组合 → 校验硬门 → 过门回流。总谱锁定后与资产并行，只执行导演意图。"""

    def __init__(self, library: AtomLibrary | None = None) -> None:
        self.lib = library or AtomLibrary()
        self.retriever = AtomRetriever()
        self.composer = SequenceComposer()
        self.gate = AtomGate()

    def compose(self, intent: dict[str, Any], *, target_sec: float = 3.0
                ) -> AtomComposeResult:
        exp, act = self.retriever.retrieve(self.lib, intent)
        seq = self.composer.compose(intent, exp, act, target_sec=target_sec)
        gate = self.gate.review(seq, intent)
        if gate.passed:
            self.lib.backflow(seq)          # BackflowWriter：成功组合回流
        return AtomComposeResult(sequence=seq, gate=gate)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

_NEG_EMO = {"愤怒", "绝望", "威胁", "恐惧", "无助", "屈辱", "压抑", "紧张", "焦虑"}
_EMO_ALIAS = {"压抑愤怒": ["压抑", "愤怒"], "压抑": ["绝望"], "紧张": ["恐惧"]}


def _emotion_set(target: str) -> list[str]:
    """把 '压抑→愤怒' 解析成**有序**情绪弧（起→止），确定性（不依赖 set 顺序）。"""
    if not target:
        return ["冷静"]
    out: list[str] = []

    def add(x: str) -> None:
        if x and x not in out:
            out.append(x)

    for p in target.replace("→", " ").replace("->", " ").split():
        p = p.strip()
        if p in _NEG_EMO or p in ("决绝", "冷静", "喜悦"):
            add(p)
        for k, vs in _EMO_ALIAS.items():
            if k in p:
                for v in vs:
                    add(v)
    return out or ["冷静"]


def _as_float(v: Any, default: float) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and ("high" in v or "强" in v):
        return 0.85
    return default


def _pick(atoms: list, emo: str, idx: int):
    """从匹配情绪的候选里循环取第 idx 个（避免重复同一原子凑秒）。"""
    cands = [a for a in atoms if a.emotion == emo] or list(atoms)
    return cands[idx % len(cands)] if cands else None


def _i(cat: str, msg: str) -> dict[str, Any]:
    return {"agent": "atom_gate", "category": cat, "message": msg}


__all__ = ["PerformanceAtomDNAService", "AtomLibrary", "AtomGate", "AtomGateResult",
           "AtomComposeResult", "AtomRetriever", "SequenceComposer"]
