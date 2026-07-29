"""Shot Contract —— 一镜头一合约 (Phase 3)

对应多智能体文档 §2.6 协作要点：

    **Shot Contract 是 Render 的唯一合法输入。没有合同的镜头禁止渲染。**

合约把一个镜头需要的一切**钉死**在一个结构里：
四元组、场景/人物/道具资产（连 asset_hash 和 Gate 结论一起）、机位、
情绪、表演节拍、台词、时长。Render 只认合约，不认剧本、不认口头约定。

## 三条铁规在这里落地

1. `require_render_ready()` 校验每一个引用资产都**已过 Gate 且带 asset_hash**
   —— 主规格铁规 4「未过 Production Gate 不得渲染」的执行点。
2. 合约签发时算 `contract_hash`；渲染前重算比对，**改过就拒渲**
   —— 防止有人签完合约再偷偷改参数绕过 Gate。
3. 状态机 DRAFT → PERFORMANCE_READY → RENDERED，跳步一律拒绝
   —— 没有表演节拍的镜头不许进渲染。
"""

from __future__ import annotations

import re
from typing import Any, Final

from asset_brain.common import schemas
from asset_brain.common.hashing import asset_hash_of, sha256_hex
from asset_brain.common.quad import Quad

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

SHOT_ESTABLISHING: Final = "ESTABLISHING"   # 建立镜
SHOT_MEDIUM: Final = "MEDIUM"               # 中景叙事
SHOT_CLOSEUP: Final = "CLOSEUP"             # 特写
SHOT_REACTION: Final = "REACTION"           # 反应镜
SHOT_INSERT: Final = "INSERT"               # 道具插入镜

SHOT_TYPES: Final[frozenset[str]] = frozenset(
    {SHOT_ESTABLISHING, SHOT_MEDIUM, SHOT_CLOSEUP, SHOT_REACTION, SHOT_INSERT}
)

# ---------------------------------------------------------------------------
# 口型策略（Mouth Policy）—— 短剧最大翻车点之一，写死为确定性规则
# ---------------------------------------------------------------------------
# 铁律：说话必须有声音+口型；不说话嘴必须闭合；无法 lip-sync 的说话镜头
# 必须改判为 反应/画外音/手机画面/背影。每个镜头必须声明一个策略。

MOUTH_SPEAKING_LIPSYNC: Final = "SPEAKING_LIPSYNC"  # 说话且做口型 → 必须有对应角色音轨
MOUTH_SILENT_CLOSED: Final = "SILENT_CLOSED"        # 不说话 → 嘴闭合，禁止无声嘴动
MOUTH_REACTION: Final = "REACTION"                  # 反应镜（听者，无自己台词）
MOUTH_OFFSCREEN_VO: Final = "OFFSCREEN_VO"          # 画外音/旁白
MOUTH_PHONE_SCREEN: Final = "PHONE_SCREEN"          # 手机/画面插入
MOUTH_BACK_SHOT: Final = "BACK_SHOT"                # 背影

MOUTH_POLICIES: Final[frozenset[str]] = frozenset(
    {MOUTH_SPEAKING_LIPSYNC, MOUTH_SILENT_CLOSED, MOUTH_REACTION,
     MOUTH_OFFSCREEN_VO, MOUTH_PHONE_SCREEN, MOUTH_BACK_SHOT}
)

#: 允许"有台词但不做正脸口型"的替代策略（声音在、口型不强求）
MOUTH_DIALOGUE_OK: Final[frozenset[str]] = frozenset(
    {MOUTH_SPEAKING_LIPSYNC, MOUTH_OFFSCREEN_VO, MOUTH_PHONE_SCREEN,
     MOUTH_BACK_SHOT, MOUTH_REACTION}
)

STATUS_DRAFT: Final = "DRAFT"
STATUS_PERFORMANCE_READY: Final = "PERFORMANCE_READY"
STATUS_RENDERED: Final = "RENDERED"
STATUS_FAILED: Final = "FAILED"

#: 单镜头时长上限（短剧节奏；超过基本是没切开的长镜）
MAX_SHOT_DURATION_SEC: Final = 12.0
MIN_SHOT_DURATION_SEC: Final = 1.0

_SHOT_ID_RE: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_HASH_RE: Final = re.compile(r"^sha256:[0-9a-fA-F]{64}$")

#: 计算 contract_hash 时排除的字段（它们本身就是 hash 之后才产生的）
_HASH_EXCLUDED: Final[frozenset[str]] = frozenset(
    {"contract_hash", "status", "render_result", "signed_at"}
)


class ShotContractError(ValueError):
    """合约结构非法。"""


class RenderNotAllowedError(RuntimeError):
    """合约未满足渲染前置条件（Gate / hash / 表演 / 篡改）。"""


# ---------------------------------------------------------------------------
# 构造
# ---------------------------------------------------------------------------


