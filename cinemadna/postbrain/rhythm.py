"""PostBrain 剪辑节奏 —— 不是机械等长拼接，而是按戏剧节拍编排剪辑速度。

商业短剧的剪辑有节奏：开场钩子不拖、冲突段向高潮**加速**（切得更快更碎）、
反应/打断镜**短促有力**、结尾反转/悬念**稍留一拍**给情绪落点。本模块在渲染前
按节拍类型与位置调制每镜时长（仍守 1.5–4s 边界），形成"加速—悬停"的剪辑曲线。

就地改 DRAFT 合约的 duration_sec（尚未签发，无 hash 问题）。语音回写会在此基础
上对有台词镜取 max(语音, 本节奏时长)，即节奏是地板、语音不被裁。
"""

from __future__ import annotations

from typing import Any

MIN_SHOT = 1.5
MAX_SHOT = 4.0

#: 节拍类型 → 剪辑速度系数（<1 更快/更碎，>1 稍留）
_BEAT_TEMPO = {
    "reaction": 0.75,        # 反应镜短促
    "interruption": 0.7,     # 打断要快、打断感
    "action": 0.9,
    "info": 0.95,
    "power_shift": 0.95,
    "obstruction": 0.9,
    "counterattack": 0.85,   # 反击有节奏地推进
    "setup": 0.95,
    "reversal": 1.15,        # 反转留一拍
    "cliffhanger": 1.2,      # 悬念落点稍长
}


def apply_editing_rhythm(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按节拍类型 + 全片位置调制每镜时长，形成剪辑节奏。就地修改并返回排序列表。"""
    cs = sorted(contracts, key=lambda c: c.get("order", 0))
    n = len(cs)
    for i, c in enumerate(cs):
        bt = (c.get("beat_type") or "").lower()
        d = float(c.get("duration_sec", 3.0))
        pos = i / max(1, n - 1)          # 0=开场 … 1=结尾
        tempo = _BEAT_TEMPO.get(bt, 1.0)
        # 向高潮加速：越靠后越紧（结尾反转/悬念的 tempo>1 会盖过这条，形成"加速后悬停"）
        accel = 1.0 - 0.15 * pos
        # 开场钩子不拖
        hook = 0.9 if i == 0 else 1.0
        nd = round(min(MAX_SHOT, max(MIN_SHOT, d * tempo * accel * hook)), 1)
        c["duration_sec"] = nd
        c.setdefault("edit_rhythm", {})
        c["edit_rhythm"] = {"tempo": tempo, "accel": round(accel, 3),
                            "final_sec": nd, "beat_type": bt}
    return cs


__all__ = ["apply_editing_rhythm", "MIN_SHOT", "MAX_SHOT"]
