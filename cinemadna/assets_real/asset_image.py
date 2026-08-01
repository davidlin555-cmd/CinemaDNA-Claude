"""统一真实资产出图助手 (Phase 2) —— SceneDNA / PropDNA / IdentityDNA 共用

统一三大资产的"真实生成"这一步：给定图像后端、Bundle、相对路径、提示词，
出一张真实图并落盘，返回结构化结果。单张失败不崩溃（降级为无图 + 记原因），
由 PreRender Gate 的真实资产校验如实拦下。

同一函数被三大资产复用，保证"解析需求 → 真实生成 → 落盘 → 写入资产"这一步一致。
"""

from __future__ import annotations

from typing import Any


def _sniff_suffix(data: bytes) -> str | None:
    """按魔数判真实图像格式，返回正确扩展名（. 开头）；未知返回 None。"""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _correct_extension(dest, relpath: str) -> tuple[Any, str]:
    """出图后端按调用方给的后缀落盘，但真实字节可能是别的格式（FLUX 常返 JPEG）。

    按魔数把文件改成真实后缀，返回 (新 Path, 新 relpath)，让 Contract / Gate /
    WebUI 拿到的路径与真实字节一致（防"扩展名撒谎")。
    """
    try:
        head = dest.open("rb").read(16)
    except Exception:
        return dest, relpath
    real = _sniff_suffix(head)
    if real is None or dest.suffix.lower() == real:
        return dest, relpath
    new_dest = dest.with_suffix(real)
    try:
        dest.replace(new_dest)
    except Exception:
        return dest, relpath
    new_rel = relpath[: -len(dest.suffix)] + real if dest.suffix else relpath + real
    return new_dest, new_rel


def generate_real_image(
    image_backend: Any,
    bundle: Any,
    *,
    relpath: str,
    prompt: str,
    item_id: str,
    width: int = 768,
    height: int = 1024,
) -> dict[str, Any]:
    """出一张真实资产图。返回 {ok, relpath, abspath, meta|error}。

    - ok=True：图已落在 bundle 的 relpath，abspath 为绝对路径（供跨剧回流复用）
    - ok=False：出图失败（如内容过滤），error 为原因；调用方应降级为无图
    """
    if image_backend is None or bundle is None:
        return {"ok": False, "error": "无图像后端或 Bundle"}
    dest = bundle.path_for(relpath)
    try:
        img = image_backend.generate(
            prompt=prompt, dest=dest, item_id=item_id, width=width, height=height)
    except Exception as e:  # noqa: BLE001 —— 单张失败不中断流水线
        return {"ok": False, "error": str(e)[:200], "relpath": relpath}
    # 真实字节格式可能与调用方给的后缀不符（FLUX 常返 JPEG）→ 纠正后缀
    dest, relpath = _correct_extension(dest, relpath)
    return {
        "ok": True,
        "relpath": relpath,
        "abspath": str(dest.resolve()),
        "meta": img.to_dict(),
    }


__all__ = ["generate_real_image"]
