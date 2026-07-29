"""真实资产生成 (Phase A) —— 把三大资产从 mock 结构升级为真实图像。

当前落地：IdentityDNA 真实人脸（Together FLUX 文生图，预算闸保护）。
后续同一接口可扩到 SceneDNA / PropDNA。
"""

from .image_backend import (
    ImageBackend,
    ImageGenError,
    ImageResult,
    TogetherImageBackend,
)

__all__ = ["ImageBackend", "ImageResult", "ImageGenError", "TogetherImageBackend"]
