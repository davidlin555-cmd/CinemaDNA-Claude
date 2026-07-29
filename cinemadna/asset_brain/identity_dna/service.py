"""IdentityDNA 自然多人脸合成大模型 · Phase 1 Service

对应接口文档 §3.1。流程：

    剧本人物 → parse_requirement → search_registry(Character Registry)
      ├─ 命中 → 直接复用（不建工单）
      └─ 未命中 → create_workorder → mock_multi_face_fusion
                  → generate_age_line / generate_family_pack
                  → run_gate（最严格，含硬性拦截）
                  → build_master_pack → backflow(Character Registry)

红线由 gate.py 执行，本文件只负责编排与两处策略强制：
1. parse_requirement 无条件把 must_multi_face_fusion / forbid_single_real_clone
   置为 True —— 调用方**无法**从入口关掉红线。
2. backflow 前再次校验 gate_report 的 hard_blocks 为空 —— 即便有人手工
   改了工单状态，也无法把带硬拦截的身份写进 Character Registry。
"""

from __future__ import annotations

from typing import Any

from ..common import schemas
from ..common.quad import Quad
from ..common.service_base import AssetServiceError, BaseAssetService
from ..common.workorder import Workorder, WorkorderStatus, new_workorder
from . import fusion_mock
from .gate import IdentityGateReport, run_identity_gate

#: Character Registry 复用判定阈值：相似度达到该值才算"这个角色已经有了"
REGISTRY_REUSE_THRESHOLD = 0.85

#: 默认融合源数量（>= gate.MIN_FUSION_SOURCES）
DEFAULT_FUSION_SOURCE_COUNT = 3

#: 需求要求年龄线但未指定目标年龄时的默认年龄线
DEFAULT_AGE_TARGETS = [25, 35, 50]


