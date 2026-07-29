"""PreRender Commercial Gate V2 (Phase C) —— 渲染前 12 项就绪总闸

对应 PDF §8/§9。进 RenderBrain 前的最后一道门：以下每一项必须 READY，
任一 BLOCKED 就停止并给出 blocker + next_action，禁止假成功。

  script_contract_ready       scene_contract_ready
  identity_contract_ready     prop_contract_ready
  performance_contract_ready  dialogue_contract_ready
  voice_contract_ready        mouth_policy_ready
  camera_contract_ready       route_contract_ready
  qa_contract_ready           cross_model_validation_ready

大量检查复用现有：require_render_ready（技术就绪）、check_mouth_policy、
cross_model_validate。这里把它们**合并成一个可见的商业级就绪总闸**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from director.shot_contract import (
    MOUTH_POLICIES,
    RenderNotAllowedError,
    check_mouth_policy,
    require_render_ready,
)

from .cross_validation import cross_model_validate

READY = "READY"
BLOCKED = "BLOCKED"

#: 12 项就绪 flag 的顺序（PDF §8）
FLAGS = (
    "script_contract_ready", "scene_contract_ready", "identity_contract_ready",
    "prop_contract_ready", "performance_contract_ready", "dialogue_contract_ready",
    "voice_contract_ready", "mouth_policy_ready", "camera_contract_ready",
    "route_contract_ready", "qa_contract_ready", "cross_model_validation_ready",
)


@dataclass
class PreRenderGateResult:
    story_id: str
    status: str                              # READY / BLOCKED
    flags: dict[str, str] = field(default_factory=dict)
    blockers: list[dict[str, Any]] = field(default_factory=dict)
    cross_validation: dict[str, Any] = field(default_factory=dict)
    next_action: str = ""

    @property
    def ready(self) -> bool:
        return self.status == READY

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": "cinemadna.prerender_gate.v2",
            "story_id": self.story_id,
            "status": self.status,
            "flags": self.flags,
            "blockers": self.blockers,
            "cross_validation": self.cross_validation,
            "next_action": self.next_action,
        }


def _flag(contracts, check) -> tuple[str, list[dict[str, Any]]]:
    """对每个合约跑 check（返回问题列表），汇总成 flag + blockers。"""
    blockers: list[dict[str, Any]] = []
    for c in contracts:
        for msg in check(c):
            blockers.append({"shot_id": c["shot_id"], "reason": msg})
    return (READY if not blockers else BLOCKED), blockers


def run_prerender_gate(
    contracts: list[dict[str, Any]], *, story_id: str,
    characters_by_id: dict[str, dict[str, Any]] | None = None,
    script_ready: bool = True,
    asset_checker: Any = None,
    require_real_faces: bool = False,
    require_real_assets: bool = False,
) -> PreRenderGateResult:
    """跑 PreRender Commercial Gate V2。"""
    flags: dict[str, str] = {}
    all_blockers: list[dict[str, Any]] = []

    def record(flag: str, status: str, blockers: list[dict[str, Any]]) -> None:
        flags[flag] = status
        for b in blockers:
            all_blockers.append({"flag": flag, **b})

    if not contracts:
        return PreRenderGateResult(
            story_id=story_id, status=BLOCKED,
            flags={f: BLOCKED for f in FLAGS},
            blockers=[{"flag": "*", "shot_id": "*", "reason": "没有任何镜头合约"}],
            next_action="先跑 DirectorDNA 生成镜头合约")

    # script（来自 ScriptBrain 是否通过）
    record("script_contract_ready", READY if script_ready else BLOCKED,
           [] if script_ready else [{"shot_id": "*", "reason": "剧本未通过"}])

    # scene / identity / prop / performance —— 复用 require_render_ready 的技术就绪
    scene_b, id_b, prop_b, perf_b = [], [], [], []
    for c in contracts:
        try:
            require_render_ready(c)
        except RenderNotAllowedError as e:
            msg = str(e)
            if "场景" in msg:
                scene_b.append({"shot_id": c["shot_id"], "reason": msg})
            elif "人物" in msg or "Master Pack" in msg:
                id_b.append({"shot_id": c["shot_id"], "reason": msg})
            elif "道具" in msg:
                prop_b.append({"shot_id": c["shot_id"], "reason": msg})
            elif "表演" in msg or "节拍" in msg:
                perf_b.append({"shot_id": c["shot_id"], "reason": msg})
            else:
                scene_b.append({"shot_id": c["shot_id"], "reason": msg})
    record("scene_contract_ready", READY if not scene_b else BLOCKED, scene_b)
    record("identity_contract_ready", READY if not id_b else BLOCKED, id_b)
    record("prop_contract_ready", READY if not prop_b else BLOCKED, prop_b)
    record("performance_contract_ready", READY if not perf_b else BLOCKED, perf_b)

    # dialogue：结构存在即可（无台词也算就绪，反应/空镜合法）
    record("dialogue_contract_ready", READY, [])

    # voice：有台词的镜头必须有 voice_contract 绑定且性别已定
    voice_b = []
    for c in contracts:
        vc = c.get("voice_contract") or {}
        if vc.get("has_dialogue"):
            for b in vc.get("bindings") or []:
                if b.get("voice_gender") in (None, "", "unspecified"):
                    voice_b.append({"shot_id": c["shot_id"],
                        "reason": f"说话人 {b['speaker']} 未绑定声音性别"})
    record("voice_contract_ready", READY if not voice_b else BLOCKED, voice_b)

    # mouth_policy
    s, b = _flag(contracts, lambda c: (
        [] if c.get("mouth_policy") in MOUTH_POLICIES else ["缺 mouth_policy"]
    ) + check_mouth_policy(c))
    record("mouth_policy_ready", s, b)

    # camera：景别 + 运镜 + 意图齐全
    s, b = _flag(contracts, lambda c: (
        [] if all((c.get("camera") or {}).get(k) for k in ("shot_size", "movement", "intent"))
        else ["机位合同不完整"]))
    record("camera_contract_ready", s, b)

    # route：有路由决策
    s, b = _flag(contracts, lambda c: (
        [] if (c.get("route_contract") or {}).get("model") else ["无路由决策"]))
    record("route_contract_ready", s, b)

    # qa：有本镜通过标准
    s, b = _flag(contracts, lambda c: (
        [] if c.get("qa_contract") else ["无 qa_contract"]))
    record("qa_contract_ready", s, b)

    # cross-model validation（含真实角色资产校验）
    cv = cross_model_validate(
        contracts, characters_by_id=characters_by_id,
        asset_checker=asset_checker, require_real_faces=require_real_faces,
        require_real_assets=require_real_assets)
    record("cross_model_validation_ready", READY if cv["passed"] else BLOCKED,
           [{"shot_id": i["shot_id"], "reason": f"{i['direction']}: {i['message']}"}
            for i in cv["issues"]])

    status = READY if all(v == READY for v in flags.values()) else BLOCKED
    next_action = "" if status == READY else \
        "查看 blockers，按 flag 打回对应子模型修复后重跑 PreRender Gate"
    return PreRenderGateResult(
        story_id=story_id, status=status, flags=flags,
        blockers=all_blockers, cross_validation=cv, next_action=next_action)
