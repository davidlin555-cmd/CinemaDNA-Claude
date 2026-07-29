"""LLM 叙事作者 —— 产出**高密度节拍表**，不是低密度长镜。

商业短剧 = 单位时间戏剧信息密度更高。本模块用 LLM 一次性为整部短剧写**节拍表**：
8–14 个紧凑节拍，每拍 2–4 秒、一个新发展（信息/动作/反应/权力变化），并强制包含
打断 / 目标受阻 / 主动反击，结尾 3–5 秒反转或强悬念。每拍映射为一个短镜。

一次调用写全片 → 全局连贯 + 高密度。文本生成，几分钱/部。client 可注入（测试用假
实现，不联网）。输出严格 JSON，解析后二次校验（属主/重复/结构节拍/时长），不合格
带反馈重试一次。
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

import config

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_BEAT_TYPES = ("setup", "info", "action", "reaction", "power_shift",
               "interruption", "obstruction", "counterattack",
               "reversal", "cliffhanger")


class NarrativeAuthorError(RuntimeError):
    pass


CallFn = Callable[[str, str], str]


def _anthropic_call(model: str) -> CallFn:
    import anthropic
    client = anthropic.Anthropic(api_key=config.require("ANTHROPIC_API_KEY"))

    def call(system: str, user: str) -> str:
        msg = client.messages.create(
            model=model, max_tokens=8192, system=system,
            messages=[{"role": "user", "content": user}])
        return "".join(getattr(b, "text", "") for b in msg.content)
    return call


_SYSTEM = (
    "你是爆款竖屏商业短剧编剧。核心：**单位时间戏剧信息密度极高**——短剧不是把长电影"
    "压成少量长镜，而是每 2–4 秒一个新发展。硬规则：\n"
    "1. 全片 8–14 个节拍，每拍 2–4 秒，一个新信息/动作/反应/权力变化。\n"
    "2. 必须含：打断(interruption)、目标受阻(obstruction)、主动反击(counterattack)；"
    "末拍必须反转或强悬念(reversal/cliffhanger)。\n"
    "3. **前 3 秒钩子要有即时视觉冲击/威胁**（如上门砸门、限时催款、当面羞辱），"
    "不要用'回家/看手机'这种平淡开场。\n"
    "4. **反转前先给身体冲突或绝望谷**（被抓手腕/被逼到墙角/被当众羞辱），让观众有"
    "触感和爽感落点，禁止'撕纸→掏证据'式的空翻转。\n"
    "5. **竖屏近场**：关键证物（缴费单/短信/协议/转账）要安排**大特写**节拍并在描述里"
    "点明要强调的金额/威胁词；走位靠近镜头制造逼迫感。\n"
    "6. 说话人只能是该场在场角色，禁止错角；全片台词不重复、不凑字数、贴情绪、"
    "口语短促（≤18字）、中文。节拍因果相连、层层递进（受阻→身体冲突→反击→反转）。\n"
    "7. **镜头功能压缩**：尽量让单个镜头承载**镜内状态变化**（一个镜头里发生转折），"
    "用 internal_shift 写清'起始状态→中途转折→结束状态'（如'读账单→手机突响→僵住'）。"
    "多用镜内反转能在更短时间塞进更多戏（爽感更密）。\n"
    "beat.type：setup/info/action/reaction/power_shift/interruption/obstruction/"
    "counterattack/reversal/cliffhanger。纯动作/反应拍 line 可空串。\n"
    "只输出 JSON，不要解释。"
)


class LLMNarrativeAuthor:
    """LLM 高密度节拍表作者。"""

    def __init__(self, *, call: CallFn | None = None, model: str | None = None) -> None:
        self.model = model or config.get("NARRATIVE_MODEL", "") or _DEFAULT_MODEL
        self._call = call

    def available(self) -> bool:
        return self._call is not None or config.has("ANTHROPIC_API_KEY")

    def _ensure_call(self) -> CallFn:
        if self._call is None:
            self._call = _anthropic_call(self.model)
        return self._call

    # -- 提示词 ---------------------------------------------------------

    @staticmethod
    def _build_user(theme: str, logline: str, genre: str,
                    characters: list[dict], scenes: list[dict],
                    target_sec: float) -> str:
        chars = [{"character_id": c.get("character_id"),
                  "name": c.get("name") or c.get("character_id"),
                  "role": c.get("role") or c.get("archetype") or ""}
                 for c in characters]
        scene_brief = [{
            "scene_id": s.get("scene_id"),
            "beat_type": s.get("beat_type"),
            "beat_summary": s.get("beat_summary"),
            "mood": s.get("mood"),
            "location": s.get("location_description") or s.get("location_key"),
            "characters": list(s.get("characters") or []),
        } for s in scenes]
        n_scenes = max(1, len(scene_brief))
        beats_lo, beats_hi = max(8, n_scenes * 3), max(10, n_scenes * 4)
        spec = {
            "theme": theme, "logline": logline, "genre": genre,
            "target_seconds": round(target_sec),
            "total_beats_range": [beats_lo, beats_hi],
            "characters": chars, "scenes": scene_brief,
            "output_schema": {
                "scenes": [{
                    "scene_id": "…（对应输入 scene_id）",
                    "cause": "本场因何发生（承接上场）",
                    "effect": "本场导致的结果（引出下场）",
                    "entry_state": "开场处境/情绪",
                    "exit_state": "结束处境/情绪",
                    "emotional_tone": "本场主导情绪",
                    "beats": [{
                        "type": "setup/info/action/reaction/power_shift/interruption/"
                                "obstruction/counterattack/reversal/cliffhanger",
                        "seconds": "2-4 的整数",
                        "description": "这拍发生什么（一个新发展）",
                        "internal_shift": "镜内状态变化'起→转→结'（尽量有；无则空串）",
                        "character_id": "说话人（该场角色之一）或 null",
                        "line": "≤18字中文台词，纯动作拍可空串",
                        "emotion": "这拍情绪",
                    }],
                }]
            },
        }
        return ("按 spec 写整部短剧的高密度节拍表。把 8–14 个节拍分配到各场，每场"
                "beats 依时间顺序；全片满足结构要求（打断/受阻/反击/结尾反转）。\n\n"
                + json.dumps(spec, ensure_ascii=False))

    # -- 解析 + 校验 ----------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise NarrativeAuthorError("LLM 未返回 JSON")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise NarrativeAuthorError(f"LLM JSON 解析失败: {e}") from e

    @staticmethod
    def _validate(authored: dict, scenes: list[dict]) -> list[str]:
        issues: list[str] = []
        by_id = {s["scene_id"]: s for s in scenes}
        got = {sc.get("scene_id"): sc for sc in authored.get("scenes") or []}
        seen_lines: dict[str, str] = {}
        all_types: list[str] = []
        total_beats = 0
        ordered_last_type = ""
        for sid, src in by_id.items():
            sc = got.get(sid)
            if not sc:
                issues.append(f"{sid}: 缺少场次")
                continue
            for field in ("cause", "effect", "entry_state", "exit_state"):
                if not (sc.get(field) or "").strip():
                    issues.append(f"{sid}: 缺 {field}")
            allowed = set(src.get("characters") or [])
            beats = sc.get("beats") or []
            if not beats:
                issues.append(f"{sid}: 无节拍")
            for b in beats:
                total_beats += 1
                bt = (b.get("type") or "").lower()
                all_types.append(bt)
                ordered_last_type = bt or ordered_last_type
                cid = b.get("character_id")
                txt = (b.get("line") or "").strip()
                if cid and allowed and cid not in allowed:
                    issues.append(f"{sid}: 台词属主 {cid} 不在本场角色 {sorted(allowed)}")
                if txt:
                    if txt in seen_lines:
                        issues.append(f"{sid}: 台词重复「{txt}」(与 {seen_lines[txt]})")
                    else:
                        seen_lines[txt] = sid
        # 全片密度/结构
        if total_beats < 8:
            issues.append(f"节拍太少（{total_beats}<8），密度不足")
        present = set(all_types)
        for t in ("interruption", "obstruction", "counterattack"):
            if t not in present:
                issues.append(f"缺结构节拍：{t}")
        if ordered_last_type not in ("reversal", "cliffhanger"):
            issues.append(f"结尾非反转/悬念（末拍 type={ordered_last_type}）")
        return issues

    # -- 主入口 ---------------------------------------------------------

    def author_episodes(self, episodes: list[dict], outline: dict,
                        *, theme: str, logline: str = "", genre: str = "",
                        characters: list[dict] | None = None,
                        target_sec: float = 30.0,
                        feedback: list[str] | None = None) -> list[dict]:
        """把 LLM 作者化的高密度节拍写回 episodes（每场带 beats + narrative + 扁平
        dialogue 供下游兼容）。`feedback`=上一轮责编意见，会被针对性改进。
        失败抛 NarrativeAuthorError，由上层降级模板。"""
        call = self._ensure_call()
        all_scenes = [s for ep in episodes for s in ep.get("scenes") or []]
        chars = characters or outline.get("characters") or []
        user = self._build_user(theme, logline, genre, chars, all_scenes, target_sec)
        if feedback:
            user += ("\n\n【上一稿责编意见，必须针对性改进后重写】：\n"
                     + "\n".join(f"- {n}" for n in feedback))

        authored, issues = None, ["首次"]
        for attempt in range(2):
            text = call(_SYSTEM, user if attempt == 0
                        else user + f"\n\n上次问题，请修正：{issues}")
            authored = self._extract_json(text)
            issues = self._validate(authored, all_scenes)
            if not issues:
                break
        if issues:
            raise NarrativeAuthorError(f"LLM 节拍表校验未过（重试后）：{issues[:6]}")

        got = {sc["scene_id"]: sc for sc in authored["scenes"]}
        out = []
        for ep in episodes:
            new_scenes = []
            for s in ep.get("scenes") or []:
                a = got.get(s["scene_id"]) or {}
                beats = []
                dialogue = []
                for b in a.get("beats") or []:
                    seconds = b.get("seconds")
                    try:
                        seconds = float(seconds)
                    except (TypeError, ValueError):
                        seconds = 3.0
                    seconds = round(min(4.0, max(2.0, seconds)), 1)
                    line = (b.get("line") or "").strip()
                    cid = b.get("character_id")
                    beat = {"type": (b.get("type") or "info").lower(),
                            "seconds": seconds,
                            "description": b.get("description", ""),
                            "internal_shift": (b.get("internal_shift") or "").strip(),
                            "character_id": cid if line else None,
                            "line": line, "emotion": b.get("emotion", "")}
                    beats.append(beat)
                    if line and cid:
                        dialogue.append({"character_id": cid, "line": line,
                                         "emotion": b.get("emotion", "")})
                new_scenes.append({
                    **s, "beats": beats, "dialogue": dialogue,
                    "narrative": {
                        "cause": a.get("cause", ""), "effect": a.get("effect", ""),
                        "entry_state": a.get("entry_state", ""),
                        "exit_state": a.get("exit_state", ""),
                        "emotional_tone": a.get("emotional_tone", s.get("mood", "")),
                        "authored_by": "llm",
                    },
                })
            out.append({**ep, "scenes": new_scenes})
        return out


__all__ = ["LLMNarrativeAuthor", "NarrativeAuthorError", "CallFn"]
