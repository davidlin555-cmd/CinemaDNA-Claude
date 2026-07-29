"""CinemaDNA / DramaOS-X Factory Core — 哈希与确定性伪随机 (Phase 1)

用途：
- asset_hash 生成：对结构化资产做规范化 JSON 序列化后取 sha256，
  输出形如 'sha256:<64位hex>'，与 quad._ASSET_HASH_RE 对齐。
- mock 打分：Phase 1 没有真实模型，但打分必须**可复现**（同样输入永远同样分数），
  否则测试会抖动、Gate 结论不可审计。因此统一用 sha256 派生 [0,1) 稳定值。

注意：stable_unit 只是"确定性伪随机"，不承担任何真实质量评估语义。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """规范化 JSON 序列化：键排序 + 无多余空白 + 保留中文。

    保证同一逻辑内容永远得到同一字节串，从而得到稳定 hash。
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(obj: Any) -> str:
    """返回规范化 JSON 的 sha256 十六进制摘要（不带前缀）。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def asset_hash_of(obj: Any) -> str:
    """返回可直接填入四元组的 asset_hash：'sha256:<64位hex>'。"""
    return f"sha256:{sha256_hex(obj)}"


def stable_unit(*seed_parts: Any) -> float:
    """由任意可序列化种子派生一个稳定的 [0, 1) 浮点数。

    同样的 seed_parts 永远返回同样的值，跨进程、跨平台一致。
    """
    digest = hashlib.sha256(canonical_json(list(seed_parts)).encode("utf-8")).digest()
    # 取前 8 字节作为无符号整数，再归一化到 [0,1)
    raw = int.from_bytes(digest[:8], "big")
    return raw / float(1 << 64)


def stable_score(low: float, high: float, *seed_parts: Any, ndigits: int = 4) -> float:
    """在 [low, high] 区间内派生一个稳定分数，用于 mock Gate 打分。"""
    if high < low:
        raise ValueError(f"stable_score 区间非法: low={low} high={high}")
    return round(low + (high - low) * stable_unit(*seed_parts), ndigits)
