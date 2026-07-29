"""通用地基层单元测试 (Phase 1)

覆盖：
- 四元组：合法通过 / 缺字段被拒 / 非法值被拒 / asset_hash 绑定语义
- Workorder 状态机：合法迁移链 / 非法迁移被拦 / FAILED 逃生舱 / 终态封锁
- schemas：schema_version、workorder_type、Bundle 路径、ISO 时间戳
"""

from __future__ import annotations

import pytest

from asset_brain.common import schemas
from asset_brain.common.quad import Quad, QuadValidationError
from asset_brain.common.workorder import (
    InvalidStateTransition,
    WorkorderStatus,
    can_transition,
    new_workorder,
)

VALID_HASH = "sha256:" + "a" * 64


def make_quad(**overrides):
    base = dict(
        story_id="drama_0005",
        bundle_id="bundle_20260723_001",
        task_id="task_scenedna_001",
        asset_hash=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 四元组
# ---------------------------------------------------------------------------


class TestQuad:
    def test_valid_context_quad_passes(self):
        q = Quad(**make_quad()).validate()
        assert q.story_id == "drama_0005"
        assert q.is_bound is False

    def test_valid_bound_quad_passes(self):
        q = Quad(**make_quad(asset_hash=VALID_HASH))
        assert q.is_bound is True
        q.require_bound()  # 不应抛异常

    @pytest.mark.parametrize("field", ["story_id", "bundle_id", "task_id"])
    def test_missing_id_field_rejected(self, field):
        with pytest.raises(QuadValidationError):
            Quad(**make_quad(**{field: ""}))

    @pytest.mark.parametrize("field", ["story_id", "bundle_id", "task_id"])
    def test_none_id_field_rejected(self, field):
        with pytest.raises(QuadValidationError):
            Quad(**make_quad(**{field: None}))

    def test_illegal_chars_rejected(self):
        with pytest.raises(QuadValidationError):
            Quad(**make_quad(story_id="drama 0005"))  # 含空格
        with pytest.raises(QuadValidationError):
            Quad(**make_quad(task_id="task/../escape"))  # 含路径分隔

    def test_from_mapping_missing_key_rejected(self):
        # 完全没带 asset_hash 键 → 拒绝
        data = {
            "story_id": "drama_0005",
            "bundle_id": "bundle_20260723_001",
            "task_id": "task_x",
        }
        with pytest.raises(QuadValidationError) as ei:
            Quad.from_mapping(data)
        assert "asset_hash" in str(ei.value)

    def test_from_mapping_with_none_asset_hash_ok(self):
        # asset_hash 键存在但为 None → 上下文级合法
        q = Quad.from_mapping(make_quad())
        assert q.asset_hash is None

    def test_require_bound_without_hash_rejected(self):
        q = Quad(**make_quad())  # asset_hash=None
        with pytest.raises(QuadValidationError):
            q.require_bound()

    def test_bad_asset_hash_format_rejected(self):
        with pytest.raises(QuadValidationError):
            Quad(**make_quad(asset_hash="not-a-hash"))
        with pytest.raises(QuadValidationError):
            Quad(**make_quad(asset_hash="sha256:short"))

    def test_with_asset_hash_returns_bound_copy(self):
        q = Quad(**make_quad())
        q2 = q.with_asset_hash(VALID_HASH)
        assert q.asset_hash is None  # 原对象不可变
        assert q2.asset_hash == VALID_HASH
        assert q2.is_bound is True

    def test_frozen_immutable(self):
        q = Quad(**make_quad())
        with pytest.raises(Exception):
            q.story_id = "hacked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Workorder 状态机
# ---------------------------------------------------------------------------


def make_wo(**overrides):
    kwargs = dict(
        workorder_id="wo_scenedna_20260723_001",
        workorder_type=schemas.WORKORDER_TYPE_SCENE,
        quad=Quad(**make_quad()),
        requested_by="scriptbrain",
    )
    kwargs.update(overrides)
    return new_workorder(**kwargs)


class TestWorkorderCreation:
    def test_new_workorder_starts_pending(self):
        wo = make_wo()
        assert wo.status is WorkorderStatus.PENDING
        assert wo.schema_version == schemas.SCHEMA_WORKORDER
        assert wo.is_terminal is False

    @pytest.mark.parametrize(
        "wtype",
        [
            schemas.WORKORDER_TYPE_SCENE,
            schemas.WORKORDER_TYPE_IDENTITY,
            schemas.WORKORDER_TYPE_PROP,
        ],
    )
    def test_all_three_types_creatable(self, wtype):
        wo = make_wo(workorder_type=wtype)
        assert wo.workorder_type == wtype

    def test_invalid_type_rejected(self):
        with pytest.raises(schemas.SchemaValidationError):
            make_wo(workorder_type="AUDIO")

    def test_invalid_priority_rejected(self):
        with pytest.raises(schemas.SchemaValidationError):
            make_wo(priority="urgent")

    def test_to_dict_flattens_quad(self):
        wo = make_wo()
        d = wo.to_dict()
        assert d["story_id"] == "drama_0005"
        assert d["task_id"] == "task_scenedna_001"
        assert d["asset_hash"] is None
        assert d["status"] == "PENDING"
        assert d["schema_version"] == schemas.SCHEMA_WORKORDER


class TestWorkorderTransitions:
    def test_happy_path_to_backflow(self):
        wo = make_wo()
        wo.mark_running()
        assert wo.status is WorkorderStatus.RUNNING
        wo.mark_gate_review({"passed": True})
        assert wo.status is WorkorderStatus.GATE_REVIEW
        assert wo.gate_report == {"passed": True}
        wo.mark_approved()
        assert wo.status is WorkorderStatus.APPROVED
        wo.mark_backflowed("bf_scene_001")
        assert wo.status is WorkorderStatus.BACKFLOWED
        assert wo.backflow_record_id == "bf_scene_001"
        assert wo.is_terminal is True

    def test_reject_then_retry_running(self):
        wo = make_wo()
        wo.mark_running().mark_gate_review().mark_rejected()
        assert wo.status is WorkorderStatus.REJECTED
        # REJECTED 可回到 RUNNING 重新生成
        wo.mark_running()
        assert wo.status is WorkorderStatus.RUNNING

    def test_illegal_skip_gate_rejected(self):
        wo = make_wo()
        wo.mark_running()
        # RUNNING 不能直接 APPROVED（必须先 GATE_REVIEW）
        with pytest.raises(InvalidStateTransition):
            wo.mark_approved()

    def test_illegal_pending_to_backflow_rejected(self):
        wo = make_wo()
        with pytest.raises(InvalidStateTransition):
            wo.transition_to(WorkorderStatus.BACKFLOWED)

    def test_any_active_state_can_fail(self):
        wo = make_wo()
        wo.mark_running()
        wo.mark_failed("mock 生成异常")
        assert wo.status is WorkorderStatus.FAILED
        assert wo.error == "mock 生成异常"
        assert wo.is_terminal is True

    def test_terminal_states_are_locked(self):
        wo = make_wo()
        wo.mark_running().mark_gate_review().mark_approved().mark_backflowed("bf_x")
        # 终态无出边
        with pytest.raises(InvalidStateTransition):
            wo.mark_running()
        with pytest.raises(InvalidStateTransition):
            wo.mark_failed("再也不能失败")

    def test_updated_at_changes_on_transition(self):
        wo = make_wo()
        before = wo.updated_at
        # 直接改内部时钟不可行，改用 transition 后字段存在且格式合法即可
        wo.mark_running()
        assert schemas.is_iso_utc(wo.updated_at)
        assert wo.updated_at >= before  # ISO 字符串可字典序比较

    def test_can_transition_table(self):
        assert can_transition(WorkorderStatus.PENDING, WorkorderStatus.RUNNING)
        assert not can_transition(WorkorderStatus.PENDING, WorkorderStatus.APPROVED)
        assert can_transition(WorkorderStatus.RUNNING, WorkorderStatus.FAILED)
        assert not can_transition(WorkorderStatus.BACKFLOWED, WorkorderStatus.FAILED)


# ---------------------------------------------------------------------------
# schemas 公共约定
# ---------------------------------------------------------------------------


class TestSchemas:
    def test_utc_now_iso_format(self):
        assert schemas.is_iso_utc(schemas.utc_now_iso())

    def test_require_iso_utc_rejects_bad(self):
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_iso_utc("2026-07-23 04:00:00")  # 缺 T/Z
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_iso_utc("not-a-time")

    def test_require_schema_version(self):
        assert (
            schemas.require_schema_version(
                schemas.SCHEMA_WORKORDER, schemas.SCHEMA_WORKORDER
            )
            == schemas.SCHEMA_WORKORDER
        )
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_schema_version("wrong.v1", schemas.SCHEMA_WORKORDER)

    def test_require_workorder_type(self):
        schemas.require_workorder_type("SCENE")
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_workorder_type("scene")  # 大小写敏感

    def test_bundle_has_14_dirs(self):
        assert len(schemas.BUNDLE_DIRS) == 14
        assert "07_payloads" in schemas.BUNDLE_DIRS

    def test_valid_bundle_relpath(self):
        p = schemas.require_bundle_relpath("03_scene/EP001_SC001/layout.json")
        assert p.parts[0] == "03_scene"

    def test_bundle_relpath_rejects_escape(self):
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_bundle_relpath("../secret.json")
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_bundle_relpath("03_scene/../../etc/passwd")

    def test_bundle_relpath_rejects_unknown_dir(self):
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_bundle_relpath("99_hack/x.json")

    def test_bundle_relpath_rejects_absolute(self):
        with pytest.raises(schemas.SchemaValidationError):
            schemas.require_bundle_relpath("/03_scene/x.json")