def new_shot_contract(
    *,
    shot_id: str,
    scene_id: str,
    episode_id: str,
    order: int,
    shot_type: str,
    quad: Quad,
    camera: dict[str, Any],
    duration_sec: float,
    emotion: dict[str, Any],
    scene_asset: dict[str, Any],
    character_assets: list[dict[str, Any]],
    prop_assets: list[dict[str, Any]],
    dialogue: list[dict[str, Any]] | None = None,
    difficulty: str = "normal",
    notes: str = "",
    mouth_policy: str | None = None,
) -> dict[str, Any]:
    """签发一张 DRAFT 合约（表演字段留空，由 PerformanceDNA 填）。"""
    if shot_type not in SHOT_TYPES:
        raise ShotContractError(
            f"未知镜头类型 {shot_type!r}，可选 {sorted(SHOT_TYPES)}"
        )
    mouth_policy = mouth_policy or _default_mouth_policy(shot_type, dialogue)
    if mouth_policy not in MOUTH_POLICIES:
        raise ShotContractError(
            f"未知口型策略 {mouth_policy!r}，可选 {sorted(MOUTH_POLICIES)}"
        )
    if not _SHOT_ID_RE.match(shot_id):
        raise ShotContractError(f"shot_id 含非法字符（会变成文件名）: {shot_id!r}")
    quad.validate()

    contract: dict[str, Any] = {
        "schema_version": schemas.SCHEMA_SHOT_CONTRACT,
        "shot_id": shot_id,
        "scene_id": scene_id,
        "episode_id": episode_id,
        "order": order,
        "shot_type": shot_type,
        "status": STATUS_DRAFT,
        "story_id": quad.story_id,
        "bundle_id": quad.bundle_id,
        "task_id": quad.task_id,
        "asset_hash": None,          # 渲染产物的 hash，渲染后才绑定
        "camera": dict(camera),
        "duration_sec": round(float(duration_sec), 2),
        "emotion": dict(emotion),
        "assets": {
            "scene": dict(scene_asset),
            "characters": [dict(c) for c in character_assets],
            "props": [dict(p) for p in prop_assets],
        },
        "performance": {},
        "dialogue": [dict(d) for d in dialogue or []],
        "mouth_policy": mouth_policy,
        "difficulty": difficulty,
        "notes": notes,
        "created_at": schemas.utc_now_iso(),
        "signed_at": None,
        "contract_hash": None,
        "render_result": None,
    }
    validate_contract(contract)
    return contract


def _default_mouth_policy(shot_type: str, dialogue: list[dict[str, Any]] | None) -> str:
    """按镜头类型 + 有无台词推导默认口型策略。

    有台词 → 默认做正脸口型（SPEAKING_LIPSYNC）；无台词看景别：
    插入镜 → 手机/画面；反应镜 → 反应；其余能看到脸 → 嘴闭合。
    """
    has_line = bool(dialogue)
    if shot_type == SHOT_INSERT:
        return MOUTH_PHONE_SCREEN
    if shot_type == SHOT_REACTION:
        return MOUTH_REACTION
    if has_line:
        return MOUTH_SPEAKING_LIPSYNC
    return MOUTH_SILENT_CLOSED


def check_mouth_policy(contract: dict[str, Any]) -> list[str]:
    """口型铁律的确定性校验，返回违规说明列表（空=通过）。

    现在（无 Audio/多模态）只能靠合约声明层判定，但已能堵住最典型的矛盾：
    "有台词却标嘴闭合"、"无台词却标做口型"。
    """
    issues: list[str] = []
    policy = contract.get("mouth_policy")
    if policy not in MOUTH_POLICIES:
        return [f"{contract.get('shot_id')}: 缺少合法 mouth_policy"]
    has_line = bool(contract.get("dialogue"))
    if has_line and policy not in MOUTH_DIALOGUE_OK:
        issues.append(
            f"{contract['shot_id']}: 有台词却标 {policy}（说话镜头必须做口型，"
            f"或改判为 反应/画外音/手机/背影）"
        )
    if has_line and policy == MOUTH_SILENT_CLOSED:
        issues.append(f"{contract['shot_id']}: 有台词却标嘴闭合，矛盾")
    if not has_line and policy == MOUTH_SPEAKING_LIPSYNC:
        issues.append(f"{contract['shot_id']}: 无台词却标做口型（会出现无声嘴动）")
    return issues


def compute_contract_hash(contract: dict[str, Any]) -> str:
    """对合约的实质内容取 sha256（排除 hash / 状态 / 渲染结果本身）。"""
    payload = {k: v for k, v in contract.items() if k not in _HASH_EXCLUDED}
    return f"sha256:{sha256_hex(payload)}"


