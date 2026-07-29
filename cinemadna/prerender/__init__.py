"""剧本驱动多模型协同：Contract V5 + Cross-Model Validation + PreRender Gate V2

对应 PDF《剧本驱动多模型协同生产》要求的三块缺口：

- Shot Production Contract V5：每镜头完整生产合同（命名子合同）
- Cross-Model Validation：子模型互相校验能否共同组成一场戏（防素材拼接感）
- PreRender Commercial Gate V2：渲染前 12 项 *_ready 合并总闸

铁律：没过 Cross-Model Validation / PreRender Gate 的镜头禁止渲染。
"""

from .contract_v5 import contract_v5_view, enrich_contract_v5
from .cross_validation import cross_model_validate
from .gate import PreRenderGateResult, run_prerender_gate
from .service import PreRenderService, PreRenderStoryResult

__all__ = [
    "enrich_contract_v5",
    "contract_v5_view",
    "cross_model_validate",
    "run_prerender_gate",
    "PreRenderGateResult",
    "PreRenderService",
    "PreRenderStoryResult",
]
