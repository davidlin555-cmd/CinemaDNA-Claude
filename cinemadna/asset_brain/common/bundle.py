"""CinemaDNA / DramaOS-X Factory Core — Active Production Bundle 落盘 (Phase 1)

对应主规格 §二「Active Production Bundle 隔离」与接口文档验收标准 #7：
所有 Workorder / Gate Report / Backflow Record / 资产载荷必须落在
对应 Bundle 的标准 14 目录内，不允许写到 Bundle 之外。

Phase 1 只做本地文件系统落盘（JSON），不做对象存储 / 加密 / 归档策略。
Bundle 为可选依赖：Service 在 bundle=None 时仍可完整跑通内存闭环。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import schemas


class Bundle:
    """一个 story + bundle_id 对应的 Active Production Bundle 根目录。

    目录结构：<root>/<bundle_id>/<01_script .. 14_trash>/...
    """

    def __init__(self, root: str | Path, bundle_id: str) -> None:
        if not bundle_id:
            raise schemas.SchemaValidationError("bundle_id 不能为空")
        self.bundle_id = bundle_id
        self.root = Path(root) / bundle_id

    # -- 目录 ---------------------------------------------------------------

    def ensure(self) -> "Bundle":
        """创建 Bundle 根与标准 14 子目录（幂等）。"""
        for d in schemas.BUNDLE_DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        return self

    def path_for(self, relpath: str) -> Path:
        """把受校验的 Bundle 相对路径解析为绝对路径。

        relpath 必须以标准子目录开头且不含 '..'（由 schemas 强制）。
        """
        p = schemas.require_bundle_relpath(relpath)
        return self.root.joinpath(*p.parts)

    # -- 写入 ---------------------------------------------------------------

    def write_json(self, relpath: str, data: Any) -> str:
        """把结构化数据写入 Bundle 内指定相对路径，返回该相对路径。

        返回相对路径而非绝对路径：便于直接记录进 Workorder / Backflow，
        且不泄漏本机绝对路径。
        """
        target = self.path_for(relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        return relpath

    def read_json(self, relpath: str) -> Any:
        """读回 Bundle 内的 JSON（主要供测试与看板使用）。"""
        return json.loads(self.path_for(relpath).read_text(encoding="utf-8"))

    def exists(self, relpath: str) -> bool:
        return self.path_for(relpath).exists()
