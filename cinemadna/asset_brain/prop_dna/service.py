"""PropDNA 道具生成大模型 · Phase 1 Service

对应接口文档 §4.1。流程：

    剧本道具 → parse_requirement → search_internal(PropDNA 库)
      ├─ 命中 → 引用并检查状态连续性（缺状态则仍需补生成）
      └─ 未命中 → create_workorder → mock_synthesize（按状态生成版本）
                  → manage_continuity（跨镜头状态时间线）
                  → run_gate → backflow

PropDNA 的独有价值在"状态连续性"：同一件道具在不同镜头里可能是
「完整 / 被攥皱 / 放在桌上」，必须是同一资产的不同状态版本，
而不是三件长得不一样的道具。
"""

from __future__ import annotations

from typing import Any

from ..common import schemas
from ..common.gate import GateReport
from ..common.hashing import asset_hash_of, stable_score
from ..common.quad import Quad
from ..common.service_base import AssetServiceError, BaseAssetService
from ..common.workorder import Workorder, WorkorderStatus, new_workorder
from .gate import run_prop_gate


#: 短剧高频道具 → 专用出图提示词模板（英文，稳出图）。按 prop_id/名称关键词匹配。
_PROP_TEMPLATES: list[tuple[tuple[str, ...], str]] = [
    (("phone", "手机", "screen", "屏幕"),
     "smartphone screen UI mockup, clean mobile app interface, realistic phone display, "
     "no real brand logo, legible text"),
    (("contract", "合同", "协议", "agreement"),
     "close-up of a paper contract document, printed legal text, signature line, "
     "realistic paper texture, legible"),
    (("payment", "付款", "缴费", "bill", "invoice", "账单"),
     "mobile payment record screen UI, transaction list, amounts, clean interface, "
     "no real bank brand, legible numbers"),
    (("record", "录音", "voice", "audio"),
     "voice recording app UI on a phone screen, waveform, timer, record button, "
     "clean interface, legible"),
]

_PROP_STATE_HINT = {
    "完整": "intact and new", "被攥皱": "crumpled", "被撕": "torn",
    "放在桌上": "lying on a wooden table", "被摔裂": "cracked screen",
}


def prop_prompt(requirement: dict[str, Any], state: str) -> str:
    """由道具需求 + 状态拼出文生图提示词。高频道具走专用模板。"""
    pid = str(requirement.get("prop_id", "")).lower()
    name = str(requirement.get("name", ""))
    desc = str(requirement.get("description", ""))
    key = f"{pid} {name} {desc}".lower()
    base = None
    for keywords, tmpl in _PROP_TEMPLATES:
        if any(k in key for k in keywords):
            base = tmpl
            break
    if base is None:
        base = f"a realistic prop: {name}, {desc}, product photo on plain background"
    state_hint = _PROP_STATE_HINT.get(state, state)
    return (f"{base}, condition: {state_hint}, photorealistic, high detail, "
            f"vertical composition, SFW, no watermark")


