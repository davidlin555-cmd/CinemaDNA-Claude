"""首帧素材闸（fail-fast）—— 付费 Kling 渲染**之前**,用便宜的首帧验主角锁脸。

复盘([[i2v-reference-lock-gap]] / [[staged-gates-fail-fast]]): 身份漂移本可在首帧阶段
(FLUX/PuLID,分钱)拦下,却拖到 Kling 渲染后(元钱)才由 InsightFace 发现 → REJECTED 重来。
根治 = **逐级把关 + 尽早失败**: 每个主角单人镜的首帧必须锁到该角色的铸造锚脸
(首帧→锚脸 cos 距 < 阈值),不过 → 拦下(不花 Kling 钱) → 自修再验。

诚实: 这是**把检查前移成闸**,不改生成质量。首帧落盘缓存,预验不导致 render_all 重复付费。
判官可注入假 embedder → 0 成本测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

#: 首帧→锚脸 cos 距阈值。实测 PuLID 锁的首帧≈0.15,漏成 FLUX 随机脸≈0.66-0.97,
#: 0.35 干净分开(与 identity/consistency.DRIFT_THRESHOLD 对齐)。
FIRST_FRAME_IDENTITY_THRESHOLD = 0.35


@dataclass
class FirstFrameCheck:
    """一个主角镜的首帧核验记录。"""
    shot_id: str
    character_id: str
    tier: str                      # pulid_anchor / scene_first_frame / gold_multiangle
    distance: float | None         # 首帧→锚脸 cos 距；None=无法比对
    ok: bool
    reason: str = ""


@dataclass
class PreflightResult:
    checks: list[FirstFrameCheck] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # 无锚脸/双人/无脸镜:不判
    embedder_is_real: bool = False

    @property
    def failed(self) -> list[FirstFrameCheck]:
        return [c for c in self.checks if not c.ok]

    @property
    def ready(self) -> bool:
        return not self.failed

    @property
    def failed_ids(self) -> list[str]:
        return [c.shot_id for c in self.failed]

    def report(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "checked": len(self.checks),
            "passed": len([c for c in self.checks if c.ok]),
            "failed": [{"shot_id": c.shot_id, "character_id": c.character_id,
                        "tier": c.tier, "distance": c.distance, "reason": c.reason}
                       for c in self.failed],
            "skipped": self.skipped,
            "embedder_is_real": self.embedder_is_real,
            "threshold": FIRST_FRAME_IDENTITY_THRESHOLD,
        }


class FirstFrameIdentityPreflight:
    """纯验证器: 对每个「主角单人镜」的首帧核验身份锁定。生成/落盘由调用方(pipeline
    用真 backend)完成,这里只吃已备好的记录 → 可 0 成本注入假 embedder 测。

    embed_png(path)->vec|None: 对 PNG 抽脸签名(InsightFace);
    anchor_for(cid)->Path|None: 该角色铸造锚脸图; cos_dist(a,b)->float。
    """

    def __init__(self, *, embed_png: Callable[[Path], Any],
                 anchor_for: Callable[[str], "Path | None"],
                 cos_dist: Callable[[Any, Any], float],
                 embedder_is_real: bool = False,
                 threshold: float = FIRST_FRAME_IDENTITY_THRESHOLD) -> None:
        self.embed_png = embed_png
        self.anchor_for = anchor_for
        self.cos_dist = cos_dist
        self.embedder_is_real = embedder_is_real
        self.threshold = threshold
        self._anchor_cache: dict[str, Any] = {}

    def _anchor_embed(self, cid: str) -> Any:
        if cid not in self._anchor_cache:
            ap = self.anchor_for(cid)
            self._anchor_cache[cid] = self.embed_png(ap) if ap else None
        return self._anchor_cache[cid]

    def check(self, shots: list[dict[str, Any]]) -> PreflightResult:
        """shots = [{shot_id, character_id, single_char: bool, tier: str,
                     first_frame: Path, has_anchor: bool}]。

        只核验「有锚脸 + 单人 + 主角在场」的镜: 首帧→锚脸 < 阈值。其余(双人建立镜/
        插入镜/无锚脸角色)不判,记 skipped(它们不承诺 PuLID 锁脸)。
        """
        res = PreflightResult(embedder_is_real=self.embedder_is_real)
        for sh in shots or []:
            sid = sh.get("shot_id", "?")
            cid = sh.get("character_id") or ""
            if not (sh.get("single_char") and sh.get("has_anchor") and cid):
                res.skipped.append(sid)
                continue
            tier = str(sh.get("tier") or "")
            ff = sh.get("first_frame")
            # ① 主角单人镜却没走 PuLID 锚脸 = 正是漏锁 bug → 直接判失败(不用比对)
            if tier != "pulid_anchor":
                res.checks.append(FirstFrameCheck(
                    sid, cid, tier or "none", None, False,
                    f"主角单人镜未锁 PuLID 锚脸(tier={tier or 'none'}),会渲成随机脸"))
                continue
            # ② 走了 PuLID → 用锚脸真比对(防 PuLID 生成噪声/换脸)
            anchor = self._anchor_embed(cid)
            fv = self.embed_png(Path(ff)) if ff else None
            if anchor is None or fv is None:
                # 抽不到脸: 诚实不误杀(交后续渲染后 InsightFace 兜底),但记录
                res.checks.append(FirstFrameCheck(
                    sid, cid, tier, None, True, "首帧/锚脸抽不到脸,跳过比对(不误杀)"))
                continue
            d = round(float(self.cos_dist(fv, anchor)), 4)
            ok = d < self.threshold
            res.checks.append(FirstFrameCheck(
                sid, cid, tier, d, ok,
                "" if ok else f"首帧→锚脸距 {d} ≥ {self.threshold}(未锁到同一张脸)"))
        return res


__all__ = ["FirstFrameIdentityPreflight", "PreflightResult", "FirstFrameCheck",
           "FIRST_FRAME_IDENTITY_THRESHOLD"]
