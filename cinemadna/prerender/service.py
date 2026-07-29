"""PreRender 协同服务 (Phase C) —— 把 Contract V5 + Cross-Validation + Gate 串起来

给 Orchestrator 一个入口：对一部剧的全部合约跑 PreRender Commercial Gate V2，
产出报告并落 Bundle。渲染前必须先过这一关（PDF 铁律：没过就禁止渲染）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from asset_brain.common.bundle import Bundle

from .contract_v5 import contract_v5_view
from .gate import PreRenderGateResult, run_prerender_gate


@dataclass
class PreRenderStoryResult:
    gate: PreRenderGateResult
    contracts_v5: list[dict[str, Any]]

    @property
    def ready(self) -> bool:
        return self.gate.ready

    def summary(self) -> dict[str, Any]:
        return {
            "status": self.gate.status,
            "ready": self.gate.ready,
            "flags": self.gate.flags,
            "blocker_count": len(self.gate.blockers),
            "cross_validation_passed": self.gate.cross_validation.get("passed"),
            "cross_validation_pending": len(
                self.gate.cross_validation.get("pending") or []),
            "next_action": self.gate.next_action,
        }


class PreRenderService:
    def __init__(self, bundle: Bundle | None = None) -> None:
        self.bundle = bundle

    def _asset_checker(self):
        """给互检一个"真实资产文件是否存在"的检查器。

        既支持本剧 Bundle 内的相对路径，也支持跨剧回流复用时存下的绝对路径
        （SceneDNA/PropDNA 回流记录同时存 relpath 和 abspath）。
        """
        if self.bundle is None:
            return None
        from pathlib import Path
        def check(relpath: str):
            try:
                p = Path(relpath)
                if not p.is_absolute():
                    p = self.bundle.path_for(relpath)
                if p.is_file():
                    return {"exists": True, "bytes": p.stat().st_size}
            except Exception:
                pass
            return {"exists": False, "bytes": 0}
        return check

    def run(
        self, contracts: list[dict[str, Any]], *, story_id: str,
        characters_by_id: dict[str, dict[str, Any]] | None = None,
        script_ready: bool = True,
        require_real_faces: bool = False,
        require_real_assets: bool = False,
    ) -> PreRenderStoryResult:
        gate = run_prerender_gate(
            contracts, story_id=story_id,
            characters_by_id=characters_by_id, script_ready=script_ready,
            asset_checker=self._asset_checker(),
            require_real_faces=require_real_faces,
            require_real_assets=require_real_assets)
        v5 = [contract_v5_view(c) for c in contracts]
        if self.bundle is not None:
            self.bundle.write_json(
                f"11_review/prerender_gate/{story_id}.json", gate.report())
            self.bundle.write_json(
                f"06_shots/contract_v5/{story_id}.json",
                {"story_id": story_id, "contracts": v5})
        return PreRenderStoryResult(gate=gate, contracts_v5=v5)
