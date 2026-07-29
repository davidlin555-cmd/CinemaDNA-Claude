"""Render Brain · Phase 4 Service —— 只认合约的渲染执行层

对应多智能体文档 §2.8。五个 Agent 落成方法：

| 文档中的 Agent            | 本服务对应                                   |
|---------------------------|----------------------------------------------|
| Model Router              | `render.router.route_shot()`                 |
| Local/Cloud Cost Optimizer| `render.router.optimize_cost()`              |
| Reference Control         | `build_request()`（强制注入资产 hash）       |
| Render Executor           | `BackendRegistry` → `RenderBackend`          |
| Consistency Enforcer      | `enforce_consistency()`                      |

## 派单五步（每一步都能拦住问题）

1. **合约铁规** `require_render_ready()`：状态不对 / 资产没过 Gate /
   缺 hash / 签发后被篡改 —— 任一条直接拒渲
2. **路由 + 成本优化**：人脸特写强制云端且不降级
3. **能力检查**：目标后端做不了这个时长/分辨率，当场拒绝，不浪费调用
4. **执行 + 重试**：`RetryableRenderError` 自动重试，`FatalRenderError` 立即判死
5. **产物校验**：`validate_rendered_shot()` 防串单/防垃圾返回；
   声称出片的后端必须真的落下文件

一致性检查分两层：
- **结构层（真判定）**：同一人物跨镜头必须同一 Master Pack 与同一 asset_hash
- **打分层（mock）**：identity/scene/motion 分数是确定性伪随机，
  仅用于验证"低分会被打回"这条通路
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asset_brain.common import schemas
from asset_brain.common.bundle import Bundle
from director.shot_contract import (
    STATUS_FAILED,
    bind_render_result,
    require_render_ready,
)

from .backend import (
    FatalRenderError,
    MediaSink,
    RenderBackend,
    RenderError,
    RenderRequest,
    RetryableRenderError,
    seed_from,
    validate_rendered_shot,
)
from .budget import BudgetError
from .registry import BackendRegistry
from .router import cost_summary, optimize_cost, route_shot

#: 一致性分数下限，低于此值的镜头判为需要重渲
MIN_IDENTITY_CONSISTENCY = 0.75
MIN_SCENE_CONSISTENCY = 0.70

#: 单镜头最大尝试次数（含首次）
DEFAULT_MAX_ATTEMPTS = 2

#: 通用负面提示词：短剧最常见的翻车点
DEFAULT_NEGATIVE_PROMPT = "多余手指, 面部畸变, 五官漂移, 字幕水印, 镜头抖动, 光影跳变"


class RenderRejected(RuntimeError):
    """镜头被渲染层拒收（合约不合法 / 未过 Gate / 被篡改 / 超出能力）。"""


@dataclass
class RenderResult:
    rendered: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    routing: list[dict[str, Any]] = field(default_factory=list)
    consistency_report: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    backends: dict[str, Any] = field(default_factory=dict)
    retries: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.rejected
            and not self.failed
            and self.consistency_report.get("passed", False)
        )

    @property
    def rendered_duration_sec(self) -> float:
        return round(sum(r["duration_sec"] for r in self.rendered), 1)

    @property
    def has_real_media(self) -> bool:
        return bool(self.rendered) and all(
            r.get("is_real_media") for r in self.rendered
        )

    def artifacts(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "08_submissions/routing_decisions.json": {
                "schema_version": schemas.SCHEMA_ROUTING_DECISION,
                "decisions": self.routing,
                "cost": self.cost,
                "backends": self.backends,
            },
            "11_review/consistency/report.json": self.consistency_report,
        }
        for r in self.rendered:
            out[f"07_payloads/render/{r['shot_id']}.json"] = r
        return out

    def summary(self) -> dict[str, Any]:
        return {
            "rendered": len(self.rendered),
            "rejected": len(self.rejected),
            "failed": len(self.failed),
            "retries": self.retries,
            "rendered_duration_sec": self.rendered_duration_sec,
            "consistency_passed": self.consistency_report.get("passed"),
            "cloud_ratio": self.cost.get("cloud_ratio"),
            "estimated_cost_total": self.cost.get("estimated_cost_total"),
            "is_real_media": self.has_real_media,
            "is_generated_footage": bool(self.rendered)
            and all(r.get("is_generated_footage") for r in self.rendered),
        }


class RenderBrainService:
    """Render Brain Phase 4 Service。"""

    def __init__(
        self,
        backend: RenderBackend | None = None,
        *,
        registry: BackendRegistry | None = None,
        bundle: Bundle | None = None,
        max_cloud_ratio: float = 0.5,
        local_first: bool = True,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        if registry is not None and backend is not None:
            raise ValueError("registry 与 backend 只能给一个")
        self.registry = registry or BackendRegistry(default=backend)
        self.sink = MediaSink(bundle)
        self.bundle = bundle
        self.max_cloud_ratio = max_cloud_ratio
        self.local_first = local_first
        self.max_attempts = max(1, int(max_attempts))

    # ------------------------------------------------------------------
    # Reference Control：把已过 Gate 的资产 hash 强制注入请求
    # ------------------------------------------------------------------

    @staticmethod
    def build_request(
        contract: dict[str, Any], decision: dict[str, Any]
    ) -> RenderRequest:
        assets = contract["assets"]
        scene = assets["scene"]
        cam = contract["camera"]
        beats = contract["performance"]["acting_beats"]
        tags = "，".join(scene.get("tags") or [])
        emo = contract["emotion"]
        # 动作驱动：把**这一镜要发生的动作**（story_function=节拍描述）放在最前，让
        # image2video 生成真实动作而非"证件照动嘴"。再叠场景/景别/情绪/微表情。
        action = (contract.get("story_function") or "").strip()
        shift = (contract.get("internal_shift") or "").strip()   # 镜内状态变化
        atoms = (contract.get("performance_atoms_hint") or "").strip()  # 原子表演驱动
        mood = emo.get("mood") or ""
        line = (contract.get("dialogue") or [{}])[0].get("line", "")
        prompt = (
            f"{action}。" if action else ""
        ) + (
            f"镜内变化：{shift}。" if shift else ""
        ) + (
            f"{atoms}。" if atoms else ""          # 原子表演：动作链+表情链
        ) + (
            f"{tags}。{cam['shot_size']}·{cam['movement']}·{cam['angle']}。"
            f"情绪：{mood}（强度{emo.get('intensity')}），"
            f"{beats[0]['micro_expression']['primary']}。"
        ) + (f"台词：{line}" if line else "")
        return RenderRequest(
            shot_id=contract["shot_id"],
            task_id=contract["task_id"],
            story_id=contract["story_id"],
            bundle_id=contract["bundle_id"],
            contract_hash=contract["contract_hash"],
            model=decision["model"],
            tier=decision["tier"],
            duration_sec=float(contract["duration_sec"]),
            seed=seed_from(contract["contract_hash"], contract["shot_id"]),
            prompt=prompt,
            negative_prompt=DEFAULT_NEGATIVE_PROMPT,
            camera={**cam, "shot_type": contract["shot_type"]},
            emotion=dict(contract["emotion"]),
            performance=contract["performance"],
            dialogue=list(contract.get("dialogue") or []),
            reference_hashes={
                "scene": scene["asset_hash"],
                "characters": {
                    c["character_id"]: c["asset_hash"] for c in assets["characters"]
                },
                "props": {p["prop_id"]: p["asset_hash"] for p in assets["props"]},
            },
            reference_assets={
                "scene": scene["asset_id"],
                "characters": {
                    c["character_id"]: c.get("master_pack_id")
                    for c in assets["characters"]
                },
                # 真实人脸图路径（image2video 用；mock 身份为 None）
                "character_images": {
                    c["character_id"]: c.get("base_face_image")
                    for c in assets["characters"]
                },
                "props": {p["prop_id"]: p["asset_id"] for p in assets["props"]},
            },
        )

    @classmethod
    def build_payload(
        cls, contract: dict[str, Any], decision: dict[str, Any]
    ) -> dict[str, Any]:
        """请求的 dict 形式（落盘 / 看板用）。"""
        return cls.build_request(contract, decision).to_dict()

    # ------------------------------------------------------------------
    # Consistency Enforcer
    # ------------------------------------------------------------------

    @staticmethod
    def enforce_consistency(
        contracts: list[dict[str, Any]], rendered: list[dict[str, Any]]
    ) -> dict[str, Any]:
        structural: list[str] = []
        low_score: list[str] = []

        packs: dict[str, set[tuple[str, str]]] = {}
        for c in contracts:
            for ch in c["assets"]["characters"]:
                packs.setdefault(ch["character_id"], set()).add(
                    (str(ch.get("master_pack_id")), str(ch.get("asset_hash")))
                )
        for cid, variants in packs.items():
            if len(variants) > 1:
                structural.append(
                    f"人物 {cid} 在不同镜头引用了不同的身份资产 {sorted(variants)}，"
                    f"角色必然漂移"
                )

        for r in rendered:
            m = r.get("metrics") or {}
            if m.get("identity_consistency", 1.0) < MIN_IDENTITY_CONSISTENCY:
                low_score.append(
                    f"{r['shot_id']} 身份一致性 {m['identity_consistency']} < "
                    f"{MIN_IDENTITY_CONSISTENCY}"
                )
            if m.get("scene_consistency", 1.0) < MIN_SCENE_CONSISTENCY:
                low_score.append(
                    f"{r['shot_id']} 场景一致性 {m['scene_consistency']} < "
                    f"{MIN_SCENE_CONSISTENCY}"
                )

        issues = structural + low_score
        return {
            "schema_version": schemas.SCHEMA_CONSISTENCY_REPORT,
            "passed": not issues,
            "issues": issues,
            "structural_issues": structural,
            "low_score_issues": low_score,
            "thresholds": {
                "identity": MIN_IDENTITY_CONSISTENCY,
                "scene": MIN_SCENE_CONSISTENCY,
            },
            "scores_are_mock": all(
                (r.get("metrics") or {}).get("mock", False) for r in rendered
            ),
            "checked_at": schemas.utc_now_iso(),
        }

    # ------------------------------------------------------------------
    # 执行单镜（含重试与产物校验）
    # ------------------------------------------------------------------

    def _execute(self, request: RenderRequest) -> tuple[dict[str, Any], int]:
        backend = self.registry.get(request.model)
        issues = backend.capabilities.check(request)
        if issues:
            raise FatalRenderError(
                f"{request.shot_id}: 后端 {backend.name} 做不了这个镜头 -> {issues}"
            )

        retries = 0
        last: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                rendered = backend.render(request, sink=self.sink)
            except RetryableRenderError as e:
                last = e
                retries += 1
                continue
            validate_rendered_shot(
                rendered, request,
                duration_tolerance=backend.capabilities.duration_tolerance_sec,
            )
            # 声称出片的后端必须真的落下文件
            if backend.capabilities.produces_media and self.sink.available:
                if not self.sink.exists(rendered["file_relpath"]):
                    raise FatalRenderError(
                        f"{request.shot_id}: 后端声称已出片，但 "
                        f"{rendered['file_relpath']} 不存在"
                    )
            return rendered, retries
        raise RenderError(
            f"{request.shot_id}: 重试 {self.max_attempts} 次仍失败 -> {last}"
        ) from last

    # ------------------------------------------------------------------
    # 主流程
    # ------------------------------------------------------------------

    def render_all(self, contracts: list[dict[str, Any]]) -> RenderResult:
        result = RenderResult(backends=self.registry.describe())

        # 1) 入口铁规
        accepted: list[dict[str, Any]] = []
        for c in contracts:
            try:
                require_render_ready(c)
                accepted.append(c)
            except Exception as e:
                c["status"] = STATUS_FAILED
                result.rejected.append({"shot_id": c.get("shot_id"), "reason": str(e)})

        # 2) 路由 + 成本优化
        decisions = [route_shot(c, local_first=self.local_first) for c in accepted]
        decisions = optimize_cost(decisions, max_cloud_ratio=self.max_cloud_ratio)
        for d in decisions:
            d["backend"] = self.registry.get(d["model"]).name
            d["real_backend"] = self.registry.has_real_backend(d["model"])
        result.routing = decisions
        result.cost = cost_summary(decisions, max_cloud_ratio=self.max_cloud_ratio)
        by_shot = {d["shot_id"]: d for d in decisions}

        # 3) 执行
        for c in accepted:
            request = self.build_request(c, by_shot[c["shot_id"]])
            try:
                rendered, retries = self._execute(request)
                result.retries += retries
            except BudgetError as e:
                # 预算闸拦截：干净地记为失败镜头，不崩溃、不重试、不记账
                c["status"] = STATUS_FAILED
                result.failed.append(
                    {"shot_id": c["shot_id"], "reason": f"预算闸拦截: {e}"}
                )
                continue
            except RenderError as e:
                c["status"] = STATUS_FAILED
                result.failed.append({"shot_id": c["shot_id"], "reason": str(e)})
                continue
            bind_render_result(c, rendered)
            result.rendered.append(rendered)

        # 4) 一致性
        result.consistency_report = self.enforce_consistency(accepted, result.rendered)
        return result
