"""CinemaDNA / DramaOS-X Factory Core — 三大资产 Service 的公共基类 (Phase 1)

把 SceneDNA / IdentityDNA / PropDNA 共有的机械动作收敛到一处：
- 依赖注入（内存 store + 可选 Bundle 落盘）
- Workorder 解析（接受 dict 或 Workorder 对象，统一取回工单簿里的真身）
- 状态前置断言（例如 backflow 前必须是 APPROVED）
- Bundle 写入的空实现兜底（bundle=None 时静默跳过，不影响内存闭环）

不放任何业务判断：检索、生成、Gate 一律由各 Service 自己实现，
避免基类偷偷承担红线逻辑而难以审计。
"""

from __future__ import annotations

from typing import Any

from .bundle import Bundle
from .quad import Quad
from .store import AssetBrainStore
from .workorder import InvalidStateTransition, Workorder, WorkorderStatus


class AssetServiceError(RuntimeError):
    """资产 Service 层的通用错误（工单不存在、前置状态不满足等）。"""


class BaseAssetService:
    """三大资产 Service 的公共基类。"""

    #: 子类覆写：SCENE / IDENTITY / PROP
    workorder_type: str = ""
    #: 子类覆写：工单 ID 前缀，如 wo_scenedna
    workorder_prefix: str = "wo"

    def __init__(
        self, store: AssetBrainStore | None = None, bundle: Bundle | None = None
    ) -> None:
        self.store = store if store is not None else AssetBrainStore()
        self.bundle = bundle

    # -- 上下文 -------------------------------------------------------------

    @staticmethod
    def _check_context(context: Quad) -> Quad:
        """任何入口都必须携带合法四元组上下文（asset_hash 可空）。"""
        if not isinstance(context, Quad):
            raise AssetServiceError(
                f"context 必须是 Quad，实际: {type(context).__name__}"
            )
        return context.validate()

    # -- Workorder ----------------------------------------------------------

    def _resolve(self, workorder: Workorder | dict[str, Any]) -> Workorder:
        """接受 Workorder 对象或其 dict 表示，返回工单簿内的真身。

        接口文档里的签名是 dict-in/dict-out，但状态机必须作用在同一个对象上，
        所以这里统一回查 store，杜绝"改了副本、状态没生效"的隐患。
        """
        if isinstance(workorder, Workorder):
            wo = self.store.get_workorder(workorder.workorder_id) or workorder
            return wo
        if isinstance(workorder, dict):
            wid = workorder.get("workorder_id")
            wo = self.store.get_workorder(str(wid)) if wid else None
            if wo is None:
                raise AssetServiceError(f"工单不存在于工单簿: {wid!r}")
            return wo
        raise AssetServiceError(
            f"workorder 必须是 Workorder 或 dict，实际: {type(workorder).__name__}"
        )

    @staticmethod
    def _require_status(wo: Workorder, *expected: WorkorderStatus) -> Workorder:
        """断言工单处于允许的前置状态之一，否则拒绝执行。"""
        if wo.status not in expected:
            raise InvalidStateTransition(
                f"工单 {wo.workorder_id} 当前状态 {wo.status.value}，"
                f"该操作要求 {[e.value for e in expected]}"
            )
        return wo

    # -- Bundle 落盘 ---------------------------------------------------------

    def _write(self, relpath: str, data: Any) -> str | None:
        """写入 Bundle（未配置 Bundle 时返回 None，不报错）。"""
        if self.bundle is None:
            return None
        return self.bundle.write_json(relpath, data)
