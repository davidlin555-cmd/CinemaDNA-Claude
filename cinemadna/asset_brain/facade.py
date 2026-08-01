"""统一资产大脑门面 AssetBrainFacade (Phase 1)

对应接口文档 §5：给 Pipeline Orchestrator 与 Web 层的唯一入口。
把「查库 → 缺失建工单 → mock 生成 → Gate → 回流」编排成一次调用。

三大模型共享同一个 store 与同一个 Bundle，因此四元组、工单簿、
回流记录天然统一，Web 看板只需查这一个门面。

结果 outcome 取值：
- REUSED                : 内部库/Registry 命中，直接复用，不产生工单
- BACKFLOWED            : 生成 → Gate 通过 → 已回流（完整闭环）
- PENDING_HUMAN_REVIEW  : Gate 通过但需人工确认，等待 approve_gate
- REJECTED              : Gate 未通过（软性问题），可 regenerate 重生成
- REJECTED_HARD_BLOCK   : 命中 IdentityDNA 红线，**不可人工推翻**

`generation_plan_override`：三类请求都支持在入参里带这个字段，用于
（a）后续接入真实生成器时下发参数，（b）Phase 1 模拟违规场景以验证 Gate。
它只影响 generation_plan，**永远不能影响 requirement 里的红线策略**。
"""

from __future__ import annotations

from typing import Any, Callable

from .common import schemas
from .common.bundle import Bundle
from .common.quad import Quad
from .common.service_base import AssetServiceError
from .common.store import AssetBrainStore
from .common.workorder import Workorder, WorkorderStatus
from .identity_dna.gate import HardBlockOverrideError
from .identity_dna.service import IdentityDNAService
from .prop_dna.service import PropDNAService
from .scene_dna.service import SceneDNAService

OUTCOME_REUSED = "REUSED"
OUTCOME_BACKFLOWED = "BACKFLOWED"
OUTCOME_PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_REJECTED_HARD_BLOCK = "REJECTED_HARD_BLOCK"

#: 各类型资产的"结果资产 ID"提取方式
_ASSET_IDS: dict[str, Callable[[dict[str, Any]], list[str]]] = {
    "SCENE": lambda a: [a["asset_id"]],
    "IDENTITY": lambda a: [a["base_face_asset_id"]],
    "PROP": lambda a: sorted((a.get("asset_ids_by_state") or {}).values()),
}


