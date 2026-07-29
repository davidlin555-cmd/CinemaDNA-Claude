"""CinemaDNA Web 控制台 (Phase 5)

浏览器主控：多故事看板 / 人工审核 Gate / 资产浏览器 / 工单 / 成片预览。

    python scripts/run_webui.py           # 启动，默认 http://127.0.0.1:8700
"""

from .app import create_app
from .services import FactoryService, next_action_for
from .state import FactoryWorkspace, get_workspace, set_workspace

__all__ = [
    "create_app",
    "FactoryWorkspace",
    "FactoryService",
    "next_action_for",
    "get_workspace",
    "set_workspace",
]
