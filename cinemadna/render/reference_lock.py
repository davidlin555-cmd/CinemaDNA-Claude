"""② I2V Reference Lock —— 主角镜禁纯 T2V，参考图强制，身份可验证。

复盘：跨镜变脸/身份漂移的根因之一是"核心人物用纯文生视频(T2V)"——没有参考锚，
模型每镜自由发挥脸。硬规则：**任何有在场核心角色的镜头，必须用金图/参考图做
image2video；缺参考 → 拒渲（禁纯 T2V）**。并给每镜盖一枚可验证的身份指纹
（用了哪张参考 + 种子），供跨镜同脸一致性核验（③熔断 / ⑤ InsightFace 真判）。

只做两件能改变/熔断产线的事：① 改生成输入（强制参考、拒 T2V）；② 产出可验证指纹。
不引入讨论型 Agent。
"""

from __future__ import annotations

from typing import Any

from .backend import FatalRenderError, RenderRequest


class ReferenceLockError(FatalRenderError):
    """主角镜缺参考图（纯 T2V）→ 禁止渲染。"""


def has_core_character(request: RenderRequest) -> bool:
    """本镜是否有在场核心角色（需锁脸）。"""
    ci = (request.reference_assets or {}).get("character_images") or {}
    return bool(ci)


def is_prop_insert(request: RenderRequest) -> bool:
    """纯道具插入镜（无人脸）——豁免参考锁。"""
    cam = request.camera or {}
    exec_facts = request.reference_assets.get("execution") if request.reference_assets else None
    if isinstance(exec_facts, dict) and "is_prop_insert" in exec_facts:
        return bool(exec_facts["is_prop_insert"])
    return (cam.get("shot_type") in ("INSERT", "插入")) and not has_core_character(request)


def enforce_reference_lock(request: RenderRequest, reference_b64: str | None) -> str:
    """硬门：有核心角色的镜必须有参考图；缺 → ReferenceLockError（禁纯 T2V）。

    返回校验过的参考 b64（供提交）。道具插入镜/无角色镜不强制。
    """
    if has_core_character(request) and not is_prop_insert(request):
        if not reference_b64:
            raise ReferenceLockError(
                f"{request.shot_id}: 主角镜禁止纯 T2V，必须有金图/参考图做 image2video"
                f"（缺参考锚 → 跨镜必变脸）")
    return reference_b64 or ""


def identity_fingerprint(request: RenderRequest, *, reference_kind: str,
                         ref_key: str) -> dict[str, Any]:
    """可验证身份指纹：这一镜用了哪张参考(金图/首帧) + 种子 + 在场角色。

    下游 IdentityConsistency 据此核验"同角色跨镜是否同一张脸锚"。
    """
    cids = list((request.reference_assets or {}).get("character_images") or {})
    return {"shot_id": request.shot_id, "reference_kind": reference_kind,
            "ref_key": ref_key, "seed": request.seed, "characters": cids}


def verify_reference_consistency(fingerprints: list[dict[str, Any]]) -> dict[str, Any]:
    """结构级同脸核验：同一角色（作主锚时）跨镜是否用同一参考锚（seed）。

    这是"可验证"的第一层——身份锚是否一致、可审计；**像素级真判脸见 ⑤ InsightFace**。
    返回 {consistent, anomalies, characters_checked}；anomalies=同角色用了多个 seed。
    """
    by_char: dict[str, set] = {}
    for fp in fingerprints or []:
        chars = fp.get("characters") or []
        if not chars:
            continue
        by_char.setdefault(chars[0], set()).add(fp.get("seed"))
    anomalies = [{"character": c, "seeds": sorted(s)}
                 for c, s in by_char.items() if len(s) > 1]
    return {"consistent": not anomalies, "anomalies": anomalies,
            "characters_checked": len(by_char), "verifiable": True,
            "level": "structural_reference_anchor",
            "note": "结构级(参考锚一致)；像素级真判脸待 InsightFace(⑤)"}


__all__ = ["ReferenceLockError", "has_core_character", "is_prop_insert",
           "enforce_reference_lock", "identity_fingerprint",
           "verify_reference_consistency"]
