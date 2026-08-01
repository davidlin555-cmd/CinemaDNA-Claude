"""Cross-Model Validation (Phase C) —— 子模型互检，防"素材拼接感"

对应 PDF §6。资产不能各自独立通过就直接拼接，必须互相校验能否共同组成一场戏。

PDF 要求的校验方向，逐条落成**确定性检查**（现有数据能判的）+ 判断层 PENDING：

| 方向 | 确定性检查（现在能做） | 判断层（PENDING） |
|------|------------------------|-------------------|
| Scene→Identity | 人物资产存在且过门；场景过门 | 光线是否适合角色脸、站位是否连贯 |
| Scene→Prop | 道具在本镜声明；道具过门；有状态 | 道具能否自然出现在桌面/手中 |
| Identity→Camera | 同一人物跨镜同一 Master Pack；景别能认出角色 | 服装/年龄连续 |
| Dialogue→Voice | 说话人是已声明人物；声音性别匹配人物性别 | — |
| Voice→MouthPolicy | 有声音才允许口型；无声禁止嘴动 | 可见说话是否需 lip-sync |
| Prop→Performance | 有道具时表演节拍应涉及操作 | 动作是否可拍 |

复用现有：连续性账本（跨镜同脸）、check_mouth_policy、gate_passed 标记。
"""

from __future__ import annotations

from typing import Any

from director.shot_contract import (
    MOUTH_DIALOGUE_OK,
    MOUTH_SILENT_CLOSED,
    MOUTH_SPEAKING_LIPSYNC,
    SHOT_CLOSEUP,
    SHOT_MEDIUM,
    SHOT_REACTION,
    check_mouth_policy,
)

# 能认出人脸的景别（特写/中景/反应）；全景/插入镜不要求认脸
_FACE_VISIBLE_SHOTS = frozenset({SHOT_CLOSEUP, SHOT_MEDIUM, SHOT_REACTION})


def _issue(direction: str, code: str, msg: str, shot_id: str) -> dict[str, Any]:
    return {"direction": direction, "code": code, "message": msg, "shot_id": shot_id}


