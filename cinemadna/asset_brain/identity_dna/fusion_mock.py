"""IdentityDNA · 多人脸融合 Mock (Phase 1)

Phase 1 **不实现**真实人脸融合算法。本模块只负责产出结构正确、
数值可复现的 identity_pack，让下游 Gate / Backflow 能被真实地检验。

设计要点：
1. 所有数值来自 hashing.stable_* 的确定性派生 —— 同一 workorder 永远得到
   同一结果，Gate 结论因此可复现、可审计。
2. 融合权重与"成品-源相似度"存在**耦合关系**：
       similarity_to_result = 0.05 + 0.70 * weight
   这条关系是刻意的——它让「某一真人源权重过高」自然表现为
   「成品与该真人相似度过高」，与 gate.detect_single_real_clone 的两条
   独立判据保持物理一致，而不是两个互不相干的随机数。
3. 默认路径永远产出合规的多源融合；违规场景必须由调用方**显式注入**
   （generation_plan["fusion_sources"]），Phase 1 用于测试红线。
"""

from __future__ import annotations

from typing import Any, Final

from ..common.hashing import asset_hash_of, stable_score

#: 权重 → 相似度的耦合系数（见模块文档第 2 点）
_SIM_BASE: Final = 0.05
_SIM_SLOPE: Final = 0.70

#: 自动生成融合源时的权重抖动幅度（±5%），避免完全均分显得不真实，
#: 同时保证 3 源时最大权重仍远低于 MAX_SINGLE_SOURCE_WEIGHT。
_WEIGHT_JITTER: Final = 0.05


def similarity_from_weight(weight: float) -> float:
    """由融合权重推导成品脸与该源的相似度。"""
    return round(_SIM_BASE + _SIM_SLOPE * float(weight), 4)


def build_fusion_sources(seed: str, count: int) -> list[dict[str, Any]]:
    """确定性生成 count 张授权源人脸及其融合权重。

    权重先按 1/count 均分再加小幅抖动，最后归一化到和为 1。
    """
    if count < 1:
        return []

    raw = [
        (1.0 / count) * (1.0 + stable_score(-_WEIGHT_JITTER, _WEIGHT_JITTER, seed, "w", i))
        for i in range(count)
    ]
    total = sum(raw)
    sources: list[dict[str, Any]] = []
    for i, r in enumerate(raw):
        weight = round(r / total, 4)
        sources.append(
            {
                "source_id": f"srcface_{seed}_{i:02d}",
                # Phase 1 默认全部来自授权人脸库；真实接入时改为实际授权记录
                "license": "authorized",
                "is_real_person": True,
                "weight": weight,
                "similarity_to_result": similarity_from_weight(weight),
                "public_figure_similarity": stable_score(0.02, 0.18, seed, "pf", i),
            }
        )
    return sources


def mock_multi_face_fusion(
    *,
    workorder_id: str,
    requirement: dict[str, Any],
    generation_plan: dict[str, Any],
) -> dict[str, Any]:
    """Phase 1 mock 多人脸融合，产出 identity_pack。

    若 generation_plan 里显式给了 fusion_sources，则原样使用（用于模拟
    违规场景与后续接真实融合器）；否则按 fusion_source_count 自动生成。
    """
    seed = workorder_id
    injected = generation_plan.get("fusion_sources")
    if injected:
        sources = [dict(s) for s in injected]
    else:
        count = int(generation_plan.get("fusion_source_count", 3))
        sources = build_fusion_sources(seed, count)

    base_face_asset_id = f"face_{workorder_id}_base"

    pack: dict[str, Any] = {
        "workorder_id": workorder_id,
        "character_id": requirement.get("character_id"),
        "name": requirement.get("name"),
        "base_face_asset_id": base_face_asset_id,
        "fusion_sources": sources,
        "fusion_source_count": len(sources),
        # 独立的公众人物比对通道（真实实现应接人脸检索库）
        "public_figure_matches": generation_plan.get("public_figure_matches", []),
        "naturalness": stable_score(0.80, 0.94, seed, "naturalness"),
        "ethnicity_match": stable_score(0.82, 0.95, seed, "ethnicity"),
        "family_consistency": stable_score(0.80, 0.94, seed, "family"),
        "age_line": {},
        "family_pack": {},
        "expression_baseline": ["neutral", "sorrow", "restrained_anger"],
        "mock_mode": True,
    }
    pack["asset_hash"] = asset_hash_of(pack)
    return pack


