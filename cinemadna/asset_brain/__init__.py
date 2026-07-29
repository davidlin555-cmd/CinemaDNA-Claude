"""CinemaDNA / DramaOS-X Factory Core — 统一资产大脑包。

三大专用资产大模型 (Phase 1, mock 生成 + 真实 Gate)：
- SceneDNA    自然场景原子检索与重新布局
- IdentityDNA 多人脸融合身份（含"禁止单一真人克隆"硬性 Gate）
- PropDNA     道具生成与跨镜头状态连续性

统一入口：AssetBrainFacade。
"""

from .common.bundle import Bundle
from .common.quad import Quad
from .common.store import AssetBrainStore
from .facade import AssetBrainFacade
from .identity_dna.service import IdentityDNAService
from .prop_dna.service import PropDNAService
from .scene_dna.service import SceneDNAService

__all__ = [
    "AssetBrainFacade",
    "AssetBrainStore",
    "Bundle",
    "Quad",
    "SceneDNAService",
    "IdentityDNAService",
    "PropDNAService",
]
