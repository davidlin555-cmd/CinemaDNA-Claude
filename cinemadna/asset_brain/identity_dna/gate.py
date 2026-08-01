"""IdentityDNA · Naturalness + Likeness Risk Gate (Phase 1) —— 全系统最严格的闸门

对应接口文档 §3.3 与主规格 §八 铁律 6：
    「IdentityDNA 必须走多人脸融合，禁止单一真人高相似克隆。」

本模块是该红线的**唯一执行点**，也是 Phase 1 必须真实实现（而非 mock）的逻辑。
与 SceneDNA / PropDNA 的 Gate 不同，这里区分两类判定：

1. **hard_blocks（硬性拦截）**——一票否决，不参与加权、不看总分、
   **人工审核也不得推翻**（由 facade.approve_gate 强制）。
2. soft issues（软性问题）——记录进 issues，可能触发人工复核，但不构成红线。

硬性拦截项（任意一条命中即 passed=False）：

| 代码                          | 含义                                             |
|-------------------------------|--------------------------------------------------|
| fusion_policy_disabled        | 需求里把多人脸融合/禁克隆开关关掉了（策略不可关） |
| insufficient_fusion_sources   | 去重后的融合源人脸 < 3 张                        |
| single_real_person_detected   | 某一真人源占比过高或成品与其相似度过高（=克隆）   |
| high_public_figure_similarity | 与公众人物相似度超阈值                            |
| likeness_risk_exceeded        | 综合肖像权风险超阈值                              |
| unlicensed_source_face        | 存在未授权来源人脸                                |

"占比过高"与"相似度过高"分开判定是刻意的：只看数量会被
「1 张真人 90% + 2 张陪衬 5%」绕过；只看权重会被伪造权重绕过。
两条同时存在才能真正堵住单一真人克隆路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from ..common import schemas
from ..common.gate import GateReport

# ---------------------------------------------------------------------------
# 硬性阈值（Phase 1 锁定；调整必须同步更新主规格与测试）
# ---------------------------------------------------------------------------

#: 融合源人脸最少张数（去重后）
MIN_FUSION_SOURCES: Final = 3
#: 单一源人脸在融合结果中的最大权重占比
MAX_SINGLE_SOURCE_WEIGHT: Final = 0.60
#: 成品脸与任一单一真人源的最大允许相似度
MAX_SINGLE_SOURCE_SIMILARITY: Final = 0.60
#: 与任一公众人物的最大允许相似度
MAX_PUBLIC_FIGURE_SIMILARITY: Final = 0.50
#: 综合肖像权风险上限（越低越好）
MAX_LIKENESS_RISK: Final = 0.35
#: 允许的源人脸授权类型
ALLOWED_SOURCE_LICENSES: Final[frozenset[str]] = frozenset(
    {"authorized", "synthetic", "public_domain"}
)

# 软性质量阈值
MIN_NATURALNESS: Final = 0.70
MIN_ETHNICITY_MATCH: Final = 0.60
MIN_FAMILY_CONSISTENCY: Final = 0.60
MIN_OVERALL: Final = 0.70

# ---------------------------------------------------------------------------
# 硬拦截代码
# ---------------------------------------------------------------------------

BLOCK_FUSION_POLICY_DISABLED: Final = "fusion_policy_disabled"
BLOCK_INSUFFICIENT_FUSION_SOURCES: Final = "insufficient_fusion_sources"
BLOCK_SINGLE_REAL_PERSON: Final = "single_real_person_detected"
BLOCK_PUBLIC_FIGURE: Final = "high_public_figure_similarity"
BLOCK_LIKENESS_RISK: Final = "likeness_risk_exceeded"
BLOCK_UNLICENSED_SOURCE: Final = "unlicensed_source_face"

ALL_HARD_BLOCKS: Final[frozenset[str]] = frozenset(
    {
        BLOCK_FUSION_POLICY_DISABLED,
        BLOCK_INSUFFICIENT_FUSION_SOURCES,
        BLOCK_SINGLE_REAL_PERSON,
        BLOCK_PUBLIC_FIGURE,
        BLOCK_LIKENESS_RISK,
        BLOCK_UNLICENSED_SOURCE,
    }
)


class HardBlockOverrideError(RuntimeError):
    """试图人工推翻 IdentityDNA 硬性拦截时抛出。

    这是红线保护：hard_blocks 非空的 Gate，任何 human_decision 都无效。
    """


@dataclass
class IdentityGateReport(GateReport):
    """IdentityDNA Gate Report（cinemadna.identity_gate.v1）。

    比通用 GateReport 多一个 hard_blocks 字段，并覆写 has_hard_block。
    """

    hard_blocks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        unknown = [b for b in self.hard_blocks if b not in ALL_HARD_BLOCKS]
        if unknown:
            raise schemas.SchemaValidationError(f"未登记的 hard_block 代码: {unknown}")
        # 不变式：有硬拦截就绝不可能 passed
        if self.hard_blocks and self.passed:
            raise schemas.SchemaValidationError(
                f"Gate {self.gate_id} 存在硬拦截 {self.hard_blocks} 却标记 passed=True"
            )

    @property
    def has_hard_block(self) -> bool:
        return bool(self.hard_blocks)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        # 按接口文档 §3.3 的字段顺序：scores 之后紧跟 hard_blocks
        out: dict[str, Any] = {}
        for k, v in d.items():
            out[k] = v
            if k == "scores":
                out["hard_blocks"] = list(self.hard_blocks)
        return out


# ---------------------------------------------------------------------------
# 硬性检测：单一真人克隆
# ---------------------------------------------------------------------------


def _dedup_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 source_id 去重：防止「同一张脸报 3 次」凑够张数。"""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for s in sources:
        sid = str(s.get("source_id", ""))
        if sid and sid not in seen:
            seen.add(sid)
            out.append(s)
    return out


