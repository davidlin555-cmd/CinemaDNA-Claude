"""CinemaDNA / DramaOS-X Factory Core — 资产大脑内存存储 (Phase 1)

Phase 1 用进程内字典模拟三大数据库与工单簿：

- scene_atoms        : SceneDNA 自然场景原子 / 组合布局库
- character_registry : IdentityDNA Character Registry（Character Master Pack）
- prop_library       : PropDNA 道具库（含状态版本）
- workorders         : 全部 Workorder（三类共用同一本工单簿）
- gate_reports       : 全部 Gate Report
- backflow_records   : 全部 Backflow Record

后续 Phase 用真实数据库替换本类即可，Service 只依赖这里的方法签名。

注意：本层不做任何质量判断，只负责存与查。Gate 逻辑一律在各 Service 的
gate.py 内，避免"存储层偷偷放行"。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .workorder import Workorder

#: 视为资产文件的后缀（持久化时复制进工厂目录，扛 bundle wipe）
_ASSET_EXT = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov", ".wav", ".mp3")


class AssetBrainStore:
    """统一资产大脑存储 + 自增 ID 分配器。

    默认纯内存（`AssetBrainStore()` 行为不变）。传 `library_dir` → **可复用库跨生产
    落盘持久化**（工厂资产库地基）：scene_atoms/character_registry/prop_library 及 ID
    计数器在 put 时写盘、init 时回读 → 让各 DNA 的 reuse-first"查库命中→复用"真正跨
    生产触发（否则每片从零重造）。workorders/gate/backflow 仍是每 run 临时，不持久化。
    """

    #: 跨生产持久化的可复用库（其余为每 run 临时）
    _PERSISTED = ("scene_atoms", "character_registry", "prop_library")

    def __init__(self, *, library_dir: str | Path | None = None) -> None:
        self.scene_atoms: dict[str, dict[str, Any]] = {}
        self.character_registry: dict[str, dict[str, Any]] = {}
        self.prop_library: dict[str, dict[str, Any]] = {}
        self.workorders: dict[str, Workorder] = {}
        self.gate_reports: dict[str, dict[str, Any]] = {}
        self.backflow_records: dict[str, dict[str, Any]] = {}
        self._counters: dict[str, int] = {}
        self.library_dir = Path(library_dir) if library_dir else None
        if self.library_dir is not None:
            self._load_library()

    # -- 工厂资产库持久化（可选，跨生产复用地基） --------------------------

    def _load_library(self) -> None:
        for name in self._PERSISTED:
            p = self.library_dir / f"{name}.json"
            if p.is_file():
                try:
                    getattr(self, name).update(json.loads(p.read_text("utf-8")))
                except (ValueError, OSError):
                    pass
        cp = self.library_dir / "_counters.json"
        if cp.is_file():
            try:
                self._counters.update(json.loads(cp.read_text("utf-8")))
            except (ValueError, OSError):
                pass

    @staticmethod
    def _is_library_quality(record: dict[str, Any]) -> bool:
        """1d 质量门：只有真实/过闸资产进持久库，坏的(mock/未过/非真图)不污染。"""
        if not isinstance(record, dict):
            return False
        if record.get("mock_mode") is True:
            return False
        if record.get("is_real_image") is False:
            return False
        if record.get("gate_passed") is False:
            return False
        return True

    def _archive_files(self, record: Any) -> Any:
        """1b：把记录里的资产文件复制进工厂 assets/ 并改写路径（扛 bundle wipe）。
        返回**新记录**（不改内存原件——当前 run 仍用 bundle 内路径）。"""
        assets = self.library_dir / "assets"

        def walk(v: Any) -> Any:
            if isinstance(v, str) and v.lower().endswith(_ASSET_EXT):
                p = Path(v)
                try:
                    resolved = p.resolve()
                except OSError:
                    return v
                if p.is_file() and self.library_dir.resolve() not in resolved.parents:
                    assets.mkdir(parents=True, exist_ok=True)
                    key = hashlib.sha1(str(resolved).encode()).hexdigest()[:8]
                    dst = assets / f"{key}_{p.name}"
                    if not dst.is_file():
                        dst.write_bytes(p.read_bytes())
                    return str(dst.resolve())
                return v
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return v

        return walk(record)

    def _persist(self, name: str) -> None:
        """只把**过质量门**的资产写进持久库，并把其文件归档进工厂目录（1b+1c+1d）。
        best-effort：持久化任何失败都**绝不破坏生产**（库是加成，不是关键路径）。"""
        if self.library_dir is None:
            return
        try:
            self.library_dir.mkdir(parents=True, exist_ok=True)
            out = {k: self._archive_files(v)
                   for k, v in getattr(self, name).items()
                   if self._is_library_quality(v)}
            (self.library_dir / f"{name}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
            (self.library_dir / "_counters.json").write_text(
                json.dumps(self._counters, ensure_ascii=False), "utf-8")
        except Exception:  # noqa: BLE001 —— 持久化失败不挡出片
            pass

    # -- ID 分配 ------------------------------------------------------------

    def next_id(self, prefix: str) -> str:
        """按前缀分配自增 ID，形如 wo_scenedna_0001。

        自增而非时间戳/随机：保证测试与回放可复现。
        """
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n:04d}"

    # -- Workorder ----------------------------------------------------------

    def put_workorder(self, wo: Workorder) -> Workorder:
        self.workorders[wo.workorder_id] = wo
        return wo

    def get_workorder(self, workorder_id: str) -> Workorder | None:
        return self.workorders.get(workorder_id)

    def list_workorders(
        self,
        *,
        story_id: str | None = None,
        workorder_type: str | None = None,
        status: str | None = None,
    ) -> list[Workorder]:
        """按故事 / 类型 / 状态过滤工单，供并行工厂看板使用。"""
        out: Iterable[Workorder] = self.workorders.values()
        if story_id is not None:
            out = (w for w in out if w.quad.story_id == story_id)
        if workorder_type is not None:
            out = (w for w in out if w.workorder_type == workorder_type)
        if status is not None:
            out = (w for w in out if w.status.value == status)
        return list(out)

    # -- Gate / Backflow ----------------------------------------------------

    def put_gate_report(self, report: dict[str, Any]) -> dict[str, Any]:
        self.gate_reports[report["gate_id"]] = report
        return report

    def get_gate_report(self, gate_id: str) -> dict[str, Any] | None:
        return self.gate_reports.get(gate_id)

    def put_backflow(self, record: dict[str, Any]) -> dict[str, Any]:
        key = record.get("backflow_id") or record.get("master_pack_id")
        if not key:
            raise ValueError("Backflow 记录必须含 backflow_id 或 master_pack_id")
        self.backflow_records[key] = record
        return record

    # -- 三大库写入 ---------------------------------------------------------

    def put_scene_atom(self, asset_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self.scene_atoms[asset_id] = record
        self._persist("scene_atoms")
        return record

    def put_character(self, character_id: str, pack: dict[str, Any]) -> dict[str, Any]:
        self.character_registry[character_id] = pack
        self._persist("character_registry")
        return pack

    def put_prop(self, prop_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self.prop_library[prop_id] = record
        self._persist("prop_library")
        return record

    # -- 统计（供 Web 看板）-------------------------------------------------

    def stats(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for wo in self.workorders.values():
            by_status[wo.status.value] = by_status.get(wo.status.value, 0) + 1
        return {
            "scene_atoms": len(self.scene_atoms),
            "characters": len(self.character_registry),
            "props": len(self.prop_library),
            "workorders": len(self.workorders),
            "workorders_by_status": by_status,
            "gate_reports": len(self.gate_reports),
            "backflow_records": len(self.backflow_records),
        }