class PropDNAService(BaseAssetService):
    """PropDNA Phase 1 Service（mock 生成 + 真实 Gate）。"""

    workorder_type = schemas.WORKORDER_TYPE_PROP
    workorder_prefix = "wo_propdna"

    def __init__(self, store=None, bundle=None, *, image_backend=None,
                 screen_backend=None) -> None:
        super().__init__(store, bundle)
        #: 可选真实图像后端（Together FLUX），出物理道具。None=mock。
        self.image_backend = image_backend
        #: 可选屏幕道具渲染后端（无头 Chrome 模板截图），出手机/合同/付款/录音。
        #: 本地免费、文字清晰，专治 FLUX 出屏内文字乱码。None=不出屏幕道具。
        self.screen_backend = screen_backend

    # ------------------------------------------------------------------
    # 1. 需求解析
    # ------------------------------------------------------------------

    def parse_requirement(
        self, prop_from_script: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        """从剧本提取道具需求与状态变化（接口文档 §4.2）。"""
        self._check_context(context)
        p = prop_from_script
        prop_id = p.get("prop_id") or p.get("id")
        if not prop_id:
            raise AssetServiceError(f"道具描述缺少 prop_id: {p!r}")

        states = list(p.get("states_needed") or [])
        if not states:
            # 没写状态时至少要有一个默认态，否则连续性无从谈起
            states = ["默认"]

        return {
            "prop_id": prop_id,
            "name": p.get("name", ""),
            "description": p.get("description", ""),
            "states_needed": states,
            "story_function": p.get("story_function", ""),
            "must_be_natural": True,
            "continuity_critical": bool(p.get("continuity_critical", len(states) > 1)),
        }

    # ------------------------------------------------------------------
    # 2. 内部检索
    # ------------------------------------------------------------------

    def search_internal(
        self, requirement: dict[str, Any], context: Quad
    ) -> dict[str, Any]:
        """查询内部 PropDNA 库；命中还需状态覆盖完整才算真正可复用。"""
        self._check_context(context)
        pid = requirement["prop_id"]
        rec = self.store.prop_library.get(pid)

        if not rec or not rec.get("reusable", True):
            return {
                "found": False,
                "matched_asset_ids": [],
                "similarity_scores": [],
                "missing_states": list(requirement.get("states_needed") or []),
            }

        have = dict(rec.get("asset_ids_by_state") or {})
        missing = [s for s in requirement.get("states_needed", []) if s not in have]
        coverage = 1.0
        needed = list(requirement.get("states_needed") or [])
        if needed:
            coverage = round((len(needed) - len(missing)) / len(needed), 4)

        # 状态不全 = 不能直接复用，仍需建工单补齐
        found = not missing
        return {
            "found": found,
            "matched_asset_ids": sorted(have.values()) if found else [],
            "similarity_scores": [coverage] if found else [],
            "missing_states": missing,
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
        """内部缺失（或状态不全）时创建 PROP 工单（PENDING）。"""
        self._check_context(context)
        if search_result.get("found"):
            raise AssetServiceError(
                f"道具 {requirement['prop_id']} 已在内部库命中且状态齐全，不应创建工单"
            )

        wo = new_workorder(
            workorder_id=self.store.next_id(self.workorder_prefix),
            workorder_type=self.workorder_type,
            quad=context,
            requested_by=requested_by,
            requirement=requirement,
            generation_plan={
                "mock_mode": True,
                "generate_state_variants": True,
                # 只补缺失状态；全新道具则等于全部状态
                "target_states": list(
                    search_result.get("missing_states")
                    or requirement.get("states_needed")
                    or []
                ),
            },
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
    # 4. Mock 生成 + 连续性
    # ------------------------------------------------------------------

    def mock_synthesize(self, workorder: Workorder | dict[str, Any]) -> dict[str, Any]:
        """Phase 1 mock：为每个需要的状态生成一个资产版本。"""
        wo = self._resolve(workorder)
        if wo.status is WorkorderStatus.PENDING:
            wo.mark_running()
        self._require_status(wo, WorkorderStatus.RUNNING)

        req = wo.requirement
        plan = wo.generation_plan
        seed = wo.workorder_id
        states = list(req.get("states_needed") or [])
        # skip_states 用于模拟"状态版本没生成齐"，验证连续性 Gate 会拦下来
        skip = set(plan.get("skip_states") or [])

        asset_ids_by_state = {
            state: f"prop_{req['prop_id']}_{i:02d}"
            for i, state in enumerate(states)
            if state not in skip
        }

        asset: dict[str, Any] = {
            "workorder_id": wo.workorder_id,
            "prop_id": req["prop_id"],
            "name": req.get("name", ""),
            "asset_ids_by_state": asset_ids_by_state,
            "base_asset_id": next(iter(asset_ids_by_state.values()), None),
            "sources_used": list(
                plan.get("sources_used_override") or ["internal_db", "synthetic"]
            ),
            "real_brand_marks": list(plan.get("real_brand_marks") or []),
            "naturalness": stable_score(0.78, 0.94, seed, "naturalness"),
            "script_match": stable_score(0.74, 0.95, seed, "script_match"),
            "rights_risk": stable_score(0.01, 0.10, seed, "rights"),
            "state_timeline": [],
            "mock_mode": True,
        }

        # 真实出图：屏幕道具走 Chrome 模板截图，物理道具走 FLUX
        if self.bundle is not None and (
                self.image_backend is not None or self.screen_backend is not None):
            self._attach_real_props(asset, req, asset_ids_by_state)

        asset["asset_hash"] = asset_hash_of(asset)

        self._write(f"07_payloads/prop/{wo.workorder_id}.json", asset)
        return asset

    def _attach_real_props(self, asset: dict[str, Any], req: dict[str, Any],
                           asset_ids_by_state: dict[str, str]) -> None:
        """为每个状态出一张真实道具图，落 Bundle，标真实。

        屏幕内容类道具（手机/合同/付款/录音）走 Chrome 模板截图 —— 文字清晰
        可读、零乱码；其余物理道具走 FLUX 文生图。两条路都写进同一批字段，
        对下游（Contract / Gate / 回流）完全一致。
        """
        from .screen_templates import classify_screen_prop
        pid = req["prop_id"]
        screen_kind = classify_screen_prop(req)
        is_screen = screen_kind is not None
        has_chrome = (self.screen_backend is not None
                      and self.screen_backend.available())
        # 屏幕道具**只走模板截图**——没 Chrome 就如实标缺失，绝不退回 FLUX 出乱码
        if is_screen:
            method = "template_screenshot" if has_chrome else None
        else:
            method = "flux_text2image" if self.image_backend is not None else None
        images: dict[str, str] = {}
        images_abs: dict[str, str] = {}
        errors: dict[str, str] = {}
        for state in asset_ids_by_state:
            if is_screen and has_chrome:
                ok, relpath, abspath, err = self._render_screen_prop(
                    screen_kind, req, state, pid)
            elif is_screen:
                ok, relpath, abspath, err = (
                    False, None, None, "屏幕道具但无 Chrome 渲染后端")
            elif self.image_backend is not None:
                ok, relpath, abspath, err = self._render_flux_prop(req, state, pid)
            else:
                ok, relpath, abspath, err = False, None, None, "无图像后端"
            if ok:
                images[state] = relpath
                images_abs[state] = abspath
            else:
                errors[state] = err or ""
        asset["prop_images_by_state"] = images
        asset["prop_images_abspath"] = images_abs
        asset["is_real_image"] = bool(images) and not errors
        asset["prop_render_method"] = method
        asset["screen_kind"] = screen_kind
        if errors:
            asset["real_prop_errors"] = errors

    #: 债务/催收语境（→ 已逾期待支付）
    _DEBT_KW = ("催债", "逾期", "借", "欠", "债", "高利贷", "追债", "还钱", "贷",
                "利息", "催缴", "压力", "还款", "欠款")
    #: 账单/缴费语境（本质是"待付"，绝不能默认渲成"支付成功"）
    _BILL_KW = ("缴费", "账单", "欠费", "未缴", "账", "费用", "缴纳", "收费")

    def _story_screen_state(self, kind: str, req: dict[str, Any], state: str) -> str:
        """剧情锁定信息镜状态：从道具的剧情字段推出屏内应显示的语义状态。

        物理状态名（"完整"/"放在桌上"）只描述外观，不含剧情语义。付款类信息镜若
        道具是账单/缴费单（本质待付）或处于债务/催收语境，必须显示"待支付"甚至
        "已逾期"，而非模板默认的"支付成功"——这就是"剧情锁定每镜道具状态"。
        """
        if kind != "payment":
            return state          # 短信/合同/录音模板各自按 state 关键词判定，已够用
        if any(k in state for k in ("逾期", "待", "未", "失败", "驳回", "成功")):
            return state          # 已含明确语义，不叠加
        text = f"{req.get('name', '')} {req.get('description', '')} " \
               f"{req.get('story_function', '')}"
        is_bill = any(k in text for k in self._BILL_KW)
        is_debt = any(k in text for k in self._DEBT_KW)
        if is_debt or (is_bill and "压力" in text):
            return f"{state}·已逾期待支付"   # 债务/压力语境 → 逾期
        if is_bill:
            return f"{state}·待支付"          # 账单本质待付，绝不"支付成功"
        return state

    def _render_screen_prop(self, kind, req, state, pid):
        """屏幕道具：HTML 模板 → 无头 Chrome 截图 → 清晰可读 PNG。

        文件名用物理状态（与合约 prop_image 对齐），内容用**剧情锁定的语义状态**。
        """
        import time as _time
        from .screen_templates import render_screen_html
        relpath = f"04_props/{pid}/{state}.png"
        render_state = self._story_screen_state(kind, req, state)
        html = render_screen_html(kind, req, render_state)   # 确定性，重试外
        # Chrome 无头截图偶发失败（进程启动竞态/超时）→ 重试；瞬时故障不该毁整条道具
        # （同一模板同一后端，另一状态成功=可重试）。3 次仍败才如实标缺失。
        last_err = ""
        for attempt in range(3):
            try:
                self.screen_backend.render(
                    html=html, dest=self.bundle.path_for(relpath),
                    kind=kind, width=768, height=1024)
                abspath = str(self.bundle.path_for(relpath).resolve())
                return True, relpath, abspath, None
            except Exception as e:  # noqa: BLE001 —— 单张失败不崩溃整条流水线
                last_err = str(e)[:200]
                if attempt < 2:
                    _time.sleep(0.8)                          # 让 Chrome 喘口气再试
        return False, None, None, last_err

    def _render_flux_prop(self, req, state, pid):
        """物理道具：FLUX 文生图（后缀按真实字节纠正）。"""
        from assets_real.asset_image import generate_real_image
        relpath = f"04_props/{pid}/{state}.png"
        res = generate_real_image(
            self.image_backend, self.bundle, relpath=relpath,
            prompt=prop_prompt(req, state), item_id=f"prop_{pid}_{state}",
            width=768, height=1024)
        if res["ok"]:
            return True, res["relpath"], res["abspath"], None
        return False, None, None, res.get("error", "")

    def manage_continuity(
        self, prop_asset: dict[str, Any], state_timeline: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """管理跨镜头状态版本：把 [{shot_id, state}] 绑定到具体资产版本。

        未知状态会被标记为 unresolved，由 Gate 的 state_consistency 兜底暴露。

        时间线的锚点可以是 shot_id（Director 出镜头后）或 scene_id
        （ScriptBrain 刚出剧本时还没有镜头）—— 两者都接受。
        """
        by_state = dict(prop_asset.get("asset_ids_by_state") or {})
        resolved: list[dict[str, Any]] = []
        for entry in state_timeline:
            state = entry.get("state")
            resolved.append(
                {
                    "shot_id": entry.get("shot_id"),
                    "scene_id": entry.get("scene_id"),
                    "state": state,
                    "asset_id": by_state.get(state),
                    "resolved": state in by_state,
                }
            )
        prop_asset = dict(prop_asset)
        prop_asset["state_timeline"] = resolved
        prop_asset["continuity_resolved"] = all(r["resolved"] for r in resolved)
        prop_asset["asset_hash"] = asset_hash_of(
            {k: v for k, v in prop_asset.items() if k != "asset_hash"}
        )
        return prop_asset

    # ------------------------------------------------------------------
    # 5. Gate
    # ------------------------------------------------------------------

    def run_gate(
        self,
        prop_asset: dict[str, Any],
        requirement: dict[str, Any],
        context: Quad,
    ) -> dict[str, Any]:
        """道具质量与版权 Gate，并推动工单状态。"""
        self._check_context(context)
        wid = prop_asset.get("workorder_id")
        if not wid:
            raise AssetServiceError("prop_asset 缺少 workorder_id，无法关联 Gate")
        wo = self._resolve({"workorder_id": wid})
        self._require_status(wo, WorkorderStatus.RUNNING)

        report: GateReport = run_prop_gate(
            gate_id=self.store.next_id("gate_prop"),
            workorder_id=wo.workorder_id,
            prop_asset=prop_asset,
            requirement=requirement,
        )
        d = report.to_dict()
        self.store.put_gate_report(d)
        self._write(f"11_review/prop_gate/{report.gate_id}.json", d)

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
        approved_prop: dict[str, Any],
        workorder: Workorder | dict[str, Any],
        context: Quad,
    ) -> dict[str, Any]:
        """成功道具回流 PropDNA 库（接口文档 §4.4）。"""
        self._check_context(context)
        wo = self._resolve(workorder)
        self._require_status(wo, WorkorderStatus.APPROVED)

        gr = wo.gate_report or {}
        if not gr.get("passed"):
            raise AssetServiceError(
                f"拒绝回流：工单 {wo.workorder_id} 的 Gate 未通过"
            )

        bound = context.with_asset_hash(
            approved_prop.get("asset_hash") or asset_hash_of(approved_prop)
        )
        bound.require_bound()

        record = {
            "schema_version": schemas.SCHEMA_PROP_BACKFLOW,
            "backflow_id": self.store.next_id("bf_prop"),
            "prop_id": approved_prop["prop_id"],
            "workorder_id": wo.workorder_id,
            "story_id": bound.story_id,
            "bundle_id": bound.bundle_id,
            "asset_hash": bound.asset_hash,
            "asset_ids_by_state": dict(approved_prop.get("asset_ids_by_state") or {}),
            # 真实道具图（按状态，绝对路径供跨剧复用）；mock 时为空
            "prop_images_by_state": dict(approved_prop.get("prop_images_by_state") or {}),
            "prop_images_abspath": dict(approved_prop.get("prop_images_abspath") or {}),
            "is_real_image": bool(approved_prop.get("is_real_image")),
            # 出图方式：屏幕道具=template_screenshot，物理道具=flux_text2image
            "prop_render_method": approved_prop.get("prop_render_method"),
            "screen_kind": approved_prop.get("screen_kind"),
            # 真实图缺失时记录原因（供制片人控制台显示"为何无真实道具图"）
            "real_prop_errors": dict(approved_prop.get("real_prop_errors") or {}),
            # 能进库就一定过了 Gate；显式记录，供 Director 在签合约时核验
            "gate_passed": True,
            "reusable": True,
            "continuity_supported": bool(
                gr["scores"].get("state_consistency", 0.0) >= 1.0
            ),
            "quality_score": round(
                (
                    float(gr["scores"]["naturalness"])
                    + float(gr["scores"]["script_match"])
                )
                / 2.0,
                4,
            ),
            "created_at": schemas.utc_now_iso(),
        }

        self.store.put_prop(record["prop_id"], record)
        self.store.put_backflow(record)

        wo.result_asset_ids = sorted(record["asset_ids_by_state"].values())
        wo.mark_backflowed(record["backflow_id"])

        self._write(f"04_props/{record['prop_id']}/prop.json", approved_prop)
        self._write(f"12_asset_backflow/prop/{record['backflow_id']}.json", record)
        self._write(f"08_submissions/workorders/{wo.workorder_id}.json", wo.to_dict())
        return record
