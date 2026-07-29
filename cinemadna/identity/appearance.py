"""角色外观锁 —— 根治跨镜服装/发型漂移。

背景(实测):PuLID 只锁**脸**,服装/发型全由文本 prompt 控制。旧版 char_desc 用模糊的
`"story-appropriate everyday clothing"` → FLUX 每镜自由发挥 → 女主外套在墨绿/卡其间漂移。

本模块给每角色产出一套**具体、固定**的外观串(发型 + 具体服装),同一角色全片共用同一串:
同源流向锚脸铸造 / 起帧 / 尾帧三处 → 服装稳定一致。

优先级:
1. 剧本/角色定义里显式 `appearance` / `wardrobe` 字段(LLM 作者化可指定) → 直接用。
2. 否则按 role + gender **确定性派生**(种子=character_id → 同角色每次同一套,不随机漂)。

只描述**外观**(脸型由 PuLID 锚脸锁,这里锁发型+服装),不含构图/景别/动作(那些按镜变)。
"""
from __future__ import annotations

import hashlib
from typing import Any

# 角色职能 → 英文(去中文名,避免 FLUX 渲乱码);与旧 _desc_en 对齐并扩充。
_ROLE_EN = {
    "护士": "a nurse", "医生": "a doctor", "讨债": "a debt collector",
    "催债": "a debt collector", "高利贷": "a loan shark", "老板": "a boss",
    "经理": "a manager", "警": "a police officer", "律师": "a lawyer",
    "母": "a middle-aged mother", "父": "a middle-aged father",
    "主角": "an ordinary working person", "女主": "an ordinary working woman",
    "男主": "an ordinary working man",
}

# 确定性服装表(中国都市夜戏基调:写实、具体、可复现)。种子=character_id → 稳定不漂。
_WARDROBE_F = [
    "wearing a dark olive-green zip-up jacket over a grey high-neck sweater and black trousers",
    "wearing a fitted charcoal wool coat over a white blouse and dark jeans",
    "wearing a worn khaki trench coat over a black knit top and dark trousers",
    "wearing a navy padded jacket over a beige turtleneck and black pants",
]
_WARDROBE_M = [
    "wearing a black stand-collar jacket over a dark shirt and black trousers",
    "wearing a dark grey bomber jacket over a black t-shirt and dark jeans",
    "wearing a black leather jacket over a grey henley and black pants",
    "wearing a charcoal overcoat over a dark sweater and dark trousers",
]
_HAIR_F = [
    "long black hair tied in a low ponytail",
    "shoulder-length straight black hair",
    "black hair pulled back in a neat bun",
]
_HAIR_M = [
    "short black hair, clean-cut",
    "short cropped dark hair",
    "short dark hair swept back",
]


def _seed(character_id: str) -> int:
    return int(hashlib.sha1((character_id or "x").encode("utf-8")).hexdigest()[:6], 16)


def _pick(options: list[str], seed: int) -> str:
    return options[seed % len(options)]


def character_appearance(character: dict[str, Any]) -> str:
    """产出一角色的**固定**外观串(发型+具体服装),全片复用 → 服装一致。"""
    cid = character.get("character_id", "")
    g = character.get("gender", "")
    gender = "woman" if g == "female" else "man" if g == "male" else "person"
    role = f"{character.get('role', '') or ''}{character.get('role_type', '') or ''}" \
           f"{character.get('name', '') or ''}"
    role_en = next((v for k, v in _ROLE_EN.items() if k in role),
                   "an ordinary working woman" if g == "female"
                   else "an ordinary working man" if g == "male"
                   else "an ordinary person")
    seed = _seed(cid)
    female = g == "female"
    hair = _pick(_HAIR_F if female else _HAIR_M, seed)
    # 显式指定优先(LLM 作者化 appearance/wardrobe);否则确定性派生
    explicit = str(character.get("appearance") or character.get("wardrobe") or "").strip()
    wardrobe = explicit or _pick(_WARDROBE_F if female else _WARDROBE_M, seed)
    return (f"a realistic East Asian Chinese {gender}, {role_en}, late 20s to 30s, "
            f"{hair}, {wardrobe}, natural expression")


#: 兜底外观(某角色不在表里时用;仍是**具体**服装,不留模糊口子)
DEFAULT_APPEARANCE = (
    "a realistic East Asian Chinese person, late 20s to 30s, short dark hair, "
    "wearing a dark grey jacket over a plain top and dark trousers, natural expression")


__all__ = ["character_appearance", "DEFAULT_APPEARANCE", "_ROLE_EN"]
