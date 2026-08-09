"""ScriptBrain — 输入题材 Brief (Phase 2)

用户侧的输入应当极简：给一个题材/方向就能开工。
本模块负责把这句话规整成结构化的 `ScriptBrief`，并做题材归类。

Phase 2 不接 LLM：题材归类走关键词命中 + 确定性兜底，
保证同一句话永远得到同一部剧（可复现、可回归测试）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cinemadna.asset_brain.common import schemas


class ScriptBriefError(ValueError):
    """Brief 非法。"""


#: 每集最少场景数 —— 少于 3 场就凑不齐「开头钩子 + 中段冲突 + 结尾悬念」
MIN_SCENES_PER_EPISODE = 3
MAX_SCENES_PER_EPISODE = 8
MAX_EPISODES = 20


@dataclass(frozen=True)
class ScriptBrief:
    """一次剧本生产的输入。"""

    theme: str
    genre: str | None = None          # 不给则自动归类
    episode_count: int = 1
    scenes_per_episode: int = 3
    target_platform: str = "shortdrama"
    tone: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.theme, str) or not self.theme.strip():
            raise ScriptBriefError(f"题材/方向不能为空: {self.theme!r}")
        if not 1 <= self.episode_count <= MAX_EPISODES:
            raise ScriptBriefError(
                f"episode_count 必须在 1~{MAX_EPISODES}，实际 {self.episode_count}"
            )
        if not MIN_SCENES_PER_EPISODE <= self.scenes_per_episode <= MAX_SCENES_PER_EPISODE:
            raise ScriptBriefError(
                f"scenes_per_episode 必须在 {MIN_SCENES_PER_EPISODE}~"
                f"{MAX_SCENES_PER_EPISODE}（少于 3 场凑不齐钩子/冲突/悬念），"
                f"实际 {self.scenes_per_episode}"
            )

    @classmethod
    def coerce(cls, value: "ScriptBrief | str | dict[str, Any]") -> "ScriptBrief":
        """允许直接传一句话或 dict，方便 Web / 脚本调用。"""
        if isinstance(value, ScriptBrief):
            return value
        if isinstance(value, str):
            return cls(theme=value)
        if isinstance(value, dict):
            known = {
                "theme", "genre", "episode_count", "scenes_per_episode",
                "target_platform", "tone",
            }
            kwargs = {k: v for k, v in value.items() if k in known}
            extra = {k: v for k, v in value.items() if k not in known}
            return cls(**kwargs, extra=extra)
        raise ScriptBriefError(f"无法识别的 brief 类型: {type(value).__name__}")

    @property
    def seed(self) -> str:
        """确定性种子：同一 brief 永远生成同一部剧。"""
        return f"{self.theme}|{self.genre}|{self.episode_count}|{self.scenes_per_episode}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": schemas.SCHEMA_CONCEPT_BRIEF,
            "theme": self.theme,
            "genre": self.genre,
            "episode_count": self.episode_count,
            "scenes_per_episode": self.scenes_per_episode,
            "target_platform": self.target_platform,
            "tone": self.tone,
            "extra": dict(self.extra),
        }
