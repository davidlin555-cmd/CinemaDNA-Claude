"""CinemaDNA / DramaOS-X Factory Core — 通用约定层 (Phase 1)

本模块集中定义"三大资产模型 Phase 1 接口文档 §0 通用约定"里的全局规范，
供 quad / workorder / 三大 Service 共同引用，避免各处硬编码字符串。

覆盖：
- schema_version 常量登记表（所有结构化 JSON 必须带 schema_version）
- ISO 8601 UTC 时间戳工具
- Active Production Bundle 标准 14 目录 + 路径约束校验
- workorder_type 枚举
- 通用校验异常与轻量校验助手

注意：本层不做任何真实生成/搜索逻辑，只负责"规范"本身。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Final

# ---------------------------------------------------------------------------
# schema_version 登记表
# ---------------------------------------------------------------------------
# 每一种结构化 JSON 都必须携带 schema_version 字段，取值只能来自本表。
# 后续任何新增结构都应先在此登记，防止散落的魔法字符串。

SCHEMA_WORKORDER: Final = "cinemadna.workorder.v1"

SCHEMA_SCENE_GATE: Final = "cinemadna.scene_gate.v1"
SCHEMA_SCENE_BACKFLOW: Final = "cinemadna.scene_backflow.v1"

SCHEMA_IDENTITY_GATE: Final = "cinemadna.identity_gate.v1"
SCHEMA_CHARACTER_MASTER_PACK: Final = "cinemadna.character_master_pack.v1"

SCHEMA_PROP_GATE: Final = "cinemadna.prop_gate.v1"
SCHEMA_PROP_BACKFLOW: Final = "cinemadna.prop_backflow.v1"

# ScriptBrain（L4 执行大脑集群 · 编剧工厂）
SCHEMA_CONCEPT_BRIEF: Final = "cinemadna.concept_brief.v1"
SCHEMA_SHOW_BIBLE: Final = "cinemadna.show_bible.v1"
SCHEMA_EPISODE_OUTLINE: Final = "cinemadna.episode_outline.v1"
SCHEMA_SHOOTING_SCRIPT: Final = "cinemadna.shooting_script.v1"
SCHEMA_SCENE_EXPORT: Final = "cinemadna.scene_export.v1"
SCHEMA_CHARACTER_LIST: Final = "cinemadna.character_list.v1"
SCHEMA_SCRIPT_REVIEW: Final = "cinemadna.script_review.v1"
SCHEMA_MARKET_REVIEW: Final = "cinemadna.market_review.v1"

# DirectorDNA / PerformanceDNA / Render Brain（L4 执行大脑集群）
SCHEMA_SHOT_STRATEGY: Final = "cinemadna.shot_strategy.v1"
SCHEMA_SHOT_CONTRACT: Final = "cinemadna.shot_contract.v1"
SCHEMA_CONTINUITY_LEDGER: Final = "cinemadna.continuity_ledger.v1"
SCHEMA_CONTINUITY_CHECK: Final = "cinemadna.continuity_check.v1"
SCHEMA_ACTING_BEATS: Final = "cinemadna.acting_beats.v1"
SCHEMA_PERFORMANCE_BIBLE: Final = "cinemadna.performance_bible.v1"
SCHEMA_ROUTING_DECISION: Final = "cinemadna.routing_decision.v1"
SCHEMA_RENDER_PAYLOAD: Final = "cinemadna.render_payload.v1"
SCHEMA_RENDERED_SHOT: Final = "cinemadna.rendered_shot.v1"
SCHEMA_CONSISTENCY_REPORT: Final = "cinemadna.consistency_report.v1"
SCHEMA_ROUGH_CUT: Final = "cinemadna.rough_cut.v1"

# QA Critic + Repair Planner（镜头级质检与精准打回）
SCHEMA_VISION_QA: Final = "cinemadna.vision_qa.v1"
SCHEMA_PERFORMANCE_QA: Final = "cinemadna.performance_qa.v1"
SCHEMA_CONTINUITY_QA: Final = "cinemadna.continuity_qa.v1"
SCHEMA_GLOBAL_QA: Final = "cinemadna.global_qa.v1"
SCHEMA_REPAIR_PLAN: Final = "cinemadna.repair_plan.v1"

# AudioDNA（声音性别绑定 / 口型策略实测 / 字幕）
SCHEMA_AUDIO_CONTRACT: Final = "cinemadna.audio_contract.v1"
SCHEMA_AUDIO_REVIEW: Final = "cinemadna.audio_review.v1"

# 成片级 Final Review + QA Council（5 维总审）
SCHEMA_FINAL_REVIEW: Final = "cinemadna.final_review.v1"

# 复用引用：命中内部库时，在本剧 Bundle 内留一条"我用了哪个全局资产"的凭证
SCHEMA_ASSET_REUSE: Final = "cinemadna.asset_reuse.v1"

# Pipeline Orchestrator（L2 多故事状态机）
SCHEMA_STORY: Final = "cinemadna.story.v1"
SCHEMA_FACTORY_STATUS: Final = "cinemadna.factory_status.v1"

# 所有已登记的合法 schema_version 取值集合
KNOWN_SCHEMA_VERSIONS: Final[frozenset[str]] = frozenset(
    {
        SCHEMA_WORKORDER,
        SCHEMA_SCENE_GATE,
        SCHEMA_SCENE_BACKFLOW,
        SCHEMA_IDENTITY_GATE,
        SCHEMA_CHARACTER_MASTER_PACK,
        SCHEMA_PROP_GATE,
        SCHEMA_PROP_BACKFLOW,
        SCHEMA_CONCEPT_BRIEF,
        SCHEMA_SHOW_BIBLE,
        SCHEMA_EPISODE_OUTLINE,
        SCHEMA_SHOOTING_SCRIPT,
        SCHEMA_SCENE_EXPORT,
        SCHEMA_CHARACTER_LIST,
        SCHEMA_SCRIPT_REVIEW,
        SCHEMA_MARKET_REVIEW,
        SCHEMA_SHOT_STRATEGY,
        SCHEMA_SHOT_CONTRACT,
        SCHEMA_CONTINUITY_LEDGER,
        SCHEMA_CONTINUITY_CHECK,
        SCHEMA_ACTING_BEATS,
        SCHEMA_PERFORMANCE_BIBLE,
        SCHEMA_ROUTING_DECISION,
        SCHEMA_RENDER_PAYLOAD,
        SCHEMA_RENDERED_SHOT,
        SCHEMA_CONSISTENCY_REPORT,
        SCHEMA_ROUGH_CUT,
        SCHEMA_VISION_QA,
        SCHEMA_PERFORMANCE_QA,
        SCHEMA_CONTINUITY_QA,
        SCHEMA_GLOBAL_QA,
        SCHEMA_REPAIR_PLAN,
        SCHEMA_AUDIO_CONTRACT,
        SCHEMA_AUDIO_REVIEW,
        SCHEMA_FINAL_REVIEW,
        SCHEMA_ASSET_REUSE,
        SCHEMA_STORY,
        SCHEMA_FACTORY_STATUS,
    }
)

# ---------------------------------------------------------------------------
# Workorder 类型枚举
# ---------------------------------------------------------------------------

WORKORDER_TYPE_SCENE: Final = "SCENE"
WORKORDER_TYPE_IDENTITY: Final = "IDENTITY"
WORKORDER_TYPE_PROP: Final = "PROP"

WORKORDER_TYPES: Final[frozenset[str]] = frozenset(
    {WORKORDER_TYPE_SCENE, WORKORDER_TYPE_IDENTITY, WORKORDER_TYPE_PROP}
)

# ---------------------------------------------------------------------------
# Active Production Bundle 标准目录（主规格 §二 强制）
# ---------------------------------------------------------------------------
# 铁律：任何资产文件路径必须落在对应 Bundle 的以下子目录之一内。

BUNDLE_ROOT_NAME: Final = "_ACTIVE_PRODUCTION_BUNDLE"

BUNDLE_DIRS: Final[tuple[str, ...]] = (
    "01_script",
    "02_cast",
    "03_scene",
    "04_props",
    "05_performance",
    "06_shots",
    "07_payloads",       # 带 hash 的完整载荷
    "08_submissions",
    "09_downloads",      # 按 task_id 隔离
    "10_outputs",
    "11_review",
    "12_asset_backflow",
    "13_archive",
    "14_trash",
)

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class SchemaValidationError(ValueError):
    """结构/规范校验失败时抛出。

    继承 ValueError，便于调用方按需捕获而不必依赖本模块的具体类型。
    """


# ---------------------------------------------------------------------------
# 时间工具（ISO 8601 UTC）
# ---------------------------------------------------------------------------

# 形如 2026-07-23T04:00:00Z 或带小数秒 2026-07-23T04:00:00.123456Z
_ISO_UTC_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


def utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串，以 'Z' 结尾（秒级）。

    统一全系统时间格式，避免各处自造格式。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_iso_utc(value: object) -> bool:
    """判断字符串是否为合法的 ISO 8601 UTC（以 Z 结尾）时间戳。"""
    return isinstance(value, str) and bool(_ISO_UTC_RE.match(value))


def require_iso_utc(value: object, field_name: str = "timestamp") -> str:
    """校验并返回合法的 ISO 8601 UTC 时间戳，否则抛 SchemaValidationError。"""
    if not is_iso_utc(value):
        raise SchemaValidationError(
            f"字段 {field_name!r} 必须是 ISO 8601 UTC 时间戳（形如 "
            f"2026-07-23T04:00:00Z），实际得到: {value!r}"
        )
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# schema_version 校验
# ---------------------------------------------------------------------------


def require_schema_version(value: object, expected: str) -> str:
    """校验 schema_version 字段与期望值一致，且属于登记表。"""
    if value != expected:
        raise SchemaValidationError(
            f"schema_version 必须为 {expected!r}，实际得到: {value!r}"
        )
    if value not in KNOWN_SCHEMA_VERSIONS:
        raise SchemaValidationError(f"未登记的 schema_version: {value!r}")
    return value  # type: ignore[return-value]


def require_workorder_type(value: object) -> str:
    """校验 workorder_type 属于 {SCENE, IDENTITY, PROP}。"""
    if value not in WORKORDER_TYPES:
        raise SchemaValidationError(
            f"workorder_type 必须是 {sorted(WORKORDER_TYPES)} 之一，"
            f"实际得到: {value!r}"
        )
    return value  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Bundle 路径约束
# ---------------------------------------------------------------------------


def is_valid_bundle_subdir(subdir: str) -> bool:
    """判断给定名称是否为标准 Bundle 子目录之一。"""
    return subdir in BUNDLE_DIRS


def require_bundle_relpath(relpath: str) -> PurePosixPath:
    """校验资产相对路径合法：必须落在标准 Bundle 子目录内，且不越界。

    规则（Phase 1）：
    - 使用 POSIX 风格相对路径（正斜杠），不允许绝对路径。
    - 首段必须是 BUNDLE_DIRS 之一。
    - 不允许出现 '..' 逃逸段。

    返回规范化后的 PurePosixPath 供调用方拼接到具体 Bundle 根。
    """
    if not isinstance(relpath, str) or not relpath.strip():
        raise SchemaValidationError(f"Bundle 相对路径不能为空: {relpath!r}")

    p = PurePosixPath(relpath)
    if p.is_absolute():
        raise SchemaValidationError(f"Bundle 相对路径不能是绝对路径: {relpath!r}")

    parts = p.parts
    if ".." in parts:
        raise SchemaValidationError(f"Bundle 相对路径不允许包含 '..': {relpath!r}")

    if not parts or parts[0] not in BUNDLE_DIRS:
        raise SchemaValidationError(
            f"Bundle 相对路径首段必须是标准子目录之一 {BUNDLE_DIRS}，"
            f"实际首段: {parts[0] if parts else None!r}"
        )
    return p