def detect_single_real_clone(identity_pack: dict[str, Any]) -> list[str]:
    """核心红线检测：判断该身份是否构成「单一真人克隆」或授权风险。

    返回命中的 hard_block 代码列表（空列表 = 未命中红线）。
    本函数独立于打分逻辑，可被单独测试与审计。
    """
    blocks: list[str] = []
    sources = _dedup_sources(list(identity_pack.get("fusion_sources") or []))
    real_sources = [s for s in sources if bool(s.get("is_real_person", True))]

    # 1) 融合源数量不足 → 非多人脸融合。
    #    但**纯合成人脸**（无任何真实人脸来源，如 FLUX 文生图）没有真人可克隆，
    #    不受"至少 3 源"约束——多人脸融合是为了稀释真人相似度，纯生成无需稀释。
    #    只要存在真实人脸来源，就必须凑够 3 源（防"1 真人 + 0 陪衬"的裸克隆）。
    if real_sources and len(sources) < MIN_FUSION_SOURCES:
        blocks.append(BLOCK_INSUFFICIENT_FUSION_SOURCES)

    for s in sources:
        # 2) 授权红线：来源必须是授权 / 合成 / 公有领域
        if str(s.get("license", "unknown")) not in ALLOWED_SOURCE_LICENSES:
            if BLOCK_UNLICENSED_SOURCE not in blocks:
                blocks.append(BLOCK_UNLICENSED_SOURCE)

        is_real_person = bool(s.get("is_real_person", True))
        weight = float(s.get("weight", 0.0))
        sim = float(s.get("similarity_to_result", 0.0))

        # 3) 单一真人主导：权重占比过高 → 事实上的克隆
        #    只对"真人源"生效；纯合成源占比高不构成肖像权风险。
        if is_real_person and weight >= MAX_SINGLE_SOURCE_WEIGHT:
            if BLOCK_SINGLE_REAL_PERSON not in blocks:
                blocks.append(BLOCK_SINGLE_REAL_PERSON)

        # 4) 成品与单一真人源相似度过高 → 克隆（即便权重被伪造得很低）
        if is_real_person and sim >= MAX_SINGLE_SOURCE_SIMILARITY:
            if BLOCK_SINGLE_REAL_PERSON not in blocks:
                blocks.append(BLOCK_SINGLE_REAL_PERSON)

        # 5) 公众人物相似度
        if float(s.get("public_figure_similarity", 0.0)) >= MAX_PUBLIC_FIGURE_SIMILARITY:
            if BLOCK_PUBLIC_FIGURE not in blocks:
                blocks.append(BLOCK_PUBLIC_FIGURE)

    # 6) 独立的公众人物比对结果（不依附于融合源）
    for m in identity_pack.get("public_figure_matches") or []:
        if float(m.get("similarity", 0.0)) >= MAX_PUBLIC_FIGURE_SIMILARITY:
            if BLOCK_PUBLIC_FIGURE not in blocks:
                blocks.append(BLOCK_PUBLIC_FIGURE)

    return blocks


def compute_likeness_risk(identity_pack: dict[str, Any]) -> float:
    """综合肖像权风险 = max(与任一真人源相似度, 与任一公众人物相似度)。

    取 max 而非平均：肖像权风险是"最坏情况"问题，
    不能用两张不相干的脸把一张高相似的脸平均掉。
    """
    risks = [0.0]
    for s in _dedup_sources(list(identity_pack.get("fusion_sources") or [])):
        if bool(s.get("is_real_person", True)):
            risks.append(float(s.get("similarity_to_result", 0.0)))
        risks.append(float(s.get("public_figure_similarity", 0.0)))
    for m in identity_pack.get("public_figure_matches") or []:
        risks.append(float(m.get("similarity", 0.0)))
    return round(max(risks), 4)