class AssetBrainFacade:
    """统一调用入口。"""

    def __init__(
        self, store: AssetBrainStore | None = None, bundle: Bundle | None = None,
        *, image_backend=None, screen_backend=None,
    ) -> None:
        self.store = store if store is not None else AssetBrainStore()
        self.bundle = bundle
        if bundle is not None:
            bundle.ensure()
        # 配了 image_backend 且有 Bundle 时，三大资产都出真实图
        # 屏幕道具后端（screen_backend）须显式传入（生产入口构造），保持测试可控
        self.scene = SceneDNAService(self.store, bundle,
                                     image_backend=image_backend)
        self.identity = IdentityDNAService(self.store, bundle,
                                           image_backend=image_backend)
        self.prop = PropDNAService(self.store, bundle,
                                   image_backend=image_backend,
                                   screen_backend=screen_backend)
        # 等待人工审核的工单 → 已生成但尚未回流的资产
        self._pending_assets: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 三大入口
    # ------------------------------------------------------------------

    def request_scene(
        self, scene_req: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        req = self.scene.parse_requirement(scene_req, context)
        search = self.scene.search_internal(req, context)
        if search["found"]:
            return self._reuse_result(
                search, f"03_scene/{req['scene_id']}/reused.json", context
            )

        wo = self.scene.create_workorder(req, search, context)
        self._apply_plan_override(
            wo["workorder_id"], scene_req.get("generation_plan_override")
        )
        return self._generate_and_settle(wo["workorder_id"], context)

    def request_identity(
        self, char_req: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        req = self.identity.parse_requirement(char_req, context)
        search = self.identity.search_registry(req, context)
        if search["found"]:
            return self._reuse_result(
                search, f"02_cast/{req['character_id']}/reused.json", context
            )

        wo = self.identity.create_workorder(req, search, context)
        self._apply_plan_override(
            wo["workorder_id"], char_req.get("generation_plan_override")
        )
        return self._generate_and_settle(wo["workorder_id"], context)

    def request_prop(
        self,
        prop_req: dict[str, Any],
        context: Quad,
        state_timeline: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        req = self.prop.parse_requirement(prop_req, context)
        search = self.prop.search_internal(req, context)
        if search["found"]:
            return self._reuse_result(
                search, f"04_props/{req['prop_id']}/reused.json", context
            )

        wo = self.prop.create_workorder(req, search, context)
        override = dict(prop_req.get("generation_plan_override") or {})
        # 状态时间线存进 generation_plan，重生成时无需调用方再传一次
        if state_timeline:
            override["state_timeline"] = state_timeline
        self._apply_plan_override(wo["workorder_id"], override)
        return self._generate_and_settle(wo["workorder_id"], context)

    # ------------------------------------------------------------------
    # 重生成（Gate 判负后的唯一正当出路）
    # ------------------------------------------------------------------

    def regenerate(
        self,
        workorder_id: str,
        *,
        plan_override: dict[str, Any] | None = None,
        context: Quad | None = None,
    ) -> dict[str, Any]:
        """对 REJECTED 的工单重新生成并重新过闸（REJECTED → RUNNING → ...）。

        这是 Gate 判负后唯一被允许的前进方式：不能跳过 Gate，也不能
        绕开红线 —— 换汤不换药的重生成会被同一套 Gate 再拦一次。
        """
        wo = self._require_workorder(workorder_id)
        if wo.status is not WorkorderStatus.REJECTED:
            raise AssetServiceError(
                f"只有 REJECTED 的工单可以重生成，工单 {workorder_id} 当前 "
                f"{wo.status.value}"
            )
        if plan_override:
            wo.generation_plan.update(plan_override)
        wo.mark_running()
        return self._generate_and_settle(workorder_id, context or wo.quad)

    # ------------------------------------------------------------------
    # 内部：生成 → Gate → 收敛
    # ------------------------------------------------------------------

    def _require_workorder(self, workorder_id: str) -> Workorder:
        wo = self.store.get_workorder(workorder_id)
        if wo is None:
            raise AssetServiceError(f"工单不存在: {workorder_id}")
        return wo

    def _service_for(self, workorder_type: str) -> Any:
        return {
            "SCENE": self.scene,
            "IDENTITY": self.identity,
            "PROP": self.prop,
        }[workorder_type]

    def _apply_plan_override(
        self, workorder_id: str, override: dict[str, Any] | None
    ) -> None:
        """把调用方下发的生成参数合并进 generation_plan。

        只动 generation_plan，绝不触碰 requirement —— 红线策略（如
        must_multi_face_fusion）在 parse_requirement 里已被写死。
        """
        if not override:
            return
        self._require_workorder(workorder_id).generation_plan.update(override)

    def _reuse_result(
        self, search: dict[str, Any], relpath: str, context: Quad
    ) -> dict[str, Any]:
        """命中内部库：不建工单，但要在本剧 Bundle 内留一条复用凭证。

        否则一部"全靠复用"的剧，Bundle 里会空无一物，下游（Director /
        Render）无从知道本剧到底用了哪些全局资产，也无法事后审计。
        """
        if self.bundle is not None:
            self.bundle.write_json(
                relpath,
                {
                    "schema_version": schemas.SCHEMA_ASSET_REUSE,
                    "story_id": context.story_id,
                    "bundle_id": context.bundle_id,
                    "task_id": context.task_id,
                    "reused_asset_ids": search["matched_asset_ids"],
                    "similarity_scores": search["similarity_scores"],
                    "source": "global_asset_brain",
                    "reused_at": schemas.utc_now_iso(),
                },
            )
        return {
            "outcome": OUTCOME_REUSED,
            "workorder_id": None,
            "gate_id": None,
            "asset_ids": search["matched_asset_ids"],
            "similarity_scores": search["similarity_scores"],
        }

    def _generate_and_settle(
        self, workorder_id: str, context: Quad
    ) -> dict[str, Any]:
        """按类型跑 mock 生成 → Gate → 状态收敛。"""
        wo = self._require_workorder(workorder_id)
        kind = wo.workorder_type

        if kind == "SCENE":
            asset = self.scene.mock_generate(wo)
        elif kind == "IDENTITY":
            asset = self.identity.mock_multi_face_fusion(wo)
        else:
            asset = self.prop.mock_synthesize(wo)
            timeline = wo.generation_plan.get("state_timeline")
            if timeline:
                asset = self.prop.manage_continuity(asset, timeline)

        service = self._service_for(kind)
        gate = service.run_gate(asset, wo.requirement, context)
        return self._settle(
            service=service,
            workorder_id=workorder_id,
            gate=gate,
            asset=asset,
            context=context,
            asset_id_getter=_ASSET_IDS[kind],
        )

    def _settle(
        self,
        *,
        service: Any,
        workorder_id: str,
        gate: dict[str, Any],
        asset: dict[str, Any],
        context: Quad,
        asset_id_getter: Callable[[dict[str, Any]], list[str]],
    ) -> dict[str, Any]:
        """根据 Gate 结论决定：自动回流 / 挂起人工 / 判负。"""
        base = {
            "workorder_id": workorder_id,
            "gate_id": gate["gate_id"],
            "gate_scores": gate["scores"],
            "issues": gate["issues"],
        }
        hard_blocks = gate.get("hard_blocks") or []

        if hard_blocks:
            return {
                **base,
                "outcome": OUTCOME_REJECTED_HARD_BLOCK,
                "hard_blocks": hard_blocks,
                "asset_ids": [],
            }

        if not gate["passed"]:
            return {**base, "outcome": OUTCOME_REJECTED, "asset_ids": []}

        if gate["human_review_required"]:
            # 挂起：资产已生成，等 approve_gate 决定是否回流
            self._pending_assets[workorder_id] = asset
            return {
                **base,
                "outcome": OUTCOME_PENDING_HUMAN_REVIEW,
                "asset_ids": asset_id_getter(asset),
            }

        record = self._approve_and_backflow(
            service, workorder_id, asset, context, decided_by="auto_gate"
        )
        return {
            **base,
            "outcome": OUTCOME_BACKFLOWED,
            "asset_ids": asset_id_getter(asset),
            "backflow_record_id": record.get("backflow_id")
            or record.get("master_pack_id"),
        }

    def _approve_and_backflow(
        self,
        service: Any,
        workorder_id: str,
        asset: dict[str, Any],
        context: Quad,
        *,
        decided_by: str,
    ) -> dict[str, Any]:
        wo = self._require_workorder(workorder_id)
        wo.mark_approved()
        if wo.gate_report is not None:
            wo.gate_report["decided_by"] = decided_by
            wo.gate_report["decided_at"] = wo.updated_at
            # 决策结果必须回写 Bundle，否则盘上留下的是"未决"版本，无法审计
            if self.bundle is not None:
                sub = {
                    "SCENE": "scene_gate",
                    "IDENTITY": "identity_gate",
                    "PROP": "prop_gate",
                }[wo.workorder_type]
                self.bundle.write_json(
                    f"11_review/{sub}/{wo.gate_report['gate_id']}.json", wo.gate_report
                )

        if isinstance(service, IdentityDNAService):
            master_pack = service.build_master_pack(asset)
            return service.backflow(master_pack, wo, context)
        return service.backflow(asset, wo, context)

    # ------------------------------------------------------------------
    # 人工审核入口（Web 调用）
    # ------------------------------------------------------------------

    def approve_gate(
        self, gate_id: str, human_decision: dict[str, Any]
    ) -> dict[str, Any]:
        """人工审核 Gate。

        红线保护：报告含 hard_blocks 时，**无论 human_decision 写什么**
        都抛 HardBlockOverrideError —— 单一真人克隆不存在"人工放行"这条路。
        """
        report = self.store.get_gate_report(gate_id)
        if report is None:
            raise AssetServiceError(f"Gate Report 不存在: {gate_id}")

        if report.get("hard_blocks"):
            raise HardBlockOverrideError(
                f"Gate {gate_id} 命中硬性拦截 {report['hard_blocks']}，"
                f"禁止任何人工推翻（IdentityDNA 肖像权红线）"
            )

        workorder_id = report["workorder_id"]
        wo = self._require_workorder(workorder_id)

        decided_by = human_decision.get("decided_by", "human")
        approved = bool(human_decision.get("approved"))

        if not approved:
            if wo.status is WorkorderStatus.GATE_REVIEW:
                wo.mark_rejected()
            self._pending_assets.pop(workorder_id, None)
            return {
                "outcome": OUTCOME_REJECTED,
                "workorder_id": workorder_id,
                "gate_id": gate_id,
                "decided_by": decided_by,
            }

        if not report.get("passed"):
            raise AssetServiceError(
                f"Gate {gate_id} 未通过（issues={report.get('issues')}），"
                f"不允许人工审批放行，请重新生成"
            )

        asset = self._pending_assets.pop(workorder_id, None)
        if asset is None:
            raise AssetServiceError(f"工单 {workorder_id} 没有待回流的资产")

        record = self._approve_and_backflow(
            self._service_for(wo.workorder_type),
            workorder_id,
            asset,
            wo.quad,
            decided_by=decided_by,
        )
        return {
            "outcome": OUTCOME_BACKFLOWED,
            "workorder_id": workorder_id,
            "gate_id": gate_id,
            "decided_by": decided_by,
            "backflow_record_id": record.get("backflow_id")
            or record.get("master_pack_id"),
        }

    # ------------------------------------------------------------------
    # 查询（供 Web 看板）
    # ------------------------------------------------------------------

    def get_workorder_status(self, workorder_id: str) -> dict[str, Any]:
        return self._require_workorder(workorder_id).to_dict()

    def list_workorders(
        self,
        *,
        story_id: str | None = None,
        workorder_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            w.to_dict()
            for w in self.store.list_workorders(
                story_id=story_id, workorder_type=workorder_type, status=status
            )
        ]

    def stats(self) -> dict[str, Any]:
        return self.store.stats()
