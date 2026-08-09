"""CinemaDNA Web 控制台 · FastAPI 应用 (Phase 5)

浏览器是主操作面：查看故事状态 → 审批 Gate → 触发下一步 → 预览成片，
全流程不需要命令行。

## 错误语义（页面据此给提示，不靠猜）

| 领域异常                  | HTTP | 含义                                   |
|---------------------------|------|----------------------------------------|
| `HardBlockOverrideError`  | 409  | **肖像权红线，人工也不得放行**         |
| `InvalidStageTransition`  | 409  | 当前阶段不允许这个操作                 |
| `StoryNotFoundError`      | 404  | 故事不存在                             |
| 其它 `OrchestratorError`  | 409  | 流程冲突（如资产未就绪就想排镜头）     |
| `AssetServiceError`       | 409  | 资产层拒绝（如回流前二次校验没过）     |
| `ValueError`              | 400  | 入参不合法                             |

红线单独给 `code="HARD_BLOCK"`，前端会把"通过"按钮直接禁掉 ——
不给人留下"点一下试试"的机会。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from cinemadna.asset_brain.common.service_base import AssetServiceError
from cinemadna.asset_brain.identity_dna.gate import HardBlockOverrideError
from director.shot_contract import RenderNotAllowedError, ShotContractError
from orchestrator.pipeline import OrchestratorError, StoryNotFoundError
from orchestrator.story import InvalidStageTransition
from render.backend import RenderError
from scriptbrain.brief import ScriptBriefError
from scriptbrain.script import ScriptValidationError

from .schemas import (
    CreateStoryRequest,
    GateDecisionRequest,
    ReviewRequest,
    StepRequest,
)
from .services import FactoryService
from .state import FactoryWorkspace, set_workspace

STATIC_DIR = Path(__file__).parent / "static"

#: 领域异常 → (HTTP 码, 机器可读 code)
_ERROR_MAP: list[tuple[type[Exception], int, str]] = [
    (HardBlockOverrideError, 409, "HARD_BLOCK"),
    (StoryNotFoundError, 404, "NOT_FOUND"),
    (InvalidStageTransition, 409, "INVALID_STAGE"),
    (RenderNotAllowedError, 409, "RENDER_NOT_ALLOWED"),
    (ShotContractError, 409, "CONTRACT_INVALID"),
    (ScriptValidationError, 409, "SCRIPT_INVALID"),
    (ScriptBriefError, 400, "BAD_BRIEF"),
    (AssetServiceError, 409, "ASSET_REJECTED"),
    (RenderError, 409, "RENDER_FAILED"),
    (OrchestratorError, 409, "CONFLICT"),
    (PermissionError, 403, "FORBIDDEN"),
    (FileNotFoundError, 404, "NOT_FOUND"),
    (ValueError, 400, "BAD_REQUEST"),
]


def create_app(workspace: FactoryWorkspace | None = None) -> FastAPI:
    ws = workspace or FactoryWorkspace(Path.cwd() / "workspace")
    set_workspace(ws)
    svc = FactoryService(ws)

    app = FastAPI(
        title="CinemaDNA 工厂控制台",
        version="0.5.0",
        description="多故事并行短剧工厂的网页主控：看板 / 审核 / 资产 / 工单 / 成片",
    )
    app.state.workspace = ws
    app.state.service = svc

    # -- 异常映射 ---------------------------------------------------------
    # 逐个类型注册（而不是注册一个全局 Exception handler）：
    # Starlette 的全局 handler 归 ServerErrorMiddleware 管，它返回响应后仍会
    # 重新抛出异常；只有按具体类型注册才会被 ExceptionMiddleware 正常拦下。

    def _make_handler(status: int, tag: str):
        async def handler(request: Request, exc: Exception):
            return JSONResponse(
                status_code=status,
                content={"error": str(exc), "code": tag,
                         "type": type(exc).__name__},
            )
        return handler

    for exc_type, status_code, tag in _ERROR_MAP:
        app.add_exception_handler(exc_type, _make_handler(status_code, tag))

    # -- 页面 -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        import config

        from . import simulations

        return {
            "ok": True,
            "workspace": str(ws.root),
            "backends": ws.available_backends(),
            "simulations": simulations.describe(),
            "stories": len(ws.orc.list_stories()),
            # 只报"哪些能力接了密钥"，绝不含密钥值本身
            "config": config.describe(),
        }

    @app.post("/api/connectivity")
    def connectivity_check() -> dict[str, Any]:
        """按需对已配置的真实 Key 发一次最小只读请求，验证连通性。

        用 POST（有副作用：真实网络请求），且**只测已配 key**、只打只读端点，
        绝不触发计费任务。响应只含掩码指纹与结构，绝无密钥值。
        """
        import connectivity

        return connectivity.summary()

    # -- 工厂看板 ---------------------------------------------------------

    @app.get("/api/readiness")
    def readiness_audit() -> dict[str, Any]:
        """商业级就绪度审计（只读、无成本）。"""
        import readiness

        return readiness.audit()

    @app.get("/api/factory")
    def factory() -> dict[str, Any]:
        return svc.factory_board()

    @app.get("/api/stories")
    def list_stories(
        stage: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = ws.orc.list_stories(stage=stage, status=status)
        return [svc.story_detail(r["story_id"]) for r in rows]

    @app.post("/api/stories", status_code=201)
    def create_story(req: CreateStoryRequest) -> dict[str, Any]:
        return svc.create_story(**req.model_dump())

    @app.get("/api/stories/{story_id}")
    def get_story(story_id: str) -> dict[str, Any]:
        return svc.story_detail(story_id)

    @app.post("/api/stories/{story_id}/step")
    def run_step(story_id: str, req: StepRequest = Body(default=StepRequest())):
        return svc.run_step(story_id, req.action)

    # -- 人工审核 ---------------------------------------------------------

    @app.get("/api/reviews")
    def reviews() -> list[dict[str, Any]]:
        return svc.pending_reviews()

    @app.post("/api/stories/{story_id}/review")
    def review(story_id: str, req: ReviewRequest) -> dict[str, Any]:
        return svc.review(
            story_id, approved=req.approved, decided_by=req.decided_by,
            note=req.note, gate_id=req.gate_id,
        )

    @app.get("/api/gates/{gate_id}")
    def gate(gate_id: str) -> dict[str, Any]:
        detail = svc.gate_detail(gate_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Gate 不存在: {gate_id}")
        return detail

    @app.post("/api/gates/{gate_id}/decide")
    def decide_gate(gate_id: str, req: GateDecisionRequest) -> dict[str, Any]:
        return svc.decide_gate(
            gate_id, approved=req.approved, decided_by=req.decided_by, note=req.note
        )

    # -- 制片人控制台：审核中心 / 工作台 / 成本额度 -----------------------

    @app.get("/api/stories/{story_id}/reviews")
    def module_reviews(story_id: str) -> dict[str, Any]:
        """统一专业审核层：9 个模块各自最新审核结论 + 汇总。"""
        return svc.module_reviews(story_id)

    @app.get("/api/stories/{story_id}/workbench")
    def workbench(story_id: str) -> dict[str, Any]:
        """故事工作台：角色/场景/道具真实资产预览（含图片路径）。"""
        return svc.story_workbench(story_id)

    @app.get("/api/cost")
    def cost() -> dict[str, Any]:
        """成本额度：预算闸的已花/上限/剩余（无真实后端时为 mock 视图）。"""
        return svc.cost_view()

    @app.get("/api/optimizer")
    def optimizer() -> dict[str, Any]:
        """OptimizerDNA 复盘（只分析、不改档）。"""
        return svc.optimizer_view(apply=False)

    @app.post("/api/optimizer/run")
    def optimizer_run() -> dict[str, Any]:
        """跑一轮自我优化闭环（自我验收上版 + 应用可逆调参）。"""
        return svc.optimizer_view(apply=True)

    # -- 资产浏览器 -------------------------------------------------------

    @app.get("/api/assets/{kind}")
    def assets(kind: str) -> dict[str, Any]:
        rows = svc.assets(kind)
        return {"kind": kind, "count": len(rows), "items": rows}

    # -- 工单 -------------------------------------------------------------

    @app.get("/api/workorders")
    def workorders(
        story_id: str | None = None,
        type: str | None = Query(default=None, alias="type"),
        status: str | None = None,
    ) -> dict[str, Any]:
        rows = svc.workorders(story_id=story_id, workorder_type=type, status=status)
        return {"count": len(rows), "items": rows}

    @app.get("/api/workorders/{workorder_id}")
    def workorder(workorder_id: str) -> dict[str, Any]:
        return svc.workorder_detail(workorder_id)

    # -- 成片与产物 -------------------------------------------------------

    @app.get("/api/stories/{story_id}/prerender")
    def prerender(story_id: str) -> dict[str, Any]:
        """PreRender Commercial Gate V2 报告（12 项就绪 + 互检 + Contract V5）。"""
        pre = ws.orc.prerender_for(story_id)
        if pre is None:
            return {"story_id": story_id, "available": False,
                    "note": "尚未跑 PreRender Gate（需先到表演阶段）"}
        return {"story_id": story_id, "available": True,
                "gate": pre.gate.report(), "contracts_v5": pre.contracts_v5}

    @app.get("/api/stories/{story_id}/cut")
    def cut(story_id: str) -> dict[str, Any]:
        return svc.cut_view(story_id)

    @app.get("/api/stories/{story_id}/artifacts")
    def artifacts(story_id: str) -> dict[str, Any]:
        items = ws.list_artifacts(story_id)
        return {"count": len(items), "items": items,
                "bundle_root": str(ws.bundle_root_for(story_id) or "")}

    @app.get("/api/stories/{story_id}/file")
    def get_file(story_id: str, path: str = Query(...), download: bool = False):
        target = ws.resolve_file(story_id, path)
        media = {
            ".mp4": "video/mp4", ".html": "text/html; charset=utf-8",
            ".vtt": "text/vtt; charset=utf-8", ".json": "application/json",
            ".edl": "text/plain; charset=utf-8",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(target.suffix.lower(), "application/octet-stream")
        return FileResponse(
            target,
            media_type=media,
            filename=target.name if download else None,
        )

    return app