def sign_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """表演字段填好后签发：置 PERFORMANCE_READY 并锁定 contract_hash。

    签发之后任何字段改动都会导致 hash 对不上，渲染阶段直接拒绝。
    """
    if contract["status"] != STATUS_DRAFT:
        raise ShotContractError(
            f"只有 DRAFT 合约可以签发，{contract['shot_id']} 当前 {contract['status']}"
        )
    if not (contract.get("performance") or {}).get("acting_beats"):
        raise ShotContractError(
            f"{contract['shot_id']} 没有表演节拍，PerformanceDNA 还没处理"
        )
    contract["status"] = STATUS_PERFORMANCE_READY
    contract["signed_at"] = schemas.utc_now_iso()
    contract["contract_hash"] = compute_contract_hash(contract)
    return contract


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """结构校验（不看 Gate，只看字段完整性）。"""
    schemas.require_schema_version(
        contract.get("schema_version"), schemas.SCHEMA_SHOT_CONTRACT
    )
    for f in ("shot_id", "scene_id", "episode_id", "story_id", "bundle_id", "task_id"):
        if not contract.get(f):
            raise ShotContractError(f"合约缺少字段 {f}")
    if contract["shot_type"] not in SHOT_TYPES:
        raise ShotContractError(f"未知镜头类型: {contract['shot_type']!r}")

    d = float(contract.get("duration_sec") or 0)
    if not MIN_SHOT_DURATION_SEC <= d <= MAX_SHOT_DURATION_SEC:
        raise ShotContractError(
            f"{contract['shot_id']} 时长 {d}s 超出单镜头范围 "
            f"[{MIN_SHOT_DURATION_SEC}, {MAX_SHOT_DURATION_SEC}]"
        )
    camera = contract.get("camera") or {}
    for f in ("shot_size", "movement", "intent"):
        if not camera.get(f):
            raise ShotContractError(f"{contract['shot_id']} 的 camera 缺少 {f}")

    assets = contract.get("assets") or {}
    if not (assets.get("scene") or {}).get("asset_id"):
        raise ShotContractError(f"{contract['shot_id']} 没有绑定场景资产")
    if not isinstance(assets.get("characters"), list):
        raise ShotContractError(f"{contract['shot_id']} 的 characters 必须是列表")
    return contract


def _require_gated_asset(ref: dict[str, Any], what: str, shot_id: str) -> None:
    """任一引用资产必须：有 asset_hash（格式合法）+ Gate 已通过。"""
    h = ref.get("asset_hash")
    if not isinstance(h, str) or not _HASH_RE.match(h):
        raise RenderNotAllowedError(
            f"{shot_id}: {what} 的 asset_hash 缺失或格式非法 -> {h!r}"
        )
    if not ref.get("gate_passed"):
        raise RenderNotAllowedError(
            f"{shot_id}: {what} 未通过 Production Gate，禁止进入渲染"
        )


def require_render_ready(contract: dict[str, Any]) -> dict[str, Any]:
    """渲染前置校验。任一条不满足都抛 RenderNotAllowedError。

    这是「未过 Gate 不得渲染」的最后一道闸，Render Brain 必须先过这一关。
    """
    validate_contract(contract)

    if contract["status"] != STATUS_PERFORMANCE_READY:
        raise RenderNotAllowedError(
            f"{contract['shot_id']}: 合约状态为 {contract['status']}，"
            f"只有 {STATUS_PERFORMANCE_READY} 才允许渲染"
        )

    # 防篡改：签发后被改过就拒渲
    expected = compute_contract_hash(contract)
    if contract.get("contract_hash") != expected:
        raise RenderNotAllowedError(
            f"{contract['shot_id']}: 合约在签发后被修改（hash 不匹配），拒绝渲染"
        )

    sid = contract["shot_id"]
    assets = contract["assets"]
    _require_gated_asset(assets["scene"], "场景资产", sid)
    if not assets["characters"]:
        raise RenderNotAllowedError(f"{sid}: 没有绑定任何人物资产")
    for c in assets["characters"]:
        _require_gated_asset(c, f"人物 {c.get('character_id')}", sid)
        if not c.get("master_pack_id"):
            raise RenderNotAllowedError(
                f"{sid}: 人物 {c.get('character_id')} 没有 Character Master Pack"
            )
    for p in assets["props"]:
        _require_gated_asset(p, f"道具 {p.get('prop_id')}", sid)
        if not p.get("state"):
            raise RenderNotAllowedError(
                f"{sid}: 道具 {p.get('prop_id')} 没有指定状态，跨镜头会穿帮"
            )

    if not (contract.get("performance") or {}).get("acting_beats"):
        raise RenderNotAllowedError(f"{sid}: 没有表演节拍")
    return contract


def bind_render_result(
    contract: dict[str, Any], rendered: dict[str, Any]
) -> dict[str, Any]:
    """渲染完成后回写结果并绑定产物 asset_hash（四元组自此完整）。"""
    contract["render_result"] = rendered
    contract["asset_hash"] = rendered["asset_hash"]
    contract["status"] = STATUS_RENDERED
    return contract


def contract_quad(contract: dict[str, Any]) -> Quad:
    """取回合约的四元组。"""
    return Quad(
        story_id=contract["story_id"],
        bundle_id=contract["bundle_id"],
        task_id=contract["task_id"],
        asset_hash=contract.get("asset_hash"),
    )


def payload_hash(payload: dict[str, Any]) -> str:
    """渲染载荷 / 产物的 asset_hash。"""
    return asset_hash_of(payload)
