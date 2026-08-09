"""DirectorDNA · Phase 3 Service —— 把剧本变成可渲染的镜头合约

对应多智能体文档 §2.6。流程：

    shooting_script + 已过 Gate 的资产
      → Shot Strategy（排镜头表 + 情绪曲线）
      → Shot Contract Generator（一镜头一合约，绑死资产 hash）
      → Continuity Ledger（记账）
      → Continuity Director（查冲突，有问题就打回）

**资产解析是硬约束**：合约里每一个场景/人物/道具引用，都必须能在三大库里
查到已回流的记录并带上 asset_hash 与 gate_passed。查不到就直接抛错 ——
Director 宁可不签合约，也不签一张"资产还没生成"的空头合约。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.bundle import Bundle
from cinemadna.asset_brain.common.quad import Quad
from cinemadna.asset_brain.common.store import AssetBrainStore

from .continuity import build_ledger, check_continuity
from .shot_contract import new_shot_contract
from .strategy import build_emotion_curve, plan_scene_shots


class DirectorError(RuntimeError):
    """导演阶段错误（资产未就绪、剧本缺失等）。"""


@dataclass
class DirectorResult:
    """一次导演作业的产物。"""

    strategy: dict[str, Any]
    contracts: list[dict[str, Any]]
    ledger: dict[str, Any]
    continuity_report: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def shot_count(self) -> int:
        return len(self.contracts)

    @property
    def total_duration_sec(self) -> float:
        return round(sum(c["duration_sec"] for c in self.contracts), 1)

    @property
    def passed(self) -> bool:
        return self.continuity_report["passed"]

    def artifacts(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "06_shots/shot_strategy.json": self.strategy,
            "06_shots/continuity_ledger.json": self.ledger,
            f"11_review/continuity_check/{self.ledger['story_id']}.json":
                self.continuity_report,
        }
        for c in self.contracts:
            out[f"06_shots/contracts/{c['shot_id']}.json"] = c
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "shot_count": self.shot_count,
            "total_duration_sec": self.total_duration_sec,
            "shot_types": sorted({c["shot_type"] for c in self.contracts}),
            "continuity_passed": self.passed,
            "continuity_issues": self.continuity_report["issues"],
            "warnings": self.warnings,
        }


class DirectorDNAService:
    """DirectorDNA Phase 3 Service。"""

    def __init__(
        self, store: AssetBrainStore, bundle: Bundle | None = None
    ) -> None:
        self.store = store
        self.bundle = bundle

    # ------------------------------------------------------------------
    # 资产解析（合约的地基）
    # ------------------------------------------------------------------

    def resolve_scene_asset(self, scene_id: str, asset_id: str | None) -> dict[str, Any]:
        if not asset_id:
            raise DirectorError(f"场景 {scene_id} 没有绑定资产，先跑资产匹配")
        rec = self.store.scene_atoms.get(asset_id)
        if rec is None:
            raise DirectorError(
                f"场景 {scene_id} 的资产 {asset_id} 不在 SceneDNA 库里（未回流？）"
            )
        return {
            "scene_id": scene_id,
            "asset_id": asset_id,
            "asset_hash": rec.get("asset_hash"),
            "gate_passed": bool(rec.get("gate_passed")),
            "asset_type": rec.get("asset_type", "COMPOSED_LAYOUT"),
            # 标签带上，Render 组装提示词时要用（地点/时间/情绪）
            "tags": list(rec.get("tags") or []),
            # 真实场景图（绝对路径，跨剧复用也能取到）
            "scene_image": rec.get("scene_image_abspath") or rec.get("scene_image"),
            "is_real_image": bool(rec.get("is_real_image")),
        }

    def resolve_character_asset(self, character_id: str) -> dict[str, Any]:
        pack = self.store.character_registry.get(character_id)
        if pack is None:
            raise DirectorError(
                f"人物 {character_id} 不在 Character Registry 里（未过 Gate 或未回流）"
            )
        return {
            "character_id": character_id,
            "master_pack_id": pack.get("master_pack_id"),
            "base_face_asset_id": pack.get("base_face_asset_id"),
            # 真实人脸图路径（image2video 参考）；mock 身份为 None
            "base_face_image": pack.get("base_face_image"),
            "is_real_image": bool(pack.get("is_real_image")),
            "asset_hash": pack.get("asset_hash"),
            "gate_passed": bool(pack.get("gate_passed")),
            "age_line": dict(pack.get("age_line") or {}),
        }

    def resolve_prop_asset(self, prop_id: str, state: str) -> dict[str, Any]:
        rec = self.store.prop_library.get(prop_id)
        if rec is None:
            raise DirectorError(f"道具 {prop_id} 不在 PropDNA 库里（未回流？）")
        by_state = dict(rec.get("asset_ids_by_state") or {})
        if state not in by_state:
            raise DirectorError(
                f"道具 {prop_id} 没有「{state}」状态的版本（已有 {sorted(by_state)}），"
                f"跨镜头会穿帮"
            )
        imgs_abs = dict(rec.get("prop_images_abspath") or {})
        imgs_rel = dict(rec.get("prop_images_by_state") or {})
        return {
            "prop_id": prop_id,
            "state": state,
            "asset_id": by_state[state],
            "asset_hash": rec.get("asset_hash"),
            "gate_passed": bool(rec.get("gate_passed")),
            # 真实道具图（该状态；绝对路径跨剧可取）
            "prop_image": imgs_abs.get(state) or imgs_rel.get(state),
            "is_real_image": bool(rec.get("is_real_image")),
        }

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def run(
        self,
        shooting_script: dict[str, Any],
        *,
        story_id: str,
        bundle_id: str,
        scene_bindings: dict[str, str],
        task_id_factory: Callable[[], str],
        plan_shots: list[dict[str, Any]] | None = None,
    ) -> DirectorResult:
        """排镜头 → 签合约 → 记账 → 查连续性。

        `scene_bindings`：scene_id → 场景资产 asset_id（资产阶段产出的绑定表）
        `task_id_factory`：每个镜头分配一个 task_id（渲染下载按它隔离）
        """
        scenes = [s for ep in shooting_script.get("episodes") or [] for s in ep.get("scenes") or []]
        if not scenes:
            raise DirectorError("剧本里没有任何场景")

        seed = shooting_script.get("script_id", story_id)
        warnings: list[str] = []
        contracts: list[dict[str, Any]] = []
        all_shots: list[dict[str, Any]] = []
        order = 1
        # 总谱发单：若给了锁定总谱镜头化的 shots，按场分组、下游只执行（不再重排分镜）
        shots_by_scene: dict[str, list] = {}
        if plan_shots is not None:
            for sh in plan_shots:
                shots_by_scene.setdefault(sh["scene_id"], []).append(sh)

        for scene in scenes:
            if plan_shots is not None:
                shots = shots_by_scene.get(scene["scene_id"], [])
            else:
                shots = plan_scene_shots(scene, seed=seed, shot_index_start=1)
            all_shots.extend(shots)
            scene_ref = self.resolve_scene_asset(
                scene["scene_id"], scene_bindings.get(scene["scene_id"])
            )
            if scene.get("production_difficulty") == "hard":
                warnings.append(
                    f"{scene['scene_id']} 被标记为高难度场景，渲染成功率存疑"
                )

            for shot in shots:
                char_refs = [self.resolve_character_asset(c) for c in shot["characters"]]
                prop_refs = [
                    self.resolve_prop_asset(pid, shot["prop_states"].get(pid, ""))
                    for pid in shot["props"]
                ]
                # task_id **按 shot_id 稳定派生**（不再每次随机）：重渲/重签时同一镜
                # 拿到同一个 task_id → 云端任务缓存命中 → 只重下载不重复提交扣费。
                quad = Quad(
                    story_id=story_id,
                    bundle_id=bundle_id,
                    task_id=f"task_{shot['shot_id']}",
                    asset_hash=None,
                )
                _ = task_id_factory   # 保留签名兼容；稳定 task_id 由 shot_id 决定
                contract = new_shot_contract(
                    shot_id=shot["shot_id"],
                    scene_id=shot["scene_id"],
                    episode_id=shot["episode_id"] or scene.get("episode_id", ""),
                    order=order,
                    shot_type=shot["shot_type"],
                    quad=quad,
                    camera=shot["camera"],
                    duration_sec=shot["duration_sec"],
                    emotion=shot["emotion"],
                    scene_asset=scene_ref,
                    character_assets=char_refs,
                    prop_assets=prop_refs,
                    dialogue=shot["dialogue"],
                    difficulty=shot["difficulty"],
                    notes=scene.get("beat_summary", ""),
                )
                # 叙事层：每镜功能 + 继承本场因果/进出状态 + 节拍类型
                # （供 NarrativeContinuityQA / DensityQA）
                contract["story_function"] = shot.get("story_function", "")
                contract["internal_shift"] = shot.get("internal_shift", "")
                contract["narrative"] = dict(shot.get("narrative") or {})
                contract["beat_type"] = shot.get("beat_type", "")
                # 总谱字段：可执行动作 + 表演意图 + 道具接触 + **plan_hash 追溯**
                if shot.get("action_beats"):
                    contract["action_beats"] = shot["action_beats"]
                if shot.get("performance_intent"):
                    contract["performance_intent"] = shot["performance_intent"]
                if shot.get("prop_usage"):
                    contract["prop_usage"] = shot["prop_usage"]
                if shot.get("plan_hash"):
                    contract["director_plan_hash"] = shot["plan_hash"]
                contracts.append(contract)
                order += 1

        # PostBrain 剪辑节奏：按节拍编排剪辑速度（钩子紧/向高潮加速/悬念留白）。
        # 只对内容驱动（有 beat_type）的合约生效，模板合约不动。DRAFT 态改时长无 hash 问题。
        if any(c.get("beat_type") for c in contracts):
            from postbrain.rhythm import apply_editing_rhythm
            apply_editing_rhythm(contracts)

        # 补齐 Shot Production Contract V5 子合同（story_function/voice/route/qa）。
        # 在 DRAFT 态写入，随 PerformanceDNA 签发一起进 contract_hash。
        from prerender.contract_v5 import enrich_contract_v5
        characters_by_id = {
            c["character_id"]: c for c in shooting_script.get("characters") or []}
        for c in contracts:
            enrich_contract_v5(c, characters_by_id=characters_by_id)

        strategy = {
            "schema_version": schemas.SCHEMA_SHOT_STRATEGY,
            "story_id": story_id,
            "script_id": shooting_script.get("script_id"),
            "shot_count": len(contracts),
            "total_duration_sec": round(sum(c["duration_sec"] for c in contracts), 1),
            "shots": [
                {
                    "shot_id": c["shot_id"], "scene_id": c["scene_id"],
                    "order": c["order"], "shot_type": c["shot_type"],
                    "duration_sec": c["duration_sec"],
                    "camera": c["camera"],
                }
                for c in contracts
            ],
            "emotion_curve": build_emotion_curve(all_shots),
            "generated_at": schemas.utc_now_iso(),
        }

        ledger = build_ledger(contracts, story_id=story_id, bundle_id=bundle_id)
        report = check_continuity(
            ledger,
            prop_state_order={
                p["prop_id"]: list(p.get("states_needed") or [])
                for p in shooting_script.get("props") or []
            },
            scene_prop_states={
                s["scene_id"]: dict(s.get("prop_states") or {}) for s in scenes
            },
        )

        return DirectorResult(
            strategy=strategy,
            contracts=contracts,
            ledger=ledger,
            continuity_report=report,
            warnings=warnings,
        )
