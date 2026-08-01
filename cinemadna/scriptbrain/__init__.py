"""ScriptBrain V4.1 旗舰编剧工厂 —— Phase 2 最小可运行版。

一句题材进，标准化拍摄版 JSON 出：

    from scriptbrain import ScriptBrainService
    result = ScriptBrainService().run("县城女护士被高利贷追债")
    result.shooting_script   # 完整拍摄版
    result.scene_export      # 资产大脑直接消费的精简视图
"""

from .brief import ScriptBrief, ScriptBriefError
from .library import ALL_GENRES, GenreTemplate, get_genre, match_genre
from .script import (
    ScriptValidationError,
    build_character_list,
    build_scene_export,
    validate_shooting_script,
)
from .service import (
    GATE_CONCEPT,
    GATE_OUTLINE,
    GATE_SHOOTING_SCRIPT,
    ScriptBrainService,
    ScriptResult,
)

__all__ = [
    "ScriptBrainService",
    "ScriptResult",
    "ScriptBrief",
    "ScriptBriefError",
    "ScriptValidationError",
    "validate_shooting_script",
    "build_scene_export",
    "build_character_list",
    "GenreTemplate",
    "ALL_GENRES",
    "get_genre",
    "match_genre",
    "GATE_CONCEPT",
    "GATE_OUTLINE",
    "GATE_SHOOTING_SCRIPT",
]
