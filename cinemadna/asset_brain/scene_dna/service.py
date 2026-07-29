"""SceneDNA 自然场景大模型 · Phase 1 Service

对应接口文档 §2.1。流程：

    剧本场景 → parse_requirement → search_internal(SceneDNA 库)
      ├─ 命中 → 直接引用（不建工单）
      └─ 未命中 → create_workorder → mock_generate（原子提取 + 重新布局）
                  → run_gate（自然度 / 匹配度 / 可用性 / 版权）
                  → backflow（回流 SceneDNA 库，供下一部剧直接复用）

Phase 1 不做真实网络搜索与 3D 重建：mock_generate 只产出结构正确、
数值可复现的布局描述，真实能力在后续阶段替换。
"""

from __future__ import annotations

from typing import Any

from ..common import schemas
from ..common.gate import GateReport
from ..common.hashing import asset_hash_of, stable_score
from ..common.quad import Quad
from ..common.service_base import AssetServiceError, BaseAssetService
from ..common.workorder import Workorder, WorkorderStatus, new_workorder
from . import workorder as wo_ext
from .gate import run_scene_gate
from .workorder import scene_prompt

#: 内部库命中判定阈值（标签重合度）
INTERNAL_HIT_THRESHOLD = 0.60


class SceneDNAService(BaseAssetService):
    """SceneDNA Phase 1 Service（mock 生成 + 真实 Gate）。"""

    workorder_type = schemas.WORKORDER_TYPE_SCENE
    workorder_prefix = "wo_scenedna"

    def __init__(self, store=None, bundle=None, *, image_backend=None) -> None:
        super().__init__(store, bundle)
        #: 可选真实图像后端（Together FLUX）。None=mock（不出图）。
        self.image_backend = image_backend

    # ------------------------------------------------------------------
    # 1. 需求解析
    # ------------------------------------------------------------------

    def parse_requirement(
        self, shooting_script_scene: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        """从剧本场景提取标准化需求（接口文档 §2.2）。"""
        self._check_context(context)
        if not shooting_script_scene.get("scene_id"):
            raise AssetServiceError(
                f"剧本场景缺少 scene_id: {shooting_script_scene!r}"
            )
        return wo_ext.build_requirement(shooting_script_scene)

    # ------------------------------------------------------------------
    # 2. 内部检索
    # ------------------------------------------------------------------

    def search_internal(
        self, requirement: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        """查询内部 SceneDNA 库：按标签重合度判断是否可直接复用。"""
        self._check_context(context)
        want = set(wo_ext.derive_tags(requirement))
        matched: list[str] = []
        scores: list[float] = []

        if want:
            for asset_id, rec in self.store.scene_atoms.items():
                if not rec.get("reusable", True):
                    continue
                have = set(rec.get("tags") or [])
                score = round(len(want & have) / len(want), 4)
                if score >= INTERNAL_HIT_THRESHOLD:
                    matched.append(asset_id)
                    scores.append(score)

        # 高分优先
        order = sorted(range(len(matched)), key=lambda i: scores[i], reverse=True)
        return {
            "found": bool(matched),
            "matched_asset_ids": [matched[i] for i in order],
            "similarity_scores": [scores[i] for i in order],
            "threshold": INTERNAL_HIT_THRESHOLD,
        }

    # ------------------------------------------------------------------
    # 3. 建工单
    # ------------------------------------------------------------------

    def create_workorder(
        self,
        requirement: dict[str, Any],
        search_result: dict[str, Any],
        context: Quad,
        *,
        requested_by: str = "scriptbrain",
        priority: str = "normal",
    ) -> dict[str, Any]:
        """内部缺失时创建 SCENE 工单（PENDING）。"""
        self._check_context(context)
        if search_result.get("found"):
            raise AssetServiceError(
                f"场景 {requirement.get('scene_id')} 已在内部库命中，不应创建工单"
            )

        wo = new_workorder(
            workorder_id=self.store.next_id(self.workorder_prefix),
            workorder_type=self.workorder_type,
            quad=context,
            requested_by=requested_by,
            requirement=requirement,
            generation_plan=wo_ext.build_generation_plan(requirement),
            priority=priority,
        )
        wo.internal_search_result = {
            "found": False,
            "matched_asset_ids": [],
            "similarity_scores": [],
        }
        self.store.put_workorder(wo)
        self._write(f"08_submissions/workorders/{wo.workorder_id}.json", wo.to_dict())
        return wo.to_dict()

    # ------------------------------------------------------------------
    # 4. Mock 生成
    # ------------------------------------------------------------------

    def mock_generate(self, workorder: Workorder | dict[str, Any]) -> dict[str, Any]:
        """Phase 1 mock：产出场景原子 + 重新布局结果。

        真实实现应为：Searcher 抓取授权自然场景 → Atom Extractor 提取原子 →
        Composer 按剧本重新布局。这里只保证结构与数值可复现。
        """
        wo = self._resolve(workorder)
        if wo.status is WorkorderStatus.PENDING:
            wo.mark_running()
        self._require_status(wo, WorkorderStatus.RUNNING)

        req = wo.requirement
        plan = wo.generation_plan
        seed = wo.workorder_id
        required_atoms = list(req.get("required_atoms") or [])
        # 纯 AI 兜底路线不产出真实原子（它本来就没有"自然原子"可提取）
        if plan.get("source_kind") == "pure_ai_generated":
            required_atoms = list(plan.get("ai_covered_atoms") or required_atoms)

        atoms = [
            {
                "atom_id": f"atom_{seed}_{i:02d}",
                "atom_type": atom_type,
                # Phase 1 交替标注来源，体现"内部优先、其次授权公开"
                "source": "internal_db" if i % 2 == 0 else "authorized_public",
                "license": "authorized",
                "naturalness": stable_score(0.80, 0.95, seed, "atom", i),
            }
            for i, atom_type in enumerate(required_atoms)
        ]

        asset: dict[str, Any] = {
            "workorder_id": wo.workorder_id,
            "asset_id": f"scene_layout_{seed}",
            "asset_type": "COMPOSED_LAYOUT",
            "scene_id": req.get("scene_id"),
            "atoms": atoms,
            "layout": {
                "spatial_needs": req.get("spatial_needs"),
                "camera_intent": req.get("camera_intent"),
                "blocking_supported": True,
                "3d_support": False,
            },
            "tags": wo_ext.derive_tags(req),
            # 生成路线：自然原子重组 or 纯 AI 生图（Gate 会检查该字段）
            "source_kind": plan.get("source_kind", "natural_atom_recompose"),
            "sources_used": list(plan.get("sources_used_override") or [])
            or sorted({a["source"] for a in atoms})
            or ["internal_db"],
            "naturalness": stable_score(0.78, 0.94, seed, "naturalness"),
            "script_match": stable_score(0.72, 0.95, seed, "script_match"),
            "layout_usability": stable_score(0.70, 0.93, seed, "layout"),
            # 版权风险默认很低；下发 rights_risk_override 可模拟"需人工复核"
            # 与"风险过高被拒"两种场景，用于验证审核链路（与其它 override 同一用途）
            "rights_risk": (
                round(float(plan["rights_risk_override"]), 4)
                if plan.get("rights_risk_override") is not None
                else stable_score(0.01, 0.12, seed, "rights")
            ),
            "mock_mode": True,
        }

        # 真实出图：配了图像后端且非纯 AI 退化路线时，生成一张真实场景图
        if (self.image_backend is not None and self.bundle is not None
                and asset["source_kind"] != "pure_ai_generated"):
            self._attach_real_scene(asset, req)

        asset["asset_hash"] = asset_hash_of(asset)

        self._write(f"07_payloads/scene/{wo.workorder_id}.json", asset)
        return asset

    def _attach_real_scene(self, asset: dict[str, Any], req: dict[str, Any]) -> None:
        """出一张真实场景图（竖屏短剧构图），落 Bundle，标真实。"""
        from assets_real.asset_image import generate_real_image
        scene_id = req.get("scene_id", asset["asset_id"])
        relpath = f"03_scene/{scene_id}/scene.png"
        res = generate_real_image(
            self.image_backend, self.bundle, relpath=relpath,
            prompt=scene_prompt(req), item_id=f"scene_{scene_id}",
            width=768, height=1024)
        if res["ok"]:
            asset["scene_image"] = res["relpath"]   # 后缀可能已按真实字节纠正
            asset["scene_image_abspath"] = res["abspath"]
            asset["scene_image_meta"] = res["meta"]
            asset["is_real_image"] = True
        else:
            asset["is_real_image"] = False
            asset["real_scene_error"] = res.get("error")

    # ------------------------------------------------------------------
    # 5. Gate
    # ------------------------------------------------------------------

    def run_gate(
        self,
        generated_asset: dict[str, Any],
        requirement: dict[str, Any],
        context: Quad,
    ) -> dict[str, Any]:
        """场景质量与版权 Gate，并推动工单进入 GATE_REVIEW / REJECTED。"""
        self._check_context(context)
        wid = generated_asset.get("workorder_id")
        if not wid:
            raise AssetServiceError("generated_asset 缺少 workorder_id，无法关联 Gate")
        wo = self._resolve({"workorder_id": wid})
        self._require_status(wo, WorkorderStatus.RUNNING)

        report: GateReport = run_scene_gate(
            gate_id=self.store.next_id("gate_scene"),
            workorder_id=wo.workorder_id,
            generated_asset=generated_asset,
            requirement=requirement,
        )
        d = report.to_dict()
        self.store.put_gate_report(d)
        self._write(f"11_review/scene_gate/{report.gate_id}.json", d)

        wo.mark_gate_review(d)
        if not report.passed:
            wo.mark_rejected()
            self._write(
                f"08_submissions/workorders/{wo.workorder_id}.json", wo.to_dict()
            )
        return d

    # ------------------------------------------------------------------
    # 6. 回流
    # ------------------------------------------------------------------

    def backflow(
        self,
        approved_asset: dict[str, Any],
        workorder: Workorder | dict[str, Any],
        context: Quad,
    ) -> dict[str, Any]:
        """成功资产回流全局 SceneDNA 库（接口文档 §2.4）。"""
        self._check_context(context)
        wo = self._resolve(workorder)
        self._require_status(wo, WorkorderStatus.APPROVED)

        gr = wo.gate_report or {}
        if not gr.get("passed"):
            raise AssetServiceError(
                f"拒绝回流：工单 {wo.workorder_id} 的 Gate 未通过"
            )

        bound = context.with_asset_hash(
            approved_asset.get("asset_hash") or asset_hash_of(approved_asset)
        )
        bound.require_bound()

        quality = round(
            (
                float(gr["scores"]["naturalness"])
                + float(gr["scores"]["script_match"])
                + float(gr["scores"]["layout_usability"])
            )
            / 3.0,
            4,
        )

        record = {
            "schema_version": schemas.SCHEMA_SCENE_BACKFLOW,
            "backflow_id": self.store.next_id("bf_scene"),
            "asset_id": approved_asset["asset_id"],
            "workorder_id": wo.workorder_id,
            "story_id": bound.story_id,
            "bundle_id": bound.bundle_id,
            "asset_hash": bound.asset_hash,
            "asset_type": approved_asset.get("asset_type", "COMPOSED_LAYOUT"),
            "tags": list(approved_asset.get("tags") or []),
            "source": "generated",
            "quality_score": quality,
            # 能进库就一定过了 Gate；显式记录，供 Director 在签合约时核验
            "gate_passed": True,
            "reusable": True,
            # 真实场景图（绝对路径，供跨剧回流复用）；mock 时为 None
            "scene_image": approved_asset.get("scene_image"),
            "scene_image_abspath": approved_asset.get("scene_image_abspath"),
            "is_real_image": bool(approved_asset.get("is_real_image")),
            "created_at": schemas.utc_now_iso(),
        }

        self.store.put_scene_atom(
            record["asset_id"],
            {**record, "atoms": approved_asset.get("atoms", [])},
        )
        self.store.put_backflow(record)

        wo.result_asset_ids = [record["asset_id"]]
        wo.mark_backflowed(record["backflow_id"])

        self._write(
            f"03_scene/{approved_asset.get('scene_id')}/layout.json", approved_asset
        )
        self._write(f"12_asset_backflow/scene/{record['backflow_id']}.json", record)
        self._write(f"08_submissions/workorders/{wo.workorder_id}.json", wo.to_dict())
        return record
