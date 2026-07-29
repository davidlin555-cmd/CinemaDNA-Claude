"""CinemaDNA / DramaOS-X Factory Core — 四元组 Quad (Phase 1)

铁律（主规格 §七 / 接口文档 §0.1）：
    任何资产操作、Workorder、Gate、Backflow 必须携带完整四元组：
        story_id + bundle_id + task_id + asset_hash
    没有完整四元组的请求一律拒绝。

说明：
- asset_hash 允许在"资产尚未产出"阶段为 None（例如 Workorder 刚创建、
  尚未 mock 生成时），此时四元组仍算"上下文合法"，但不允许进入需要
  asset_hash 的操作（如 Backflow / 渲染注册）。
- 为区分这两种严格程度，提供 validate()（上下文级，asset_hash 可空）
  与 require_bound()（资产级，asset_hash 必须存在）两个入口。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Mapping

# story_id 形如 drama_0005；bundle_id 形如 bundle_20260723_001；
# task_id 形如 task_scenedna_001。统一要求非空、无空白、可作路径片段。
_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]+$")

# asset_hash 形如 sha256:<hex>。Phase 1 只校验格式而不校验真实内容。
_ASSET_HASH_RE: Final = re.compile(r"^sha256:[0-9a-fA-F]{64}$")

_REQUIRED_FIELDS: Final = ("story_id", "bundle_id", "task_id", "asset_hash")


class QuadValidationError(ValueError):
    """四元组校验失败时抛出。继承 ValueError 便于统一捕获。"""


def _check_id(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise QuadValidationError(f"四元组字段 {field_name!r} 缺失或为空: {value!r}")
    if not _ID_RE.match(value):
        raise QuadValidationError(
            f"四元组字段 {field_name!r} 含非法字符（仅允许字母数字 . _ -）: {value!r}"
        )
    return value


def _check_asset_hash(value: Any, *, allow_none: bool) -> str | None:
    if value is None:
        if allow_none:
            return None
        raise QuadValidationError(
            "asset_hash 缺失：该操作要求资产已绑定 hash（不允许 None）"
        )
    if not isinstance(value, str) or not _ASSET_HASH_RE.match(value):
        raise QuadValidationError(
            f"asset_hash 格式非法，应为 'sha256:<64位hex>'，实际得到: {value!r}"
        )
    return value


@dataclass(frozen=True)
class Quad:
    """不可变四元组：绑定一次生产行为的完整身份。

    frozen=True 保证创建后不可篡改，符合"四元组一经绑定不可更改"的铁律。
    """

    story_id: str
    bundle_id: str
    task_id: str
    asset_hash: str | None = None

    # -- 构造与校验 ---------------------------------------------------------

    def __post_init__(self) -> None:
        # 数据类构造即做上下文级校验：三个 id 必须合法，asset_hash 可为 None。
        _check_id(self.story_id, "story_id")
        _check_id(self.bundle_id, "bundle_id")
        _check_id(self.task_id, "task_id")
        _check_asset_hash(self.asset_hash, allow_none=True)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Quad":
        """从 dict/JSON 载入四元组，缺任一字段（键不存在）即拒绝。

        与直接构造的区别：这里要求四个键**全部存在**（asset_hash 键必须
        存在，但其值允许为 None），从而拦截"请求里根本没带某个字段"的情况。
        """
        if not isinstance(data, Mapping):
            raise QuadValidationError(f"四元组来源必须是 mapping，实际: {type(data)!r}")
        missing = [f for f in _REQUIRED_FIELDS if f not in data]
        if missing:
            raise QuadValidationError(f"四元组缺少字段: {missing}")
        return cls(
            story_id=data["story_id"],
            bundle_id=data["bundle_id"],
            task_id=data["task_id"],
            asset_hash=data["asset_hash"],
        )

    # -- 校验入口 -----------------------------------------------------------

    def validate(self) -> "Quad":
        """上下文级校验（asset_hash 可空）。合法则返回自身，否则抛异常。

        供 Workorder 创建、检索、mock 生成等尚未绑定 asset_hash 的阶段使用。
        （构造时已校验，此处再次显式调用以表达调用点的意图并保持幂等。）
        """
        _check_id(self.story_id, "story_id")
        _check_id(self.bundle_id, "bundle_id")
        _check_id(self.task_id, "task_id")
        _check_asset_hash(self.asset_hash, allow_none=True)
        return self

    def require_bound(self) -> "Quad":
        """资产级校验：asset_hash 必须存在且格式合法。

        供 Backflow / 渲染注册 / 下载登记等"必须已绑定资产"的操作使用。
        """
        self.validate()
        _check_asset_hash(self.asset_hash, allow_none=False)
        return self

    @property
    def is_bound(self) -> bool:
        """是否已绑定 asset_hash（即资产已产出）。"""
        return self.asset_hash is not None

    # -- 派生 ---------------------------------------------------------------

    def with_asset_hash(self, asset_hash: str) -> "Quad":
        """返回绑定了 asset_hash 的新四元组（原对象不可变，不受影响）。"""
        return Quad(
            story_id=self.story_id,
            bundle_id=self.bundle_id,
            task_id=self.task_id,
            asset_hash=asset_hash,
        ).validate()

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 友好的 dict。"""
        return {
            "story_id": self.story_id,
            "bundle_id": self.bundle_id,
            "task_id": self.task_id,
            "asset_hash": self.asset_hash,
        }
