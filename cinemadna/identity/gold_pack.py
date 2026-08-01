"""CharacterGoldPack（角色金图包）—— 跨镜身份稳定的行业硬路径。

行业成熟做法：角色先有**金图包**（多角度电影感剧照、同一身份）→ 每镜主角用金图做
Image-to-Video 起点 → 与金图比对，漂了重抽/换脸修，不硬过。把它写成系统硬规则：

  IdentityDNA → 金图包 → 导演 cast_lock 绑 gold_pack_id → Render 只允许金图锚帧 I2V

硬规则：
  1. 主角镜禁止纯 Text-to-Video
  2. 无 gold_pack 禁止渲染该角色
  3. 金图是**剧照感**（电影感环境肖像），禁止白底证件照
  4. 一次锁定，本场所有镜复用同一包；漂了自动重生成该镜

诚实：多角度"同脸"需参考图条件生成（FLUX Redux/IPAdapter）或 Kling Elements 主体绑定，
本机纯文生图做不到严格同脸多角度 → `angles_are_reference_consistent=False`，接上模型才为真。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 金图包标准角度（front/three_quarter 主用，其余可选）
GOLD_ANGLES = ("front", "three_quarter", "side",
               "expression_neutral", "expression_pressure")

#: 角度 → 英文构图（喂 FLUX；纯英文避免乱码文字）
_ANGLE_EN = {
    "front": "front view facing camera, medium shot chest-up",
    "three_quarter": "three-quarter view slightly turned",
    "side": "side profile view",
    "expression_neutral": "calm neutral expression, front view",
    "expression_pressure": "tense worried pressured expression, front view",
}
_ANTI_TEXT = ("absolutely no text, no letters, no chinese characters, no words, "
              "no watermark, no captions, no writing anywhere in the image")


@dataclass
class CharacterGoldPack:
    character_id: str
    gold_pack_id: str
    character_desc: str
    costume_lock: str
    angles: dict[str, str] = field(default_factory=dict)   # angle → 图片相对路径
    id_hash: str = ""
    gate_passed: bool = False
    angles_are_reference_consistent: bool = False          # 诚实：是否真·同脸多角度

    def angle_for(self, shot_type: str, *, pressure: bool = False) -> str | None:
        """按镜头选金图：压力戏用 pressure，特写用 front，其余用 three_quarter。"""
        order = (["expression_pressure", "front"] if pressure else
                 (["front", "three_quarter"] if shot_type in ("CLOSEUP", "INSERT")
                  else ["three_quarter", "front"]))
        for a in order + list(GOLD_ANGLES):
            if a in self.angles:
                return self.angles[a]
        return next(iter(self.angles.values()), None)

    def to_dict(self) -> dict[str, Any]:
        return {"character_id": self.character_id, "gold_pack_id": self.gold_pack_id,
                "character_desc": self.character_desc, "costume_lock": self.costume_lock,
                "angles": self.angles, "id_hash": self.id_hash,
                "gate_passed": self.gate_passed,
                "angles_are_reference_consistent": self.angles_are_reference_consistent}


def gold_prompt(desc: str, angle: str) -> str:
    return (f"cinematic film still, {desc}, {_ANGLE_EN.get(angle, 'front view')}, "
            f"consistent same person identity, dim Chinese county-town night "
            f"environment, moody cinematic lighting, shallow depth of field, "
            f"photorealistic, natural skin texture, vertical 9:16, film grain, "
            f"SFW, fully clothed, {_ANTI_TEXT}")


class GoldPackBuilder:
    """生成/锁定角色金图包。image_backend 可注入（测试用假后端，不联网）。"""

    def __init__(self, image_backend: Any, bundle=None,
                 angles: tuple[str, ...] = ("front", "three_quarter",
                                            "expression_pressure")) -> None:
        self.image_backend = image_backend
        self.bundle = bundle
        self.angles = angles

    def build(self, *, character_id: str, character_desc: str,
              costume_lock: str = "") -> CharacterGoldPack:
        pack_id = "gp_" + hashlib.sha1(
            f"{character_id}|{character_desc}".encode()).hexdigest()[:12]
        angles: dict[str, str] = {}
        for a in self.angles:
            rel = f"02_cast/gold/{character_id}/{a}.png"
            dest = self.bundle.path_for(rel) if self.bundle is not None else Path(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                self.image_backend.generate(
                    prompt=gold_prompt(character_desc, a), dest=dest,
                    item_id=f"gold_{character_id}_{a}", width=768, height=1360)
                if dest.is_file() and dest.stat().st_size > 500:
                    angles[a] = rel
            except Exception:  # noqa: BLE001 —— 单角度失败不毁整包
                continue
        id_hash = "sha256:" + hashlib.sha256(
            f"{pack_id}|{sorted(angles)}|{character_desc}".encode()).hexdigest()
        pack = CharacterGoldPack(
            character_id=character_id, gold_pack_id=pack_id,
            character_desc=character_desc, costume_lock=costume_lock,
            angles=angles, id_hash=id_hash,
            gate_passed=bool(angles),               # 有图即过（合成脸无红线；真人源另走 IdentityGate）
            angles_are_reference_consistent=False)  # 纯文生图非严格同脸；接 Redux/Elements 才为真
        if self.bundle is not None and angles:
            self.bundle.write_json(f"02_cast/gold/{character_id}/pack.json", pack.to_dict())
        return pack


class GoldPackError(RuntimeError):
    """无金图包却要渲染主角（硬规则 2）。"""


def require_gold_pack(pack: CharacterGoldPack | None, *, character_id: str) -> CharacterGoldPack:
    """硬规则：无 gold_pack 禁止渲染该角色。"""
    if pack is None or not pack.gate_passed or not pack.angles:
        raise GoldPackError(f"角色 {character_id} 无金图包，禁止渲染（主角镜禁纯文生视频）")
    return pack


__all__ = ["CharacterGoldPack", "GoldPackBuilder", "GoldPackError",
           "gold_prompt", "require_gold_pack", "GOLD_ANGLES"]
