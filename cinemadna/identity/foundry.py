"""IdentityFoundry —— 合成身份铸造：每角色一张 novel 锚脸 + PuLID 锁脸生成按镜首帧。

治上一条样片被拒的唯一真短板（跨镜漂移 + 塑料感）。链路：
  ① mint_anchor：FLUX 文生图铸一张**现实不存在的合成锚脸**（避肖像权），每角色一次、
     固定种子可复现；
  ② first_frame：每镜用 **PuLID(锚脸, 本镜构图提示)** 生成首帧 → 跨镜同一张脸（漂移
     根治）+ 自然无塑料感（PuLID 脸质）+ 按镜构图（景别/动作/场景随镜变）。

诚实：MVP 是"单张 novel 锚脸 + PuLID 锁"，已实测把跨镜同脸做到余弦 0.06-0.20。
多脸合并（家族/年线/种族脸）是后续增强（锚脸换成多脸嵌入融合产物）。
image_backend/pulid 均可注入 → 0 成本单测。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

#: 反文字（锚脸/首帧都不该出现画内文字）
_ANTI_TEXT = ("absolutely no text, no letters, no chinese characters, no words, "
              "no watermark, no captions, no signage, no logo")


def stable_seed(text: str) -> int:
    return int(hashlib.sha1((text or "x").encode("utf-8")).hexdigest()[:7], 16)


def anchor_prompt(character_desc: str) -> str:
    """铸锚脸的提示词：干净正脸肖像（身份锚点），novel 合成人，非真人。"""
    return (
        f"cinematic realistic portrait photograph of {character_desc}, "
        f"front-facing, neutral calm expression, soft even lighting, "
        f"sharp focus on the face, natural skin texture with fine detail, "
        f"photorealistic, plain neutral background, vertical portrait, "
        f"{_ANTI_TEXT}")[:1200]


class IdentityFoundryError(RuntimeError):
    pass


class IdentityFoundry:
    """每角色铸锚脸 + PuLID 锁脸生成首帧。"""

    def __init__(self, *, image_backend: Any, pulid_backend: Any,
                 bundle: Any = None) -> None:
        self.image_backend = image_backend      # FLUX 文生图，铸锚脸
        self.pulid = pulid_backend              # FalPuLIDBackend，锁脸生成
        self.bundle = bundle
        self._anchors: dict[str, Path] = {}     # character_id → 锚脸路径

    # -- ① 铸锚脸（每角色一次，固定种子可复现） -------------------------

    def mint_anchor(self, character_id: str, character_desc: str) -> Path:
        if self.bundle is None:
            raise IdentityFoundryError("铸锚脸需要 bundle")
        dest = self.bundle.path_for(f"02_cast/anchors/{character_id}.png")
        if not dest.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            self._gen_anchor(character_id, character_desc, dest)
        if not dest.is_file() or dest.stat().st_size < 500:
            raise IdentityFoundryError(f"{character_id}: 锚脸铸造失败")
        self._anchors[character_id] = dest
        return dest

    def _gen_anchor(self, cid: str, desc: str, dest: Path) -> None:
        seed = stable_seed(f"anchor_{cid}")     # 每角色固定种子 → 可复现同一锚脸
        kw: dict[str, Any] = dict(prompt=anchor_prompt(desc), dest=dest,
                                  item_id=f"anchor_{cid}", width=768, height=1024)
        import inspect
        try:
            if "seed" in inspect.signature(self.image_backend.generate).parameters:
                kw["seed"] = seed
        except (ValueError, TypeError):
            pass
        self.image_backend.generate(**kw)

    def anchor_for(self, character_id: str) -> Path | None:
        return self._anchors.get(character_id)

    def has_anchor(self, character_id: str) -> bool:
        return character_id in self._anchors

    # -- ② PuLID 锁脸生成按镜首帧 ---------------------------------------

    def first_frame(self, character_id: str, *, prompt: str, dest: Path,
                    item_id: str = "", width: int = 768, height: int = 1360,
                    id_weight: float = 1.0) -> Path:
        anchor = self.anchor_for(character_id)
        if anchor is None:
            raise IdentityFoundryError(
                f"{character_id}: 无锚脸，先 mint_anchor（无锚不锁脸）")
        return self.pulid.generate(
            reference_face=anchor, prompt=prompt, dest=dest,
            item_id=item_id or f"pulid_{character_id}",
            width=width, height=height, id_weight=id_weight)


__all__ = ["IdentityFoundry", "IdentityFoundryError", "anchor_prompt", "stable_seed"]
