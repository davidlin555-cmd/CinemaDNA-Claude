"""DirectorDNA · Continuity Ledger + Continuity Director (Phase 3)

对应多智能体文档 §2.6 的两个 Agent：
- **Continuity Ledger Agent**：把所有合约里的"谁用了哪个资产、道具处于什么状态"
  记成一本账（长剧一致性靠账本，不靠记忆）
- **Continuity Director**：拿账本查冲突，发现问题立刻打回

Phase 3 的四类检查都是**结构性**的（不是打分）：

1. 同一人物在不同镜头用了不同 Character Master Pack → 角色会漂移
2. 同一场景在不同镜头用了不同场景资产 → 空间会穿帮
3. 道具状态沿时间轴倒退（已经攥皱又变回完整）→ 穿帮
4. 镜头里的道具状态与剧本写的对不上 → 剧本与镜头脱节

这些是短剧 AI 最常见的翻车点，能用结构判定的就绝不留给打分。
"""

from __future__ import annotations

from typing import Any

from asset_brain.common import schemas


def build_ledger(
    contracts: list[dict[str, Any]], *, story_id: str, bundle_id: str
) -> dict[str, Any]:
    """把合约集合汇成一致性账本。"""
    prop_states: dict[str, list[dict[str, Any]]] = {}
    character_packs: dict[str, dict[str, Any]] = {}
    scene_assets: dict[str, dict[str, Any]] = {}

    for c in sorted(contracts, key=lambda x: x["order"]):
        sid = c["shot_id"]
        scene = c["assets"]["scene"]
        scene_assets.setdefault(c["scene_id"], {"asset_ids": [], "shots": []})
        scene_assets[c["scene_id"]]["asset_ids"].append(scene.get("asset_id"))
        scene_assets[c["scene_id"]]["shots"].append(sid)

        for ch in c["assets"]["characters"]:
            rec = character_packs.setdefault(
                ch["character_id"], {"master_pack_ids": [], "shots": []}
            )
            rec["master_pack_ids"].append(ch.get("master_pack_id"))
            rec["shots"].append(sid)

        for p in c["assets"]["props"]:
            prop_states.setdefault(p["prop_id"], []).append(
                {
                    "shot_id": sid,
                    "scene_id": c["scene_id"],
                    "order": c["order"],
                    "state": p.get("state"),
                    "asset_id": p.get("asset_id"),
                }
            )

    return {
        "schema_version": schemas.SCHEMA_CONTINUITY_LEDGER,
        "story_id": story_id,
        "bundle_id": bundle_id,
        "shot_count": len(contracts),
        "prop_states": prop_states,
        "character_packs": character_packs,
        "scene_assets": scene_assets,
        "updated_at": schemas.utc_now_iso(),
    }


def check_continuity(
    ledger: dict[str, Any],
    *,
    prop_state_order: dict[str, list[str]] | None = None,
    scene_prop_states: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Continuity Director：查四类冲突，返回检查报告。

    - `prop_state_order`：剧本声明的道具状态先后顺序（states_needed）
    - `scene_prop_states`：剧本每场戏里道具应处的状态
    """
    prop_state_order = prop_state_order or {}
    scene_prop_states = scene_prop_states or {}

    identity_issues: list[str] = []
    scene_issues: list[str] = []
    prop_issues: list[str] = []

    # 1) 人物身份漂移
    for cid, rec in ledger["character_packs"].items():
        packs = {p for p in rec["master_pack_ids"] if p}
        if len(packs) > 1:
            identity_issues.append(
                f"人物 {cid} 在镜头 {rec['shots']} 中使用了不同的 Master Pack {sorted(packs)}，"
                f"角色会漂移"
            )
        if not packs:
            identity_issues.append(f"人物 {cid} 的镜头没有绑定任何 Master Pack")

    # 2) 同场景资产不一致
    for scene_id, rec in ledger["scene_assets"].items():
        ids = {a for a in rec["asset_ids"] if a}
        if len(ids) > 1:
            scene_issues.append(
                f"场景 {scene_id} 在镜头 {rec['shots']} 中使用了不同的场景资产 {sorted(ids)}"
            )

    # 3) 道具状态倒退 / 4) 与剧本不符
    for pid, entries in ledger["prop_states"].items():
        order = prop_state_order.get(pid) or []
        index_of = {st: i for i, st in enumerate(order)}
        last_idx = -1
        last_shot = None
        for e in sorted(entries, key=lambda x: x["order"]):
            state = e["state"]
            expected = (scene_prop_states.get(e["scene_id"]) or {}).get(pid)
            if expected is not None and state != expected:
                prop_issues.append(
                    f"{e['shot_id']} 中道具 {pid} 状态为「{state}」，"
                    f"但剧本 {e['scene_id']} 写的是「{expected}」"
                )
            if state in index_of:
                idx = index_of[state]
                if idx < last_idx:
                    prop_issues.append(
                        f"道具 {pid} 状态倒退：{last_shot} 已是「{order[last_idx]}」，"
                        f"{e['shot_id']} 又变回「{state}」"
                    )
                last_idx = max(last_idx, idx)
                last_shot = e["shot_id"]
            elif order:
                prop_issues.append(
                    f"{e['shot_id']} 中道具 {pid} 的状态「{state}」未在剧本登记"
                )

    issues = identity_issues + scene_issues + prop_issues
    return {
        "schema_version": schemas.SCHEMA_CONTINUITY_CHECK,
        "story_id": ledger["story_id"],
        "passed": not issues,
        "issues": issues,
        "identity_issues": identity_issues,
        "scene_issues": scene_issues,
        "prop_issues": prop_issues,
        "checked_at": schemas.utc_now_iso(),
    }