class IdentityDNAService(BaseAssetService):
    """IdentityDNA Phase 1 Service（mock 生成 + 真实 Gate）。"""

    workorder_type = schemas.WORKORDER_TYPE_IDENTITY
    workorder_prefix = "wo_identitydna"

    def __init__(self, store=None, bundle=None, *, image_backend=None) -> None:
        super().__init__(store, bundle)
        #: 可选真实图像后端（Together FLUX 等）。为 None 时走 mock（不出图）。
        self.image_backend = image_backend

    # ------------------------------------------------------------------
    # 1. 需求解析
    # ------------------------------------------------------------------

    def parse_requirement(
        self, character_from_script: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        """从剧本人物描述提取标准化身份需求（接口文档 §3.2）。

        注意两个红线开关是**硬编码 True**，不读取调用方传入值：
        肖像权策略不是可配置项。
        """
        self._check_context(context)
        c = character_from_script
        character_id = c.get("character_id") or c.get("id")
        if not character_id:
            raise AssetServiceError(f"人物描述缺少 character_id: {c!r}")

        return {
            "character_id": character_id,
            "name": c.get("name", ""),
            "age_range": c.get("age_range", ""),
            "gender": c.get("gender", ""),
            "ethnicity_preference": c.get("ethnicity_preference", "东亚/中国"),
            "personality_keywords": list(c.get("personality_keywords", [])),
            "role_type": c.get("role_type", "配角"),
            # —— 以下两项为不可协商的红线策略 ——
            "must_multi_face_fusion": True,
            "forbid_single_real_clone": True,
            "need_age_line": bool(c.get("need_age_line", False)),
            "need_family": bool(c.get("need_family", False)),
        }

    # ------------------------------------------------------------------
    # 2. Registry 检索
    # ------------------------------------------------------------------

    def search_registry(
        self, requirement: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        """查询 Character Registry / Cast Universe，判断该角色能否直接复用。

        Phase 1 规则：同 character_id 已存在且已过 Gate 即视为命中。
        """
        self._check_context(context)
        cid = requirement["character_id"]
        matched: list[str] = []
        scores: list[float] = []

        existing = self.store.character_registry.get(cid)
        if existing and existing.get("gate_passed") and existing.get("reusable", True):
            matched.append(existing["master_pack_id"])
            scores.append(1.0)

        return {
            "found": bool(matched),
            "matched_asset_ids": matched,
            "similarity_scores": scores,
            "threshold": REGISTRY_REUSE_THRESHOLD,
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
        """Registry 缺失时创建 IDENTITY 工单（PENDING）。"""
        self._check_context(context)
        if search_result.get("found"):
            raise AssetServiceError(
                f"角色 {requirement['character_id']} 已在 Registry 命中，不应创建工单"
            )

        plan: dict[str, Any] = {
            "fusion_source_count": DEFAULT_FUSION_SOURCE_COUNT,
            "mock_mode": True,
            "age_targets": (
                list(requirement.get("age_targets") or DEFAULT_AGE_TARGETS)
                if requirement.get("need_age_line")
                else []
            ),
            "family_relations": list(requirement.get("family_relations") or []),
        }

        wo = new_workorder(
            workorder_id=self.store.next_id(self.workorder_prefix),
            workorder_type=self.workorder_type,
            quad=context,
            requested_by=requested_by,
            requirement=requirement,
            generation_plan=plan,
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
    # 4. Mock 融合 + 年龄线 + 家族脸谱
    # ------------------------------------------------------------------

    def mock_multi_face_fusion(
        self, workorder: Workorder | dict[str, Any]
    ) -> dict[str, Any]:
        """Phase 1 mock 多人脸融合，产出 identity_pack 并落 Bundle。"""
        wo = self._resolve(workorder)
        if wo.status is WorkorderStatus.PENDING:
            wo.mark_running()
        self._require_status(wo, WorkorderStatus.RUNNING)

        pack = fusion_mock.mock_multi_face_fusion(
            workorder_id=wo.workorder_id,
            requirement=wo.requirement,
            generation_plan=wo.generation_plan,
        )

        # 真实出图：配了图像后端就生成一张真实合成人脸，替换 mock 融合源为
        # 纯合成来源，并把真实图片落进 Bundle（gate 已放行纯合成场景）。
        if self.image_backend is not None and self.bundle is not None:
            self._attach_real_face(pack, wo)

        if wo.requirement.get("need_age_line"):
            pack["age_line"] = self.generate_age_line(
                pack, wo.generation_plan.get("age_targets") or DEFAULT_AGE_TARGETS
            )
        if wo.requirement.get("need_family"):
            pack["family_pack"] = self.generate_family_pack(
                pack, wo.generation_plan.get("family_relations") or []
            )

        self._write(f"07_payloads/identity/{wo.workorder_id}.json", pack)
        return pack

    def _attach_real_face(self, pack: dict[str, Any], wo: Workorder) -> None:
        """用图像后端出一张真实合成人脸，落 Bundle，并把身份包标为纯合成来源。

        单个角色出图失败（如内容过滤误判）**不崩溃整条流水线**：留作 mock、
        记原因，由 PreRender Gate 的"真实人脸缺失"如实拦下，用户可重试。
        """
        from assets_real.asset_image import generate_real_image
        cid = wo.requirement.get("character_id", wo.workorder_id)
        relpath = f"02_cast/{cid}/face.png"
        prompt = fusion_mock.face_prompt(wo.requirement)
        res = generate_real_image(
            self.image_backend, self.bundle, relpath=relpath,
            prompt=prompt, item_id=cid, width=768, height=1024)
        if not res["ok"]:                # 出图失败降级为 mock，不中断
            pack["real_face_error"] = res.get("error")
            pack["is_real_image"] = False
            return
        relpath = res["relpath"]         # 后缀可能已按真实字节纠正
        pack["fusion_sources"] = fusion_mock.synthetic_face_source(
            wo.workorder_id, relpath)
        pack["fusion_source_count"] = 1
        pack["base_face_image"] = relpath
        pack["face_image_abspath"] = res["abspath"]
        pack["face_image_meta"] = res["meta"]
        pack["generation_method"] = "synthetic_text2image"
        pack["is_real_image"] = True
        # 重算 hash（内容变了）
        from ..common.hashing import asset_hash_of
        pack["asset_hash"] = asset_hash_of(
            {k: v for k, v in pack.items() if k != "asset_hash"})

    def generate_age_line(
        self, base_identity: dict[str, Any], ages: list[int]
    ) -> dict[str, str]:
        """生成年龄线版本（mock）。"""
        return fusion_mock.mock_age_line(base_identity, list(ages))

    def generate_family_pack(
        self, base_identity: dict[str, Any], relations: list[str]
    ) -> dict[str, str]:
        """生成家族脸谱（mock）。"""
        return fusion_mock.mock_family_pack(base_identity, list(relations))

    # ------------------------------------------------------------------
    # 5. Gate（最关键）
    # ------------------------------------------------------------------

    def run_gate(
        self,
        identity_pack: dict[str, Any],
        requirement: dict[str, Any],
        context: Quad,
    ) -> dict[str, Any]:
        """自然度 + 相似度风险 + 家族一致性 Gate，并推动工单状态。

        Gate 结论直接驱动状态机：
        - passed      → GATE_REVIEW（等待自动或人工 approve）
        - 未通过      → GATE_REVIEW → 立刻 REJECTED
        """
        self._check_context(context)
        wid = identity_pack.get("workorder_id")
        if not wid:
            raise AssetServiceError("identity_pack 缺少 workorder_id，无法关联 Gate")
        wo = self._resolve({"workorder_id": wid})
        self._require_status(wo, WorkorderStatus.RUNNING)

        report: IdentityGateReport = run_identity_gate(
            gate_id=self.store.next_id("gate_identity"),
            workorder_id=wo.workorder_id,
            identity_pack=identity_pack,
            requirement=requirement,
        )
        d = report.to_dict()
        self.store.put_gate_report(d)
        self._write(f"11_review/identity_gate/{report.gate_id}.json", d)

        wo.mark_gate_review(d)
        # 未通过即落 REJECTED；硬拦截情况下这一步是终局，人工也无法翻案
        if not report.passed:
            wo.mark_rejected()
            self._write(
                f"08_submissions/workorders/{wo.workorder_id}.json", wo.to_dict()
            )
        return d

    # ------------------------------------------------------------------
    # 6. Master Pack + 回流
    # ------------------------------------------------------------------

    def build_master_pack(self, approved_identity: dict[str, Any]) -> dict[str, Any]:
        """打包 Character Master Pack（接口文档 §3.4）。"""
        return {
            "schema_version": schemas.SCHEMA_CHARACTER_MASTER_PACK,
            "character_id": approved_identity.get("character_id"),
            "master_pack_id": self.store.next_id("cmp"),
            "base_face_asset_id": approved_identity.get("base_face_asset_id"),
            # 真实人脸图路径（image2video 的角色参考）；mock 时为 None
            "base_face_image": approved_identity.get("base_face_image"),
            "is_real_image": bool(approved_identity.get("is_real_image")),
            "generation_method": approved_identity.get("generation_method", "mock"),
            "age_line": dict(approved_identity.get("age_line") or {}),
            "family_pack": dict(approved_identity.get("family_pack") or {}),
            "expression_baseline": list(
                approved_identity.get("expression_baseline") or []
            ),
            "reusable": True,
            "gate_passed": True,
            "backflow_at": None,
        }

    def backflow(
        self,
        master_pack: dict[str, Any],
        workorder: Workorder | dict[str, Any],
        context: Quad,
    ) -> dict[str, Any]:
        """回流 Character Registry。

        三重防线：
        1. 工单必须处于 APPROVED；
        2. gate_report 必须 passed 且 hard_blocks 为空（二次校验，防手工篡改状态）；
        3. 四元组必须已绑定 asset_hash。
        """
        self._check_context(context)
        wo = self._resolve(workorder)
        self._require_status(wo, WorkorderStatus.APPROVED)

        gr = wo.gate_report or {}
        if not gr.get("passed") or gr.get("hard_blocks"):
            raise AssetServiceError(
                f"拒绝回流：工单 {wo.workorder_id} 的 Gate 未通过或存在硬拦截 "
                f"{gr.get('hard_blocks')}"
            )

        bound = context.with_asset_hash(
            master_pack.get("asset_hash") or self._pack_hash(master_pack)
        )
        bound.require_bound()

        master_pack = dict(master_pack)
        master_pack["backflow_at"] = schemas.utc_now_iso()
        master_pack["asset_hash"] = bound.asset_hash
        master_pack["story_id"] = bound.story_id
        master_pack["bundle_id"] = bound.bundle_id

        cid = master_pack["character_id"]
        self.store.put_character(cid, master_pack)
        self.store.put_backflow(master_pack)

        wo.result_asset_ids = [master_pack["master_pack_id"]]
        wo.mark_backflowed(master_pack["master_pack_id"])

        self._write(f"02_cast/{cid}/master_pack.json", master_pack)
        self._write(f"12_asset_backflow/identity/{cid}.json", master_pack)
        self._write(f"08_submissions/workorders/{wo.workorder_id}.json", wo.to_dict())
        return master_pack

    @staticmethod
    def _pack_hash(master_pack: dict[str, Any]) -> str:
        from ..common.hashing import asset_hash_of

        return asset_hash_of(master_pack)