def face_prompt(requirement: dict[str, Any]) -> str:
    """由角色需求拼出文生图提示词（合成新脸，非真人）。

    **电影感环境肖像**（不是白底证件照）：基础人脸定身份（族裔/性别/年龄）+ 角色
    服装/职业 + 剧情环境 + 电影布光，给 image2video 一个"有场景的底"，避免"证件照
    动嘴"的头像动画感。仍保持中性表情（情绪来自 PerformanceDNA）+ 竖屏构图。
    职业/服装/环境可由 requirement 传入（wardrobe/setting），否则走通用电影底。
    """
    age = requirement.get("age_range", "30")
    gender = {"female": "woman", "male": "man"}.get(
        requirement.get("gender", ""), "person")
    ethnicity = "East Asian Chinese"
    wardrobe = (requirement.get("wardrobe")
                or requirement.get("occupation")
                or "realistic everyday clothing")
    setting = (requirement.get("setting")
               or "grounded in a realistic dim indoor environment at night")
    return (
        f"cinematic medium portrait film still of a {ethnicity} {gender}, "
        f"{age} years old, {wardrobe}, {setting}, moody cinematic lighting, "
        f"shallow depth of field, natural neutral expression, vertical 9:16 framing, "
        f"photorealistic, natural skin texture, sharp focus on face, high detail, "
        f"SFW, fully clothed, "
        f"absolutely no text, no letters, no chinese characters, no words, "
        f"no watermark, no captions, no writing anywhere in the image"
    )


def synthetic_face_source(seed: str, image_relpath: str) -> list[dict[str, Any]]:
    """真实文生图产出的是**纯合成新脸**：单一合成来源、非真人。

    与 mock 的 3 源结构不同——它不是多张真人融合，而是模型直接生成的合成脸，
    因此无真人克隆风险（gate 已放行纯合成场景）。
    """
    return [{
        "source_id": f"synthface_{seed}",
        "license": "synthetic",
        "is_real_person": False,        # 关键：合成脸，非真人
        "weight": 1.0,
        "similarity_to_result": 0.0,    # 无真人可比
        "public_figure_similarity": stable_score(0.01, 0.08, seed, "pf"),
        "image_relpath": image_relpath,
    }]


def mock_age_line(identity_pack: dict[str, Any], ages: list[int]) -> dict[str, str]:
    """生成年龄线版本资产 ID 映射：{"25": "asset_id", ...}。"""
    base = identity_pack.get("base_face_asset_id", "face_unknown")
    return {str(age): f"{base}_age{int(age)}" for age in ages}


def mock_family_pack(
    identity_pack: dict[str, Any], relations: list[str]
) -> dict[str, str]:
    """生成家族脸谱资产 ID 映射：{"mother": "asset_id", ...}。"""
    base = identity_pack.get("base_face_asset_id", "face_unknown")
    return {str(rel): f"{base}_rel_{rel}" for rel in relations}


# ---------------------------------------------------------------------------
# 违规场景模拟器（仅供测试 / 演示 Gate 红线，绝不在生产路径使用）
# ---------------------------------------------------------------------------


def simulate_single_real_clone_sources(seed: str = "violation") -> list[dict[str, Any]]:
    """模拟「1 张真人 + 2 张陪衬」的伪多源克隆。

    这是最需要防的绕过手法：形式上有 3 个源，实质是单一真人克隆。
    """
    dominant_weight = 0.90
    return [
        {
            "source_id": f"srcface_{seed}_real",
            "license": "authorized",
            "is_real_person": True,
            "weight": dominant_weight,
            "similarity_to_result": similarity_from_weight(dominant_weight),
            "public_figure_similarity": 0.10,
        },
        {
            "source_id": f"srcface_{seed}_filler_a",
            "license": "authorized",
            "is_real_person": True,
            "weight": 0.05,
            "similarity_to_result": similarity_from_weight(0.05),
            "public_figure_similarity": 0.04,
        },
        {
            "source_id": f"srcface_{seed}_filler_b",
            "license": "authorized",
            "is_real_person": True,
            "weight": 0.05,
            "similarity_to_result": similarity_from_weight(0.05),
            "public_figure_similarity": 0.04,
        },
    ]