def cross_model_validate(
    contracts: list[dict[str, Any]],
    *,
    characters_by_id: dict[str, dict[str, Any]] | None = None,
    asset_checker: "Any" = None,
    require_real_faces: bool = False,
    require_real_assets: bool = False,
) -> dict[str, Any]:
    """对一组合约做跨模型互检，返回校验报告。

    asset_checker(relpath) -> {"exists": bool, "bytes": int} | None
        给定就对**真实资产文件**做校验（人脸图是否真的在、够不够大）。
        不给则只做结构互检（老行为）。
    require_real_faces: 真实生产模式。为 True 时，能看到脸的镜头**必须**有
        真实且存在的人脸图，否则拦下——这就是"真实角色资产被真实校验"。
    """
    characters_by_id = characters_by_id or {}
    issues: list[dict[str, Any]] = []
    pending: list[str] = []

    # Identity→Camera（全片级）：同一人物跨镜必须同一 Master Pack
    packs: dict[str, set[str]] = {}
    for c in contracts:
        for ch in c["assets"]["characters"]:
            packs.setdefault(ch["character_id"], set()).add(
                str(ch.get("master_pack_id")))
    for cid, ids in packs.items():
        if len(ids) > 1:
            issues.append(_issue("Identity→Camera", "IDENTITY_DRIFT",
                f"人物 {cid} 跨镜头用了不同 Master Pack {sorted(ids)}", "*"))

    for c in contracts:
        sid = c["shot_id"]
        assets = c["assets"]
        scene = assets.get("scene") or {}
        chars = assets.get("characters") or []
        props = assets.get("props") or []

        # Scene→Identity
        if not scene.get("gate_passed"):
            issues.append(_issue("Scene→Identity", "SCENE_NOT_GATED",
                f"{sid} 场景未过门，人物无法置于其中", sid))
        face_visible = c["shot_type"] in _FACE_VISIBLE_SHOTS
        for ch in chars:
            cid = ch.get("character_id")
            if not ch.get("gate_passed"):
                issues.append(_issue("Scene→Identity", "IDENTITY_NOT_GATED",
                    f"{sid} 人物 {cid} 未过门", sid))
            if face_visible and not ch.get("master_pack_id"):
                issues.append(_issue("Identity→Camera", "NO_MASTER_PACK",
                    f"{sid} 景别需认脸但人物 {cid} 无 Master Pack", sid))

            # —— 真实角色资产校验 ——
            face_rel = ch.get("base_face_image")
            if face_rel and asset_checker is not None:
                info = asset_checker(face_rel)
                if not info or not info.get("exists"):
                    issues.append(_issue("Scene→Identity", "REAL_FACE_MISSING",
                        f"{sid} 人物 {cid} 声明了真实人脸图 {face_rel} 但文件不存在", sid))
                elif info.get("bytes", 0) < 1000:
                    issues.append(_issue("Scene→Identity", "REAL_FACE_INVALID",
                        f"{sid} 人物 {cid} 的人脸图过小（{info.get('bytes')} 字节），疑似损坏", sid))
            elif face_visible and (require_real_faces or require_real_assets) and not face_rel:
                # 真实生产模式：能看脸的镜头必须有真实人脸参考图
                issues.append(_issue("Scene→Identity", "NO_REAL_FACE",
                    f"{sid} 真实生产模式下人物 {cid} 缺真实人脸图（image2video 无参考）", sid))

        # Scene→Identity：真实场景图校验
        scene_img = scene.get("scene_image")
        if scene_img and asset_checker is not None:
            info = asset_checker(scene_img)
            if not info or not info.get("exists"):
                issues.append(_issue("Scene→Identity", "REAL_SCENE_MISSING",
                    f"{sid} 声明了真实场景图 {scene_img} 但文件不存在", sid))
        elif require_real_assets and not scene_img:
            issues.append(_issue("Scene→Identity", "NO_REAL_SCENE",
                f"{sid} 真实生产模式下缺真实场景图", sid))

        # Scene→Prop
        for p in props:
            if not p.get("gate_passed"):
                issues.append(_issue("Scene→Prop", "PROP_NOT_GATED",
                    f"{sid} 道具 {p.get('prop_id')} 未过门", sid))
            if not p.get("state"):
                issues.append(_issue("Scene→Prop", "PROP_NO_STATE",
                    f"{sid} 道具 {p.get('prop_id')} 无状态，跨镜会穿帮", sid))
            prop_img = p.get("prop_image")
            if prop_img and asset_checker is not None:
                info = asset_checker(prop_img)
                if not info or not info.get("exists"):
                    issues.append(_issue("Scene→Prop", "REAL_PROP_MISSING",
                        f"{sid} 道具 {p.get('prop_id')} 声明真实图 {prop_img} 但文件不存在", sid))
            elif require_real_assets and not prop_img:
                issues.append(_issue("Scene→Prop", "NO_REAL_PROP",
                    f"{sid} 真实生产模式下道具 {p.get('prop_id')} 缺真实图", sid))

        # Voice→MouthPolicy（复用口型铁律）
        for m in check_mouth_policy(c):
            issues.append(_issue("Voice→MouthPolicy", "MOUTH_POLICY", m, sid))

        # 真实音频一致性（接了 TTS 后 has_real_audio=True 时才校验）：
        # 有声才张嘴 / 无声必闭嘴——把口型从"声明"钉到"真实音频是否存在"
        if c.get("has_real_audio"):
            policy = c.get("mouth_policy")
            bindings = (c.get("voice_contract") or {}).get("bindings") or []
            has_audio = any(b.get("audio_file") for b in bindings)
            if policy == "SPEAKING_LIPSYNC" and (c.get("dialogue") or []) and not has_audio:
                issues.append(_issue("Voice→MouthPolicy", "VOICED_NO_AUDIO",
                    f"{sid} 张嘴说话镜头缺真实音频（会无声张嘴）", sid))
            if policy in ("SILENT_CLOSED", "REACTION") and has_audio:
                issues.append(_issue("Voice→MouthPolicy", "SILENT_HAS_AUDIO",
                    f"{sid} 无声镜头却挂了说话人音频（会闭嘴出声）", sid))

        # Dialogue→Voice：说话人必须是本镜在场人物，且性别与声音匹配
        in_frame = {ch["character_id"] for ch in chars}
        vc = c.get("voice_contract") or {}
        for b in vc.get("bindings") or []:
            spk = b["speaker"]
            if spk not in in_frame and c.get("mouth_policy") not in (
                    "OFFSCREEN_VO", "PHONE_SCREEN"):
                issues.append(_issue("Dialogue→Voice", "SPEAKER_NOT_IN_FRAME",
                    f"{sid} 说话人 {spk} 不在画面且非画外音/手机", sid))
            ch_gender = (characters_by_id.get(spk) or {}).get("gender", "")
            if ch_gender and b["voice_gender"] != "unspecified" \
                    and b["voice_gender"] != ch_gender:
                issues.append(_issue("Dialogue→Voice", "VOICE_GENDER_MISMATCH",
                    f"{sid} {spk} 声音性别 {b['voice_gender']} 与人物性别 {ch_gender} 不符", sid))

        # Prop→Performance：有道具时，表演节拍宜涉及操作（软提示）
        beats = (c.get("performance") or {}).get("acting_beats") or []
        if props and not beats:
            issues.append(_issue("Prop→Performance", "NO_PERFORMANCE_FOR_PROP",
                f"{sid} 有道具但无表演节拍，道具像摆设", sid))

    # 判断层（需多模态）：光线适配人脸、站位连贯、服装年龄连续、动作可拍
    pending = [
        "光线是否适合角色脸、人物站位是否连贯（Scene→Identity 多模态）",
        "道具能否自然出现在桌面/手中（Scene→Prop 多模态）",
        "服装/年龄跨镜是否连续（Identity→Camera 多模态）",
        "道具操作动作是否可拍（Prop→Performance 多模态）",
    ]

    return {
        "passed": not issues,
        "issues": issues,
        "issue_count": len(issues),
        "pending": pending,
        "checked_shots": len(contracts),
    }