# ---------------------------------------------------------------------------
# Gate 主入口
# ---------------------------------------------------------------------------


def run_identity_gate(
    *,
    gate_id: str,
    workorder_id: str,
    identity_pack: dict[str, Any],
    requirement: dict[str, Any],
) -> IdentityGateReport:
    """执行 IdentityDNA Gate，返回 IdentityGateReport。

    判定顺序刻意固定：先硬拦截，再软性打分。
    只要 hard_blocks 非空，无论分数多高一律 passed=False。
    """
    hard_blocks: list[str] = []

    # --- 0) 策略开关本身不可关闭 -------------------------------------------
    # 调用方若把 must_multi_face_fusion / forbid_single_real_clone 置为 false，
    # 视为试图绕过红线，直接硬拦截。
    if not requirement.get("must_multi_face_fusion", False):
        hard_blocks.append(BLOCK_FUSION_POLICY_DISABLED)
    if not requirement.get("forbid_single_real_clone", False):
        if BLOCK_FUSION_POLICY_DISABLED not in hard_blocks:
            hard_blocks.append(BLOCK_FUSION_POLICY_DISABLED)

    # --- 1) 单一真人克隆 / 授权 / 公众人物 ---------------------------------
    for b in detect_single_real_clone(identity_pack):
        if b not in hard_blocks:
            hard_blocks.append(b)

    # --- 2) 综合风险阈值 ----------------------------------------------------
    likeness_risk = compute_likeness_risk(identity_pack)
    if likeness_risk > MAX_LIKENESS_RISK and BLOCK_LIKENESS_RISK not in hard_blocks:
        hard_blocks.append(BLOCK_LIKENESS_RISK)

    # --- 3) 软性质量分 ------------------------------------------------------
    naturalness = round(float(identity_pack.get("naturalness", 0.0)), 4)
    ethnicity_match = round(float(identity_pack.get("ethnicity_match", 0.0)), 4)

    need_family = bool(requirement.get("need_family", False))
    family_consistency = round(float(identity_pack.get("family_consistency", 0.0)), 4)
    if not need_family:
        # 未要求家族脸谱时该项不参与判定，置 1.0 表示"不适用且不扣分"
        family_consistency = 1.0

    overall = round(
        0.35 * naturalness
        + 0.20 * ethnicity_match
        + 0.15 * family_consistency
        + 0.30 * (1.0 - likeness_risk),
        4,
    )

    issues: list[str] = []
    if naturalness < MIN_NATURALNESS:
        issues.append(f"自然度不足: {naturalness} < {MIN_NATURALNESS}")
    if ethnicity_match < MIN_ETHNICITY_MATCH:
        issues.append(f"族裔特征匹配不足: {ethnicity_match} < {MIN_ETHNICITY_MATCH}")
    if need_family and family_consistency < MIN_FAMILY_CONSISTENCY:
        issues.append(f"家族一致性不足: {family_consistency} < {MIN_FAMILY_CONSISTENCY}")
    if overall < MIN_OVERALL:
        issues.append(f"综合分不足: {overall} < {MIN_OVERALL}")

    # 年龄线完整性（要求了年龄线就必须真的产出）
    if requirement.get("need_age_line") and not identity_pack.get("age_line"):
        issues.append("需求要求年龄线，但身份包内 age_line 为空")

    # 硬拦截的可读说明也写进 issues，便于 Web 看板直接展示
    for b in hard_blocks:
        issues.append(f"[HARD_BLOCK] {b}")

    passed = not hard_blocks and not issues

    return IdentityGateReport(
        schema_version=schemas.SCHEMA_IDENTITY_GATE,
        gate_id=gate_id,
        workorder_id=workorder_id,
        passed=passed,
        scores={
            "naturalness": naturalness,
            "likeness_risk": likeness_risk,
            "ethnicity_match": ethnicity_match,
            "family_consistency": family_consistency,
            "overall": overall,
        },
        hard_blocks=hard_blocks,
        issues=issues,
        # 硬拦截无需人工复核（人也推翻不了）；软性问题需人来看。
        # 且：**只要生成了真实人脸就必过人脸审核**（肖像/版权必须人来签字）——
        # 这就是工厂 3 个人工点里的「人脸/角色审核」触发条件。
        human_review_required=(
            (bool(issues) and not hard_blocks)
            or (not hard_blocks and bool(identity_pack.get("is_real_image")))
        ),
    )
