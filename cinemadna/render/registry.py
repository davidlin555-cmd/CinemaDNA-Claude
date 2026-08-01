"""Backend Registry —— 模型名到真实后端的绑定 (Phase 4)

Model Router 决定"这个镜头该用 cloud-face-v1"，Registry 决定
"cloud-face-v1 由谁来执行"。**这就是 mock → 真实模型的切换点**：

    registry = BackendRegistry(default=MockRenderBackend())
    registry.register("cloud-face-v1", KlingBackend(api_key=...))
    registry.register("local-video-v1", LocalSVDBackend())

上线一个模型就注册一个，没注册的仍走默认后端。整条链路其余部分
（路由、参考注入、重试、一致性、拒渲）完全不用改。
"""

from __future__ import annotations

from typing import Any

from .backend import BackendCapabilities, MockRenderBackend, RenderBackend, RenderRequest


class BackendRegistry:
    """模型名 → 后端实例。"""

    def __init__(self, default: RenderBackend | None = None) -> None:
        self._backends: dict[str, RenderBackend] = {}
        self.default: RenderBackend = default or MockRenderBackend()

    def register(self, model: str, backend: RenderBackend) -> "BackendRegistry":
        if not isinstance(backend, RenderBackend):
            raise TypeError(
                f"{backend!r} 不满足 RenderBackend 协议（需要 name / capabilities / render）"
            )
        self._backends[model] = backend
        return self

    def unregister(self, model: str) -> None:
        self._backends.pop(model, None)

    def get(self, model: str) -> RenderBackend:
        """取该模型的后端；未注册则回落到默认后端。"""
        return self._backends.get(model, self.default)

    def has_real_backend(self, model: str) -> bool:
        return model in self._backends

    def capabilities_for(self, model: str) -> BackendCapabilities:
        return self.get(model).capabilities

    def check(self, request: RenderRequest) -> list[str]:
        """派单前检查该请求是否超出目标后端能力。"""
        return self.get(request.model).capabilities.check(request)

    def describe(self) -> dict[str, Any]:
        """给看板用的后端清单。"""
        rows = {
            model: {
                "backend": b.name,
                "produces_media": b.capabilities.produces_media,
                "supports_reference_images": b.capabilities.supports_reference_images,
                "is_async": b.capabilities.is_async,
                "max_duration_sec": b.capabilities.max_duration_sec,
            }
            for model, b in self._backends.items()
        }
        return {
            "registered": rows,
            "default_backend": self.default.name,
            "default_produces_media": self.default.capabilities.produces_media,
        }


def mock_registry() -> BackendRegistry:
    """全 mock 的 Registry（CI 默认）。"""
    return BackendRegistry(default=MockRenderBackend())


def placeholder_registry(**kwargs: Any) -> BackendRegistry:
    """全部走 ffmpeg 占位媒体的 Registry（演示用，能出真实 MP4）。"""
    from .backend import PlaceholderMediaBackend

    return BackendRegistry(default=PlaceholderMediaBackend(**kwargs))


def kling_registry(budget, **kwargs: Any) -> BackendRegistry:
    """全部走真实 Kling 文生视频的 Registry（**必须传预算闸**）。

    默认后端就是 Kling，因此任何模型路由都会落到真实付费提交——
    正因如此，预算闸是必填参数，没有它连 Registry 都建不起来。
    """
    from .budget import BudgetGate
    from .kling_backend import KlingTextToVideoBackend

    if not isinstance(budget, BudgetGate):
        raise TypeError("kling_registry 必须传入 BudgetGate（真实付费提交的前置保护）")
    return BackendRegistry(default=KlingTextToVideoBackend(budget=budget, **kwargs))

def acting_registry(budget, **kwargs: Any) -> BackendRegistry:
    """包含真实本地 Acting 模型支持的 Registry。"""
    from .local_acting_backend import LocalActingBackend

    registry = kling_registry(budget, **kwargs)
    registry.register("local-acting-v1", LocalActingBackend())
    return registry
