"""Web 控制台的工厂状态层 (Phase 5)

整个控制台共享**一个** `PipelineOrchestrator`。原因：三大资产库要跨剧复用、
ScriptBrain 槽位要全局计数、表演圣经要跨故事累积 —— 这些都是工厂级状态，
不能每个请求新建一个。

线程安全：Orchestrator 与 AssetBrainStore 都是普通对象，没有内部锁；
而 FastAPI 会把同步端点丢进线程池并发执行。所以**所有**改状态的操作
都必须走 `workspace.lock`，否则并发点按钮会把工单簿写坏。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, TypeVar

from orchestrator.pipeline import PipelineOrchestrator
from render.backend import ffmpeg_available
from render.registry import BackendRegistry, mock_registry, placeholder_registry

T = TypeVar("T")

#: 渲染后端选项 → Registry 工厂
BACKEND_CHOICES: dict[str, Callable[[], BackendRegistry]] = {
    # 只出结构清单，最快，用于跑通流程
    "mock": mock_registry,
    # 出真实占位 MP4（需要 ffmpeg），用于浏览器里预览成片
    "placeholder": placeholder_registry,
}


class FactoryWorkspace:
    """一个可被 Web 控制台操作的工厂实例。"""

    def __init__(
        self,
        root: str | Path,
        *,
        max_concurrent_scripts: int = 2,
        bundle_date: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.bundles_root = self.root / "bundles"
        self.bundles_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.orc = PipelineOrchestrator(
            bundle_root=self.bundles_root,
            max_concurrent_scripts=max_concurrent_scripts,
            bundle_date=bundle_date,
        )
        #: 记录每部剧用的渲染后端，便于在页面上说明"这条片子是怎么来的"
        self.backend_choice: dict[str, str] = {}

    # -- 并发 -----------------------------------------------------------

    def call(self, fn: Callable[[], T]) -> T:
        """在工厂锁内执行一次操作。"""
        with self.lock:
            return fn()

    # -- 后端选择 -------------------------------------------------------

    @staticmethod
    def available_backends() -> dict[str, Any]:
        return {
            "choices": list(BACKEND_CHOICES),
            "default": "placeholder" if ffmpeg_available() else "mock",
            "ffmpeg_available": ffmpeg_available(),
            "note": (
                "placeholder 会用 ffmpeg 出真实可播放的 MP4，但画面是占位色板，"
                "不是模型生成的画面。"
            ),
        }

    def registry_for(self, choice: str | None) -> tuple[BackendRegistry, str]:
        name = choice or self.available_backends()["default"]
        if name not in BACKEND_CHOICES:
            raise ValueError(
                f"未知渲染后端 {name!r}，可选 {list(BACKEND_CHOICES)}"
            )
        if name == "placeholder" and not ffmpeg_available():
            raise ValueError("本机没有 ffmpeg，无法使用 placeholder 后端")
        return BACKEND_CHOICES[name](), name

    # -- Bundle 文件 ----------------------------------------------------

    def bundle_root_for(self, story_id: str) -> Path | None:
        b = self.orc.bundle_for(story_id)
        return b.root if b is not None else None

    def resolve_file(self, story_id: str, relpath: str) -> Path:
        """把相对路径解析为 Bundle 内的绝对路径，越界直接拒绝。

        Web 层直接暴露文件下载，路径校验是必须的：
        没有这一步，`?path=../../../etc/passwd` 就能读到 Bundle 之外。
        """
        root = self.bundle_root_for(story_id)
        if root is None:
            raise FileNotFoundError(f"故事 {story_id} 没有 Bundle")
        target = (root / relpath).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise PermissionError(f"路径越界: {relpath!r}")
        if not target.is_file():
            raise FileNotFoundError(f"文件不存在: {relpath}")
        return target

    def list_artifacts(self, story_id: str) -> list[dict[str, Any]]:
        """列出该剧 Bundle 内的全部产物（供产物区展示与下载）。"""
        root = self.bundle_root_for(story_id)
        if root is None:
            return []
        out: list[dict[str, Any]] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            out.append(
                {
                    "path": rel,
                    "dir": rel.split("/")[0],
                    "name": p.name,
                    "bytes": p.stat().st_size,
                    "kind": _kind_of(p.suffix.lower()),
                }
            )
        return out


def _kind_of(suffix: str) -> str:
    return {
        ".mp4": "video", ".webm": "video", ".mov": "video",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".html": "page", ".vtt": "subtitle", ".edl": "text",
        ".json": "json",
    }.get(suffix, "file")


#: 进程内单例（由 app 工厂注入，测试里可替换）
_workspace: FactoryWorkspace | None = None


def get_workspace() -> FactoryWorkspace:
    if _workspace is None:
        raise RuntimeError("工厂尚未初始化：请先调用 set_workspace()")
    return _workspace


def set_workspace(ws: FactoryWorkspace) -> FactoryWorkspace:
    global _workspace
    _workspace = ws
    return ws
