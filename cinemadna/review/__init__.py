"""统一专业审核层 (Phase F) —— 把各模块已有审核收敛到一致接口。

9 个生产模块各有专业审核，历史上报告格式不一（Gate / QA / 连续性 / 五维总审 …）。
本层把它们统一成同一个 `ReviewResult`：`module / verdict / issues / repair_code /
human_gate`，让 Orchestrator 与制片人控制台用同一套接口消费——

  - verdict=PASS    专业审核通过
  - verdict=REPAIR  未过 → repair_code 指向自动修复动作（自动打回，不停厂）
  - verdict=CONFIRM 专业审核通过、但属于 3 个终确认点之一，等人最终按钮
  - verdict=PENDING 该模块还没产出，无从审

`repair_code` 直接对齐 orchestrator.repair.POLICY；`human_gate` 是 3 个终确认之一。
"""

from .result import (
    MODULE_LABELS,
    MODULE_ORDER,
    VERDICT_CONFIRM,
    VERDICT_PASS,
    VERDICT_PENDING,
    VERDICT_REPAIR,
    ReviewResult,
)
from .registry import (
    from_asset_gates,
    from_audio,
    from_continuity,
    from_final,
    from_qa_report,
    from_script,
)

__all__ = [
    "ReviewResult", "MODULE_ORDER", "MODULE_LABELS",
    "VERDICT_PASS", "VERDICT_REPAIR", "VERDICT_CONFIRM", "VERDICT_PENDING",
    "from_script", "from_asset_gates", "from_continuity",
    "from_qa_report", "from_audio", "from_final",
]
