"""Pipeline Orchestrator — 多故事并行状态机骨架 (Phase 1)

对应主规格 L2 与多智能体文档 §2.1。本骨架把该文档里的几个 Agent 职责
落成方法（Phase 1 不引入真实 Agent，只保证调度语义正确）：

| 文档中的 Agent        | 本骨架对应                                        |
|-----------------------|---------------------------------------------------|
| State Manager         | `Story` 状态机 + `factory_status()`               |
| Parallel Scheduler    | `submit_script()` 返回的并行信号 + 槽位模型        |
| Human Gate Manager    | `hold_for_human()` / `release_hold()` / `pending_holds()` |
| Bundle Lifecycle      | `create_story()` 建 Bundle、`archive_story()` 关 Bundle |
| Workflow Planner      | `next_actionable()`（挑出此刻真能推进的故事）      |
| Cost / Budget Governor| 未实现（Phase 1 明确不做）                        |

## 并行的核心：ScriptBrain 槽位

ScriptBrain 是产能瓶颈，用 `max_concurrent_scripts` 建模（默认 1）。
一个故事**仅在 stage=SCRIPT_RUNNING 且 status=RUNNING 时**占用槽位：

- 剧本提交 → SCRIPT_DONE → 槽位立刻释放 → 下一部剧马上能开
  （不需要等 A 的资产/渲染/发布）
- A 卡在人工闸门 → status=WAITING_HUMAN → 槽位同样释放
  （「Human Gate 不阻塞其它故事」）

`submit_script()` 因此返回一个显式的**并行信号**，供 Web / 调度器直接消费。

## 与资产大脑的对接（Bundle 隔离要点）

三大库是全局共享的（资产要跨剧复用），但 Bundle 必须每剧独立。
所以本类持有**一个共享 AssetBrainStore + 每个故事一个 AssetBrainFacade**，
后者绑定该故事自己的 Bundle。共用一个 facade 会把 A 的资产写进 B 的 Bundle。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.common.bundle import Bundle
from cinemadna.asset_brain.common.store import AssetBrainStore
from cinemadna.asset_brain.facade import (
    OUTCOME_PENDING_HUMAN_REVIEW,
    OUTCOME_REJECTED,
    OUTCOME_REJECTED_HARD_BLOCK,
    AssetBrainFacade,
)

from audio.service import AudioDNAService

from director.service import DirectorDNAService
from performance.service import PerformanceDNAService
from final_review.service import (
    VERDICT_HUMAN as VERDICT_FR_HUMAN,
    VERDICT_PASS_FULL as VERDICT_FR_PASS_FULL,
    VERDICT_PASS_STRUCTURAL as VERDICT_FR_PASS_STRUCTURAL,
    VERDICT_REJECTED as VERDICT_FR_REJECTED,
    FinalReviewService,
)
from prerender.service import PreRenderService
from qa.repair import VERDICT_HUMAN, VERDICT_REPAIR
from qa.service import ShotQAService
from render.assembly import assemble_rough_cut, export_cut
from render.backend import RenderBackend
from render.registry import BackendRegistry
from render.service import RenderBrainService
from scriptbrain.service import ScriptBrainService

from . import repair as repair_policy
from .story import (
    InvalidStageTransition,
    Story,
    StoryStage,
    StoryStatus,
)

#: 自修复时：要重做某阶段的产物，就把 stage 回滚到它的**前驱**，
#: 再由前向驱动重跑产生该阶段的方法（这样真的重做，而不是跳过）。
_REDO_PREDECESSOR = {
    StoryStage.ASSET_MATCHING: StoryStage.SCRIPT_DONE,   # 重做资产 → dispatch_assets
    StoryStage.DIRECTING: StoryStage.ASSET_READY,        # 重做导演 → run_director
    StoryStage.PERFORMANCE: StoryStage.DIRECTING,        # 重做表演 → run_performance
    StoryStage.RENDERING: StoryStage.PERFORMANCE,        # 重做渲染 → run_render
}


class OrchestratorError(RuntimeError):
    """Orchestrator 层通用错误。"""


class StoryNotFoundError(OrchestratorError):
    """故事不存在。"""


class ParallelCapacityError(OrchestratorError):
    """ScriptBrain 并发槽位已满，暂不能开新剧的剧本生产。"""


#: 资产未过闸的两种失败结局
_ASSET_FAILURE_OUTCOMES = (OUTCOME_REJECTED, OUTCOME_REJECTED_HARD_BLOCK)


class PipelineOrchestrator:
    """多故事并行状态机（唯一能创建故事和关闭 Bundle 的模块）。"""

    def __init__(
        self,
        *,
        store: AssetBrainStore | None = None,
        bundle_root: str | Path | None = None,
        max_concurrent_scripts: int = 1,
        bundle_date: str | None = None,
        image_backend=None,
        screen_backend=None,
        audio_backend=None,
        audio_mixer=None,
    ) -> None:
        if max_concurrent_scripts < 1:
            raise OrchestratorError("max_concurrent_scripts 至少为 1")
        #: 可选真实 TTS 后端（ElevenLabs）；None=声音只做契约层（不出声）
        self.audio_backend = audio_backend
        #: 可选 ffmpeg 音画合成器；None=不做音画混流
        self.audio_mixer = audio_mixer
        #: 可选多模态视觉深判后端（LLM）；None=只跑 ffmpeg 像素层，深判如实标未接。
        #: 调 enable_vision_llm() 接 Claude 视觉（判变脸/崩坏/乱码；产生 API 费用）。
        self.vision_llm = None
        #: 可选真实唇形后端；None=直通（lip_synced=False，诚实标记）
        self.lipsync_backend = None
        #: story_id → 唇形状态 {lip_synced, backend, note}
        self._lip_sync: dict[str, Any] = {}
        #: 驱动器渲染用的默认后端注册表（None=RenderBrain 默认 mock）
        self.render_registry = None
        #: 商业短剧 AI Council（LLM 责编评审）；None=按需构造（有 ANTHROPIC key 时）
        self._commercial_council = None
        self.store = store if store is not None else AssetBrainStore()
        self.bundle_root = Path(bundle_root) if bundle_root is not None else None
        self.max_concurrent_scripts = max_concurrent_scripts
        #: 可选真实图像后端（IdentityDNA 真实出图）；None=mock
        self.image_backend = image_backend
        #: 屏幕道具渲染后端（本地 Chrome，免费）；None=让 facade 自动探测
        self.screen_backend = screen_backend
        #: bundle_id 的日期戳；显式传入可让测试完全确定
        self.bundle_date = bundle_date or schemas.utc_now_iso()[:10].replace("-", "")

        self._stories: dict[str, Story] = {}
        self._bundles: dict[str, Bundle] = {}
        self._facades: dict[str, AssetBrainFacade] = {}
        #: story_id → ScriptBrain 产出的 scene_export（资产分发的默认输入）
        self._scene_exports: dict[str, dict[str, Any]] = {}
        #: story_id → 完整拍摄版剧本（Director 需要台词、节拍、动作）
        self._shooting_scripts: dict[str, dict[str, Any]] = {}
        #: story_id → 镜头合约（Render 的唯一合法输入）
        self._contracts: dict[str, list[dict[str, Any]]] = {}
        #: story_id → Director 的连续性检查报告（QA 会复用）
        self._director_reports: dict[str, dict[str, Any]] = {}
        self._render_results: dict[str, Any] = {}
        self._prerender_results: dict[str, Any] = {}
        #: 自修复重试计数 story_id → {code: 次数}（跨回滚累计，决定何时升级）
        self._repair_attempts: dict[str, dict[str, int]] = {}
        #: 全厂修复统计 code → {episodes, exhausted}（供 OptimizerDNA 复盘）
        self._repair_stats: dict[str, dict[str, int]] = {}
        #: OptimizerDNA 学到的可逆调参档（空档=出厂默认）
        from optimizer.profile import TuningProfile
        self.tuning = TuningProfile()
        self._optimizer = None
        self._qa_results: dict[str, Any] = {}
        self._audio_reviews: dict[str, Any] = {}
        self._showquality: dict[str, Any] = {}
        self._master_plans: dict[str, Any] = {}   # DirectorMasterPlan：唯一上游真源
        self._director_qa: dict[str, Any] = {}
        self._exec_contracts: dict[str, Any] = {}  # DirectorExecutionContract：可拍摄事实
        self._quarantine: dict[str, Any] = {}      # ③ Vision 熔断隔离结果
        #: story_id → {shot_id: ShotMasterTaskSheet}；分镜任务单体系(注册则渲染入口强制)
        self._shot_sheets: dict[str, dict[str, Any]] = {}
        self._narrative_qa: dict[str, Any] = {}
        self._density_qa: dict[str, Any] = {}
        self._council: dict[str, Any] = {}
        self._final_reviews: dict[str, Any] = {}
        #: 表演圣经要跨故事累积，所以 PerformanceDNA 是工厂级单例
        self._performance = PerformanceDNAService()
        self._seq = 0

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get(self, story_id: str) -> Story:
        s = self._stories.get(story_id)
        if s is None:
            raise StoryNotFoundError(f"故事不存在: {story_id!r}")
        return s

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _require_stage(self, s: Story, *expected: StoryStage) -> Story:
        if s.stage not in expected:
            raise InvalidStageTransition(
                f"故事 {s.story_id} 当前阶段 {s.stage.value}，"
                f"该操作要求 {[e.value for e in expected]}"
            )
        return s

    def bundle_for(self, story_id: str) -> Bundle | None:
        """取该故事的 Active Production Bundle（未配置 bundle_root 时为 None）。"""
        return self._bundles.get(story_id)

    def facade_for(self, story_id: str) -> AssetBrainFacade:
        """取该故事专属的资产大脑门面（共享三大库，独占自己的 Bundle）。"""
        s = self._get(story_id)
        if s.story_id not in self._facades:
            self._facades[s.story_id] = AssetBrainFacade(
                store=self.store, bundle=self._bundles.get(s.story_id),
                image_backend=self.image_backend,
                screen_backend=self.screen_backend,
            )
        return self._facades[s.story_id]

    def _snapshot(self, s: Story) -> None:
        """把故事清单写进它自己的 Bundle，供看板与事后审计。"""
        b = self._bundles.get(s.story_id)
        if b is not None:
            b.write_json("01_script/story_manifest.json", s.to_dict())

    # ------------------------------------------------------------------
    # 1. 创建故事（Bundle Lifecycle Agent）
    # ------------------------------------------------------------------

    def create_story(
        self,
        *,
        title: str = "",
        story_id: str | None = None,
        bundle_id: str | None = None,
        priority: str = "normal",
    ) -> Story:
        """创建新故事并分配独立 Active Production Bundle。

        创建本身**不占用** ScriptBrain 槽位 —— 排队等着开工是允许的，
        真正受并发约束的是 `start_script()`。
        """
        n = self._next_seq()
        story_id = story_id or f"drama_{n:04d}"
        if story_id in self._stories:
            raise OrchestratorError(f"故事已存在: {story_id}")
        bundle_id = bundle_id or f"bundle_{self.bundle_date}_{n:03d}"

        s = Story(
            story_id=story_id, bundle_id=bundle_id, title=title, priority=priority
        )
        self._stories[story_id] = s

        if self.bundle_root is not None:
            self._bundles[story_id] = Bundle(self.bundle_root, bundle_id).ensure()
        self._snapshot(s)
        return s

    # ------------------------------------------------------------------
    # 2. 剧本阶段与并行信号（Parallel Scheduler）
    # ------------------------------------------------------------------

    def script_slots_available(self) -> int:
        """当前还能同时开启几部剧的剧本生产。"""
        used = sum(1 for s in self._stories.values() if s.occupies_script_slot)
        return max(0, self.max_concurrent_scripts - used)

    def can_start_new_story(self) -> bool:
        return self.script_slots_available() > 0

    def start_script(self, story_id: str) -> Story:
        """启动某故事的 ScriptBrain（CREATED → SCRIPT_RUNNING）。"""
        s = self._require_stage(self._get(story_id), StoryStage.CREATED)
        if not self.can_start_new_story():
            raise ParallelCapacityError(
                f"ScriptBrain 槽位已满（{self.max_concurrent_scripts}），"
                f"故事 {story_id} 需排队；已在跑的剧本提交后会立即释放槽位"
            )
        s.advance_to(StoryStage.SCRIPT_RUNNING)
        self._snapshot(s)
        return s

    def submit_script(
        self, story_id: str, *, script_ref: str, note: str = ""
    ) -> dict[str, Any]:
        """**并行核心**：ScriptBrain 提交完成，立刻释放槽位并返回并行信号。

        返回结构即多智能体文档里的 `new_story_trigger`：调用方（Web / 调度器）
        拿到 new_story_allowed=True 就可以马上开下一部剧，无需关心本剧后续。
        """
        s = self._require_stage(self._get(story_id), StoryStage.SCRIPT_RUNNING)
        s.script_ref = script_ref
        s.advance_to(StoryStage.SCRIPT_DONE, note=note or script_ref)
        s.script_submitted_at = s.updated_at
        self._snapshot(s)

        slots = self.script_slots_available()
        return {
            "story_id": story_id,
            "script_ref": script_ref,
            "submitted_at": s.script_submitted_at,
            "new_story_allowed": slots > 0,
            "script_slots_available": slots,
            "queued_story_ids": [
                st.story_id
                for st in self._stories.values()
                if st.stage is StoryStage.CREATED
            ],
        }

    # ------------------------------------------------------------------
    # 2b. ScriptBrain 对接（L4 编剧工厂）
    # ------------------------------------------------------------------

    def run_scriptbrain(
        self,
        story_id: str,
        brief: Any,
        *,
        scriptbrain: "ScriptBrainService | None" = None,
    ) -> dict[str, Any]:
        """跑一遍 ScriptBrain，产出拍摄版并（在 Gate 3 通过时）自动提交。

        - Gate 3 自动通过 → 直接 `submit_script()`，当场释放并行槽位
        - Gate 3 未过     → 挂 WAITING_HUMAN 等人工放行（**不占槽位**，
          其它故事照常开工），人工放行后由 `approve_script_gate()` 续跑提交

        剧本产物全部落进本剧 Bundle 的 `01_script/` 与 `11_review/`。
        """
        s = self._get(story_id)
        if s.stage is StoryStage.CREATED:
            self.start_script(story_id)
        self._require_stage(s, StoryStage.SCRIPT_RUNNING)

        svc = scriptbrain if scriptbrain is not None else ScriptBrainService()
        result = svc.run(brief)

        b = self._bundles.get(story_id)
        if b is not None:
            for relpath, payload in result.artifacts().items():
                b.write_json(relpath, payload)

        self._scene_exports[story_id] = result.scene_export
        self._shooting_scripts[story_id] = result.shooting_script
        s.script_summary = result.summary()
        s.log_event(
            "SCRIPT_GENERATED",
            script_id=result.script_id,
            scenes=result.scene_count,
            gate3_passed=not result.needs_human_review,
        )

        if result.needs_human_review:
            s.hold_for_human(
                "script_gate3",
                gate_ids=[result.script_id],
                note="; ".join(result.gate3["issues"])[:500],
            )
            self._snapshot(s)
            return {
                "story_id": story_id,
                "state": s.state,
                "script_id": result.script_id,
                "summary": result.summary(),
                "parallel_signal": None,
            }

        signal = self.submit_script(
            story_id, script_ref="01_script/shooting_script.json", note=result.script_id
        )
        return {
            "story_id": story_id,
            "state": s.state,
            "script_id": result.script_id,
            "summary": result.summary(),
            "parallel_signal": signal,
        }

    def approve_script_gate(
        self, story_id: str, human_decision: dict[str, Any]
    ) -> dict[str, Any]:
        """人工裁决 ScriptBrain Gate 3。

        通过 → 释放挂起并立即提交剧本（此时才产生并行信号）；
        否决 → 释放挂起但停在 SCRIPT_RUNNING，等待重跑 `run_scriptbrain()`。

        注意：这是**编辑质量**闸门，人工有权放行；不要与 IdentityDNA 的
        法律红线（`approve_gate` 遇 hard_blocks 直接抛错）混淆。
        """
        s = self._get(story_id)
        if s.status is not StoryStatus.WAITING_HUMAN or not s.hold:
            raise InvalidStageTransition(f"故事 {story_id} 当前没有待裁决的剧本闸门")
        if s.hold.get("reason") != "script_gate3":
            raise OrchestratorError(
                f"故事 {story_id} 的挂起原因是 {s.hold.get('reason')!r}，不是剧本闸门"
            )

        decided_by = human_decision.get("decided_by", "human")
        approved = bool(human_decision.get("approved"))
        s.release_hold(decided_by=decided_by)
        s.log_event("SCRIPT_GATE3_DECIDED", approved=approved, decided_by=decided_by)

        if not approved:
            self._snapshot(s)
            return {
                "story_id": story_id,
                "state": s.state,
                "approved": False,
                "parallel_signal": None,
            }

        signal = self.submit_script(
            story_id, script_ref="01_script/shooting_script.json", note="human_approved"
        )
        return {
            "story_id": story_id,
            "state": s.state,
            "approved": True,
            "parallel_signal": signal,
        }

    def scene_export_for(self, story_id: str) -> dict[str, Any] | None:
        """取该故事最近一次 ScriptBrain 产出的 scene_export。"""
        return self._scene_exports.get(story_id)

    # ------------------------------------------------------------------
    # 3. 资产匹配（对接 AssetBrainFacade）
    # ------------------------------------------------------------------

    def dispatch_assets(
        self, story_id: str, script: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """SCRIPT_DONE → ASSET_MATCHING：把剧本需求分发给三大资产模型。

        结局三选一：
        - 全部命中/回流 → ASSET_READY
        - 有 Gate 待人工复核 → WAITING_HUMAN（stage 停在 ASSET_MATCHING）
        - 有 Gate 判负     → BLOCKED（含 IdentityDNA 红线）

        `script` 省略时使用 ScriptBrain 产出的 scene_export。
        """
        s = self._get(story_id)
        if script is None:
            script = self._scene_exports.get(story_id)
            if script is None:
                raise OrchestratorError(
                    f"故事 {story_id} 没有可分发的剧本：先 run_scriptbrain()，"
                    f"或显式传入 script"
                )
        # 传进来的若是完整拍摄版（带 episodes），顺手记下供 Director 使用
        if script.get("episodes") and story_id not in self._shooting_scripts:
            self._shooting_scripts[story_id] = script

        # 宪法层发单门：无锁定的 DirectorMasterPlan，资产生产一律不得开工。
        # 直接调用（未经驱动器）时自动补跑总导演规划+验收，保证"先有总谱"。
        if self._master_plans.get(story_id) is None and s.stage is StoryStage.SCRIPT_DONE:
            self.run_director_planning(story_id)
            self.run_director_qa(story_id)
            s = self._get(story_id)
        from director.gates import require_locked_plan
        require_locked_plan(self._master_plans.get(story_id), action="资产生产")

        if s.stage in (StoryStage.SCRIPT_DONE, StoryStage.DIRECTOR_QA):
            s.advance_to(StoryStage.ASSET_MATCHING)
        self._require_stage(s, StoryStage.ASSET_MATCHING)

        fac = self.facade_for(story_id)
        results: dict[str, list[dict[str, Any]]] = {
            "scene": [],
            "identity": [],
            "prop": [],
        }

        # 绑定表：下游 Director 靠它把场景与资产对上号
        bindings: dict[str, Any] = {"scene_by_id": {}, "character_ids": [],
                                    "prop_ids": []}

        for scene in _iter_scenes(script):
            r = fac.request_scene(scene, s.quad(kind="scenedna"))
            results["scene"].append(r)
            if r["asset_ids"]:
                bindings["scene_by_id"][scene["scene_id"]] = r["asset_ids"][0]
        for char in script.get("characters") or []:
            results["identity"].append(
                fac.request_identity(char, s.quad(kind="identitydna"))
            )
            bindings["character_ids"].append(char["character_id"])
        for prop in script.get("props") or []:
            results["prop"].append(
                fac.request_prop(
                    prop,
                    s.quad(kind="propdna"),
                    # 剧本自带的跨场景状态时间线直接驱动 PropDNA 的连续性
                    state_timeline=prop.get("state_timeline"),
                )
            )
            bindings["prop_ids"].append(prop["prop_id"])
        s.asset_bindings = bindings

        flat = [r for group in results.values() for r in group]
        failed = [r for r in flat if r["outcome"] in _ASSET_FAILURE_OUTCOMES]

        def _pending(group: str) -> list[str]:
            return [r["gate_id"] for r in results[group]
                    if r["outcome"] == OUTCOME_PENDING_HUMAN_REVIEW]

        # 只有**人脸/角色**的待审进人工（点 2）；场景/道具待审自动放行（非人工点）
        identity_pending = _pending("identity")
        auto_pending = _pending("scene") + _pending("prop")
        hard = [r for r in flat if r["outcome"] == OUTCOME_REJECTED_HARD_BLOCK]
        soft_failed = [r for r in flat if r["outcome"] == OUTCOME_REJECTED]

        fac = self.facade_for(story_id)
        for gid in auto_pending:
            fac.approve_gate(gid, {"approved": True, "decided_by": "auto_gate"})
            s.log_event("AUTO_GATE_APPROVED", gate_id=gid)

        s.asset_summary = {
            "total": len(flat),
            "by_outcome": _count_by(flat, "outcome"),
            "pending_gate_ids": identity_pending,      # 仅剩人脸待审
            "auto_approved_gate_ids": auto_pending,
            "failed_workorder_ids": [r["workorder_id"] for r in failed],
        }

        if hard:
            # 红线（单一真人克隆）→ 永不自动绕过，直接升级人脸审核
            self._enter_auto_repair(
                s, "IDENTITY_REDLINE",
                detail="; ".join(f"{r['workorder_id']}({r['outcome']})" for r in hard))
        elif soft_failed:
            # 场景/道具/身份软判负 → 自动重生成
            self._enter_auto_repair(
                s, "ASSET_REGEN",
                detail="; ".join(f"{r['workorder_id']}({r['outcome']})"
                                 for r in soft_failed))
        elif identity_pending:
            self._human_hold(s, repair_policy.IDENTITY_REVIEW,
                             gate_ids=identity_pending)
        else:
            s.advance_to(StoryStage.ASSET_READY)

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "results": results,
                "summary": s.asset_summary}

    def retry_assets(
        self, story_id: str, *, plan_override: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """BLOCKED 的故事重生成资产：**只重跑判负的那几张工单**，不重跑整部剧。

        `plan_override` 会合并进这些工单的 generation_plan（例如换掉违规的
        融合源、改回自然原子路线）。不改参数的重试会被同一套 Gate 再拦一次 ——
        这是刻意的：红线不因为"再试一次"而松动。
        """
        s = self._get(story_id)
        # 手动重生成入口（人工在人脸审核处提供合法融合源后调用）：
        # BLOCKED / AUTO_REPAIRING 都可，人工放行后（WAITING_HUMAN 已 release）亦可
        if s.status not in (StoryStatus.BLOCKED, StoryStatus.AUTO_REPAIRING,
                            StoryStatus.RUNNING):
            raise InvalidStageTransition(
                f"故事 {story_id} 当前 {s.status.value}，不处于可重生成状态"
            )
        self._require_stage(s, StoryStage.ASSET_MATCHING)

        failed = list(s.asset_summary.get("failed_workorder_ids") or [])
        if not failed:
            raise OrchestratorError(f"故事 {story_id} 没有记录在案的失败工单")

        fac = self.facade_for(story_id)
        results = [
            fac.regenerate(wid, plan_override=plan_override) for wid in failed
        ]
        still_failed = [
            r["workorder_id"] for r in results if r["outcome"] in _ASSET_FAILURE_OUTCOMES
        ]
        pending = [
            r["gate_id"]
            for r in results
            if r["outcome"] == OUTCOME_PENDING_HUMAN_REVIEW
        ]

        s.asset_summary["failed_workorder_ids"] = still_failed
        s.asset_summary["retry_count"] = s.asset_summary.get("retry_count", 0) + 1
        s.log_event(
            "ASSET_RETRY",
            attempted=len(failed),
            still_failed=len(still_failed),
            pending=len(pending),
        )

        if still_failed:
            # 手动重生成后仍判负 → 回自修复态（红线仍会再次升级人脸审核）
            hard = any(r["outcome"] == OUTCOME_REJECTED_HARD_BLOCK for r in results)
            self._enter_auto_repair(
                s, "IDENTITY_REDLINE" if hard else "ASSET_REGEN",
                detail=f"重生成后仍未过闸: {still_failed}")
        else:
            if s.status in (StoryStatus.BLOCKED, StoryStatus.AUTO_REPAIRING):
                s.clear_auto_repair(note="重生成通过") \
                    if s.status is StoryStatus.AUTO_REPAIRING else s.unblock("重生成通过")
            # 人脸待审 → 人工点 2；场景/道具此路不该有 pending（已在 dispatch 自动放行）
            if pending:
                self._human_hold(s, repair_policy.IDENTITY_REVIEW, gate_ids=pending)
            else:
                s.advance_to(StoryStage.ASSET_READY)

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "results": results,
                "summary": s.asset_summary}

    def resolve_asset_gate(
        self, story_id: str, gate_id: str, human_decision: dict[str, Any]
    ) -> dict[str, Any]:
        """人工裁决某个资产 Gate；全部裁完且无判负则自动续跑到 ASSET_READY。"""
        s = self._get(story_id)
        if s.status is not StoryStatus.WAITING_HUMAN or not s.hold:
            raise InvalidStageTransition(f"故事 {story_id} 当前没有待人工裁决的 Gate")
        if gate_id not in s.hold["gate_ids"]:
            raise OrchestratorError(
                f"Gate {gate_id} 不在故事 {story_id} 的待审列表 {s.hold['gate_ids']}"
            )

        fac = self.facade_for(story_id)
        result = fac.approve_gate(gate_id, human_decision)

        remaining = [g for g in s.hold["gate_ids"] if g != gate_id]
        decided_by = human_decision.get("decided_by", "human")
        rejected = result["outcome"] == OUTCOME_REJECTED

        s.hold["gate_ids"] = remaining
        s.log_event("ASSET_GATE_DECIDED", gate_id=gate_id, outcome=result["outcome"],
               decided_by=decided_by)

        if not remaining:
            s.release_hold(decided_by=decided_by)
            if rejected or s.asset_summary.get("failed_workorder_ids"):
                # 人工否决人脸 → 自动重生成一张（不是停厂）
                self._enter_auto_repair(
                    s, "ASSET_REGEN", detail=f"人工否决人脸 Gate {gate_id}")
            else:
                s.advance_to(StoryStage.ASSET_READY)
        elif rejected:
            # 还有别的 Gate 在审，但本次否决已经决定了这部剧过不了闸
            s.asset_summary.setdefault("failed_workorder_ids", []).append(
                result["workorder_id"]
            )

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "gate_result": result,
                "remaining_gate_ids": remaining}

    # ------------------------------------------------------------------
    # 3b. Director → Performance → Render → 粗剪
    # ------------------------------------------------------------------

    def _require_not_blocked(self, s: Story, action: str) -> Story:
        if s.status in (StoryStatus.BLOCKED, StoryStatus.AUTO_REPAIRING):
            reason = s.blocked_reason or (s.repair_directive or {}).get("code")
            raise InvalidStageTransition(
                f"故事 {s.story_id} 处于 {s.status.value}（{reason}），"
                f"必须先解决问题才能{action}"
            )
        return s

    def _shooting_script_for(self, story_id: str) -> dict[str, Any]:
        script = self._shooting_scripts.get(story_id)
        if script is None:
            raise OrchestratorError(
                f"故事 {story_id} 没有拍摄版剧本：先 run_scriptbrain()，"
                f"或在 dispatch_assets 时传入含 episodes 的完整剧本"
            )
        return script

    def contracts_for(self, story_id: str) -> list[dict[str, Any]]:
        """取该故事的镜头合约（Render 的唯一合法输入）。"""
        return self._contracts.get(story_id, [])

    def run_director_planning(self, story_id: str) -> dict[str, Any]:
        """SCRIPT_DONE → DIRECTOR_PLANNING：总导演理解剧本，出总谱骨架 + 专业细化。

        宪法层上游：先有总谱，下游才能开工。有节拍(LLM作者化)→ 真总谱；模板无节拍
        → 占位总谱（合法锁过发单门，DIRECTING 走 legacy 分镜，保 CI）。
        """
        s = self._require_stage(self._get(story_id), StoryStage.SCRIPT_DONE)
        script = self._shooting_scripts.get(story_id) or {}
        authored = any(sc.get("beats") for ep in script.get("episodes") or []
                       for sc in ep.get("scenes") or [])
        from director.master_plan import DirectorMasterPlan
        if authored:
            from director.agents.showrunner import ShowrunnerDirector
            from director.agents.director_qa import DirectorQAOfficer
            plan = ShowrunnerDirector().build_skeleton(script, story_id=story_id)
            for ag in DirectorQAOfficer().specialists:
                if hasattr(ag, "refine"):
                    ag.refine(plan)
        else:
            plan = DirectorMasterPlan(
                story_id=story_id, story_spine={}, cast_lock={}, scene_layout=[],
                prop_plan=[], emotion_curve=[], density_plan={"legacy": True},
                shot_list=[])
        self._master_plans[story_id] = plan
        s.advance_to(StoryStage.DIRECTOR_PLANNING)
        s.log_event("DIRECTOR_PLANNING", authored=authored, shots=len(plan.shot_list))
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "authored": authored,
                "shots": len(plan.shot_list)}

    def run_director_qa(self, story_id: str) -> dict[str, Any]:
        """DIRECTOR_PLANNING → DIRECTOR_QA：验收官签发 + 锁定总谱（唯一真源）。

        硬门（完整性/可执行动作/story_function）不过 → 不锁定、升级人工，绝不下行。
        专业智能体的建议记录但不阻塞。模板占位总谱直接锁定过门（advisory）。
        """
        s = self._require_stage(self._get(story_id), StoryStage.DIRECTOR_PLANNING)
        plan = self._master_plans.get(story_id)
        legacy = bool((getattr(plan, "density_plan", {}) or {}).get("legacy"))
        b = self._bundles.get(story_id)
        if plan is None:
            raise OrchestratorError(f"故事 {story_id} 没有总谱，先 run_director_planning()")
        if legacy or not plan.shot_list:
            plan.lock()
            self._director_qa[story_id] = {"verdict": "PASS", "legacy": True}
            s.advance_to(StoryStage.DIRECTOR_QA)
            s.log_event("DIRECTOR_QA", verdict="PASS", legacy=True)
            self._snapshot(s)
            return {"story_id": story_id, "state": s.state, "verdict": "PASS"}
        from director.agents.director_qa import DirectorQAOfficer
        res = DirectorQAOfficer().review(plan)
        self._director_qa[story_id] = res.report()
        if b is not None:
            b.write_json(f"11_review/director/{story_id}.json", res.report())
        s.advance_to(StoryStage.DIRECTOR_QA)
        if res.passed:
            plan.lock()                              # 通过才锁定 → 下游可开工
            # ①执行引擎：把总谱编译成"可拍摄事实"执行合同 + 自检 + 落盘
            self._compile_execution_contract(story_id, plan)
            s.log_event("DIRECTOR_QA", verdict="PASS", plan_hash=plan.plan_hash,
                        soft=len(res.soft_issues))
            # 派任务单：每镜作者化成 A–K 主任务单(各 owner 写块)+齐套校验+登记 →
            # 缺块=BLOCKED 暴露(不假填)。**就地把关**：缺块 → 当步拦下升级人工(不下行,
            # 不拖到渲染),把缺口尽早暴露。健康总谱全过(实测12/12)。
            sheet_report = self._author_shot_sheets(story_id, plan)
            if not sheet_report.get("all_valid", True):
                detail = "; ".join(
                    f"{b['shot_id']}({'/'.join(b['issues'])})"
                    for b in sheet_report.get("blocked", [])[:6])
                self._escalate(s, repair_policy.ESC_FINAL,
                               "派任务单硬门未过（缺块暴露，不假填）：" + detail[:300])
        else:
            # 硬门不过：总谱**不锁定**，发单门会拦下游；升级成片人工终确认看问题
            s.log_event("DIRECTOR_QA", verdict="REPAIR", hard=len(res.hard_issues))
            self._escalate(s, repair_policy.ESC_FINAL,
                           "导演验收硬门未过：" + "; ".join(
                               i["message"] for i in res.hard_issues[:5]))
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "verdict": res.verdict}

    def director_plan_for(self, story_id: str):
        return self._master_plans.get(story_id)

    def execution_contract_for(self, story_id: str):
        return self._exec_contracts.get(story_id)

    def quarantine_for(self, story_id: str):
        return self._quarantine.get(story_id)

    @staticmethod
    def _shot_intent(contract: dict[str, Any]) -> str:
        """一句话导演意图（给语义 VisionQA 判"演的对不对、有无不当内容"）。"""
        fn = contract.get("story_function", "")
        dlg = contract.get("dialogue") or []
        line = dlg[0].get("line", "") if dlg else ""
        perf = (contract.get("performance") or {}).get("acting_beats") or []
        action = next((b.get("body", "") for b in perf if b.get("body")), "")
        parts = [p for p in (fn, action,
                             (f"台词「{line[:16]}」" if line else "")) if p]
        return "；".join(parts) or fn or "推进剧情"

    def _author_shot_sheets(self, story_id: str, plan) -> dict[str, Any]:
        """派任务单：锁定总谱 → 每镜 A–K 主任务单(各 owner 写块,缺字段从本镜真实数据
        派生、缺数据留空=BLOCKED 暴露)。作者化+齐套校验+登记。返回 {authored, blocked}。

        用执行合同的槽位注册表做 char→slot;用 scene_layout 做场景上下文。登记后渲染前
        硬门(_require_shot_sheets_for_render)逐镜强制。不阻断发单(校验结果落盘+事件)。
        """
        from director.shot_sheet_author import ShotSheetAuthor
        ec = self._exec_contracts.get(story_id)
        char_to_slot = {sl.character_id: sid
                        for sid, sl in (ec.slot_registry.items() if ec else [])}
        layout = {x["scene_id"]: x for x in (getattr(plan, "scene_layout", None) or [])}
        author = ShotSheetAuthor()
        sheets: dict[str, Any] = {}
        blocked: list[dict[str, Any]] = []
        for sp in plan.shots:
            sc = layout.get(sp.scene_id) or {}
            scene_ctx = {"story_id": story_id, "location": sc.get("location", ""),
                         "cause": sc.get("cause", ""), "effect": sc.get("effect", ""),
                         "blocking": sc.get("blocking", ""), "mood": sp.emotion}
            sheet, issues = author.author_and_lock(
                sp, char_to_slot=char_to_slot, scene_ctx=scene_ctx)
            sheets[sp.shot_id] = sheet
            if issues:
                blocked.append({"shot_id": sp.shot_id,
                                "issues": [i.get("message", str(i)) for i in issues][:5]})
        self.register_shot_sheets(story_id, sheets)
        report = {"story_id": story_id, "authored": len(sheets),
                  "blocked": blocked, "all_valid": not blocked}
        s = self._get(story_id)
        b = self._bundles.get(story_id)
        if b is not None:
            b.write_json(f"11_review/shot_sheets/{story_id}.json", report)
        s.log_event("SHOT_SHEET_AUTHORED",
                    verdict="PASS" if not blocked else "BLOCK",
                    authored=len(sheets), blocked=len(blocked))
        return report

    def register_shot_sheets(self, story_id: str, sheets: dict[str, Any]) -> None:
        """登记分镜主任务单 {shot_id: ShotMasterTaskSheet}。登记后渲染入口逐镜强制。"""
        self._shot_sheets[story_id] = dict(sheets or {})

    def shot_sheet_for(self, story_id: str, shot_id: str):
        return (self._shot_sheets.get(story_id) or {}).get(shot_id)

    def _require_shot_sheets_for_render(self, story_id: str) -> bool:
        """硬门：登记了分镜任务单的故事，**每个待渲镜必须有有效任务单**（无单/未校验/
        hash 篡改/shot_id 不符 → 拒渲升级人工）。未登记 → 豁免（保既有 plan 流程）。"""
        sheets = self._shot_sheets.get(story_id)
        if not sheets:
            return True                              # 未启用任务单体系 → 不强制
        from director.shot_task_sheet import (
            require_shot_task_sheet, ShotTaskSheetError)
        s = self._get(story_id)
        try:
            for c in self.contracts_for(story_id):
                require_shot_task_sheet(sheets.get(c["shot_id"]), c["shot_id"])
        except ShotTaskSheetError as e:
            s.log_event("SHOT_SHEET_GATE", verdict="BLOCK", detail=str(e)[:200])
            self._escalate(s, repair_policy.ESC_FINAL,
                           "分镜任务单硬门未过（缺块/无单/篡改）：" + str(e)[:300])
            self._snapshot(s)
            return False
        return True

    #: 是否优先用 InsightFace 真判脸（默认开；模型加载失败自动回退代理）
    use_insightface: bool = True

    def _identity_service(self, bundle):
        """跨镜身份一致性服务：优先 InsightFace 真判脸(is_real=True)，
        模型加载失败(无网/未下模型) → 回退 numpy 代理(is_real=False，如实标注)。"""
        from identity.consistency import IdentityConsistencyService
        if self.use_insightface:
            try:
                from identity.insightface_embedder import InsightFaceEmbedder
                emb = InsightFaceEmbedder()
                emb._get_app()                       # 预热：触发模型加载，失败即回退
                return IdentityConsistencyService(
                    embedder=emb, threshold=emb.recommended_threshold, bundle=bundle)
            except Exception:  # noqa: BLE001 —— 模型不可用 → 代理兜底，不挡总审
                pass
        return IdentityConsistencyService(bundle=bundle)

    def enable_vision_llm(self, backend: Any = None, *, model: str | None = None) -> None:
        """接 LLM 多模态视觉深判（判变脸/手脸崩坏/画内乱码/表演僵硬）。

        ⚠️ 会产生 Anthropic 视觉 API 费用（每帧 1 张小图 + 短提示，claude-haiku 便宜档，
        约每帧几厘~几分钱）。不改生成、不刷分——只让"评分可信"。
        """
        if backend is None:
            from final_review.vision_llm import AnthropicVisionJudge
            backend = (AnthropicVisionJudge(model=model) if model
                       else AnthropicVisionJudge())
        self.vision_llm = backend

    def _compile_execution_contract(self, story_id: str, plan) -> None:
        """①Director Execution Engine：总谱 → 可拍摄事实合同 + BlockingValidator 自检。

        编译不阻塞发单（合同是渲染前硬门的凭证）；自检结果落盘、事件记录。
        合同未通过自检 → validated=False，run_render 的硬门会据此拦渲。
        """
        from director.execution import (
            ExecutionContractCompiler, BlockingValidator)
        ec = ExecutionContractCompiler().compile(plan)
        ok, issues = BlockingValidator().validate_and_lock(ec)
        self._exec_contracts[story_id] = ec
        s = self._get(story_id)
        b = self._bundles.get(story_id)
        if b is not None:
            b.write_json(f"06_shots/execution_contract/{story_id}.json", ec.to_dict())
            if issues:
                b.write_json(f"11_review/execution/{story_id}.json",
                             {"validated": ok, "issues": issues})
        s.log_event("EXEC_CONTRACT", validated=ok, shots=len(ec.shots),
                    slots=len(ec.slot_registry), issues=len(issues))

    def _require_execution_contract_for_render(self, story_id: str) -> bool:
        """B·硬门：无执行合同 / 未过自检 → 禁止渲染。返回 True=放行。

        仅对**真总谱**（非 legacy、有镜头）强制；legacy/占位或无总谱（纯渲染单测）
        豁免，保持既有行为。不过 → 进 EXEC_FIX 自动修复（回导演重编执行合同）。
        """
        plan = self._master_plans.get(story_id)
        legacy = bool((getattr(plan, "density_plan", {}) or {}).get("legacy"))
        if plan is None or legacy or not getattr(plan, "shot_list", None):
            return True                              # 无真总谱 → 不强制（既有单测路径）
        ec = self._exec_contracts.get(story_id)
        from director.execution import ExecutionContractError, require_execution_contract
        s = self._get(story_id)
        try:
            for c in self.contracts_for(story_id):
                require_execution_contract(ec, c["shot_id"])   # 逐镜过硬门
        except ExecutionContractError as e:
            # 硬门：无可拍摄事实/自检失败 → 升级成片人工（导演层问题，不自动绕过）
            s.log_event("EXEC_CONTRACT_GATE", verdict="BLOCK", detail=str(e)[:200])
            self._escalate(s, repair_policy.ESC_FINAL,
                           "执行合同硬门未过（无可拍摄事实/自检失败）：" + str(e)[:300])
            self._snapshot(s)
            return False
        return True

    def _first_frame_backend(self, backend, registry):
        """取能生成首帧的底层 backend（SceneGrounded…，带 foundry）。"""
        be = backend if backend is not None else (
            registry.default if registry is not None else (
                self.render_registry.default if self.render_registry is not None
                else None))
        return be if getattr(be, "foundry", None) is not None else None

    def _first_frame_gate_records(self, be, contracts, bundle):
        """付费 Kling 前**先只生成首帧**(便宜,落盘缓存 render_all 复用不重复付费),
        收集每镜 {tier/首帧路径/是否单人主角} 供身份核验。"""
        from render.service import RenderBrainService
        recs = []
        decision = {"model": getattr(be, "model_name", "kling"), "tier": "standard"}
        for c in contracts:
            sid = c["shot_id"]
            try:
                req = RenderBrainService.build_request(c, decision)
            except Exception as e:  # noqa: BLE001 建请求失败 → 记 gen_error,核验会拦
                recs.append({"shot_id": sid, "character_id": "",
                             "single_char": False, "tier": "gen_error",
                             "has_anchor": False, "first_frame": None,
                             "_err": str(e)[:120]})
                continue
            # 在场角色 = 与 backend `_in_frame_cids` 同源(渲染期合约无 characters 顶层键,
            # 真源是 build_request 装配的 reference_assets.character_images)。
            cids = list((getattr(req, "reference_assets", None) or {}).get(
                "character_images") or {})
            primary = cids[0] if cids else ""
            has_anchor = bool(primary and be.foundry.has_anchor(primary))
            try:
                be._reference_image_b64(req)      # 生成+缓存首帧(不提交 Kling)
                try:
                    be._tail_image_b64(req)
                except Exception:  # noqa: BLE001 尾帧非必需
                    pass
            except Exception as e:  # noqa: BLE001 生成失败 → 记 gen_error,核验会拦
                recs.append({"shot_id": sid, "character_id": primary,
                             "single_char": len(cids) == 1, "tier": "gen_error",
                             "has_anchor": has_anchor, "first_frame": None,
                             "_err": str(e)[:120]})
                continue
            tier, key = (be._ref_used.get(sid) or ("none", ""))
            fname = f"pulid_{key}.png" if tier == "pulid_anchor" else f"{key}.png"
            ff = (bundle.path_for(f"08_submissions/first_frames/{fname}")
                  if bundle is not None else None)
            recs.append({"shot_id": sid, "character_id": primary,
                         "single_char": len(cids) == 1, "tier": tier,
                         "has_anchor": has_anchor,
                         "first_frame": str(ff) if ff else None})
        return recs

    def run_first_frame_gate(self, story_id: str, *, backend=None, registry=None,
                             max_regen: int = 1) -> dict[str, Any]:
        """首帧素材闸(fail-fast)：付费 Kling **之前**验每个主角单人镜首帧锁没锁对锚脸。

        - tier 检查(主角单人镜必须走 PuLID 锚脸)0 成本,无真判官也能抓漏锁 bug；
        - 距离检查(首帧→锚脸<阈值)用 InsightFace,防 PuLID 生成噪声/换脸。
        不过 → 清掉坏首帧重生成(bounded)→ 仍不过则拦渲(不烧 Kling 钱)+升级人工。
        返回 {ready: bool, ...}；ready=False 时 run_render 不会提交 Kling。
        """
        s = self._get(story_id)
        be = self._first_frame_backend(backend, registry)
        if be is None:                        # 无 foundry(mock/单测) → 不强制,放行
            return {"ready": True, "skipped": "no_foundry"}
        contracts = self.contracts_for(story_id)
        if not contracts:
            return {"ready": True, "skipped": "no_contracts"}
        # 真判官(InsightFace)可用则做距离比对,否则只做 tier 检查(仍能抓漏锁)
        embed_png, is_real = self._first_frame_embedder()
        from render.preflight import FirstFrameIdentityPreflight
        from identity.consistency import _cos_dist
        pf = FirstFrameIdentityPreflight(
            embed_png=embed_png, anchor_for=be.foundry.anchor_for,
            cos_dist=_cos_dist, embedder_is_real=is_real)

        b = self._bundles.get(story_id)
        res = None
        for attempt in range(max_regen + 1):
            try:
                recs = self._first_frame_gate_records(be, contracts, b)
            except Exception as e:  # noqa: BLE001 闸自身故障 → 不硬拦(渲染后有兜底)
                s.log_event("FIRST_FRAME_GATE", verdict="ERROR", detail=str(e)[:200])
                return {"ready": True, "skipped": "gate_error", "error": str(e)[:200]}
            res = pf.check(recs)
            if res.ready or attempt >= max_regen:
                break
            # 清掉不过的首帧缓存 → 下一轮重生成(路由已修,应转 PuLID)
            for c in res.failed:
                rec = next((r for r in recs if r["shot_id"] == c.shot_id), None)
                ff = rec and rec.get("first_frame")
                if ff:
                    try:
                        Path(ff).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass

        report = res.report()
        if b is not None:
            b.write_json(f"11_review/first_frame_gate/{story_id}.json", report)
        s.log_event("FIRST_FRAME_GATE", verdict="PASS" if res.ready else "BLOCK",
                    checked=report["checked"], failed=len(res.failed),
                    real=is_real)
        if not res.ready:
            detail = "; ".join(
                f"{c.shot_id}({c.reason})" for c in res.failed[:6])
            self._escalate(s, repair_policy.ESC_IDENTITY,
                           "首帧素材闸未过（主角镜首帧未锁锚脸，禁烧 Kling）：" + detail[:300])
            self._snapshot(s)
        return {"ready": res.ready, "report": report}

    #: 逐级质量闸链(溯源用): (步骤名, 事件名集合, 判过函数)。按生产顺序。
    _GATE_CHAIN = [
        ("剧本", ("SCRIPT_GATE3_DECIDED", "SCRIPT_GENERATED"),
         lambda e: bool(e.get("gate3_passed", e.get("passed")))),
        ("导演分镜", ("DIRECTOR_QA",), lambda e: e.get("verdict") == "PASS"),
        ("分镜任务单(A–K/执行合同)",
         ("SHOT_SHEET_AUTHORED", "SHOT_SHEET_GATE", "EXEC_CONTRACT_GATE",
          "EXEC_CONTRACT"),
         lambda e: (e.get("verdict") != "BLOCK") and e.get("validated", True)),
        ("叙事连贯", ("NARRATIVE_QA",), lambda e: e.get("verdict") != "BLOCK"),
        ("渲染前商业闸", ("PRERENDER_GATE",),
         lambda e: int(e.get("blockers", 0)) == 0),
        ("首帧素材", ("FIRST_FRAME_GATE",), lambda e: e.get("verdict") == "PASS"),
    ]

    def gate_chain_report(self, story_id: str) -> dict[str, Any]:
        """逐级把关溯源：把每步的最新闸 verdict 汇成一条可追溯记录（剧本→导演分镜→
        任务单→叙事→渲染前→首帧）。一眼看到"哪步过没过、卡在哪"。落 11_review/gate_chain/。

        只读事件流聚合,不产生新判定(不改生产)。步骤未跑到=PENDING。
        """
        s = self._get(story_id)
        events = s.events or []
        steps = []
        first_block = None
        for name, evnames, ok_fn in self._GATE_CHAIN:
            ev = None
            for e in reversed(events):
                if e.get("event") in evnames:
                    ev = e
                    break
            if ev is None:
                steps.append({"step": name, "status": "PENDING", "ok": None})
                continue
            try:
                ok = bool(ok_fn(ev))
            except Exception:  # noqa: BLE001
                ok = None
            steps.append({"step": name, "status": "PASS" if ok else "BLOCK",
                          "ok": ok, "event": ev.get("event"), "at": ev.get("at")})
            if ok is False and first_block is None:
                first_block = name
        passed = sum(1 for st in steps if st["ok"] is True)
        report = {"story_id": story_id, "steps": steps,
                  "passed": passed, "total": len(self._GATE_CHAIN),
                  "first_block": first_block,
                  "all_clear": first_block is None
                  and all(st["ok"] is not False for st in steps)}
        b = self._bundles.get(story_id)
        if b is not None:
            b.write_json(f"11_review/gate_chain/{story_id}.json", report)
        return report

    def _first_frame_embedder(self):
        """返回 (embed_png(path)->vec|None, is_real)。真判官不可用 → 恒 None(仅 tier 检查)。"""
        if self.use_insightface:
            try:
                from identity.insightface_embedder import InsightFaceEmbedder
                emb = InsightFaceEmbedder()
                emb._get_app()                    # 预热,失败即回退
                return (lambda p: emb.embed_image(Path(p)) if p else None), True
            except Exception:  # noqa: BLE001 模型不可用 → 只做 tier 检查
                pass
        return (lambda p: None), False

    def _broadcast_plan(self, story_id: str, plan) -> dict[str, Any]:
        """把锁定总谱镜头化成合约（下游只执行，合约携带 plan_hash 追溯）。"""
        s = self._get(story_id)
        from director.broadcast import plan_to_shots
        shots = plan_to_shots(plan)
        svc = DirectorDNAService(self.store, self._bundles.get(story_id))
        result = svc.run(
            self._shooting_script_for(story_id), story_id=s.story_id,
            bundle_id=s.bundle_id,
            scene_bindings=dict((s.asset_bindings or {}).get("scene_by_id") or {}),
            task_id_factory=lambda: s.new_task_id("shot"), plan_shots=shots)
        b = self._bundles.get(story_id)
        if b is not None:
            for rel, payload in result.artifacts().items():
                b.write_json(rel, payload)
        self._contracts[story_id] = result.contracts
        self._director_reports[story_id] = result.continuity_report
        s.shot_summary = result.summary()
        s.log_event("SHOTS_BROADCAST", shots=result.shot_count,
                    plan_hash=plan.plan_hash)
        if not result.passed:
            self._enter_auto_repair(
                s, "CONTINUITY",
                detail="; ".join(result.continuity_report["issues"]))
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "summary": result.summary(),
                "contracts": result.contracts}

    def run_director(
        self, story_id: str, shooting_script: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """ASSET_READY → DIRECTING：把**锁定总谱**镜头化成合约（下游只执行）。

        有真总谱(含镜头)→ broadcast 总谱为合约（携带 plan_hash 追溯）；
        模板占位总谱 → 走 legacy 分镜（保 CI）。
        """
        s = self._get(story_id)
        if s.stage is StoryStage.ASSET_READY:
            s.advance_to(StoryStage.DIRECTING)
        self._require_stage(s, StoryStage.DIRECTING)
        self._require_not_blocked(s, "进入导演阶段")

        plan = self._master_plans.get(story_id)
        if plan is not None and plan.shot_list:
            return self._broadcast_plan(story_id, plan)

        script = shooting_script or self._shooting_script_for(story_id)
        svc = DirectorDNAService(self.store, self._bundles.get(story_id))
        result = svc.run(
            script,
            story_id=s.story_id,
            bundle_id=s.bundle_id,
            scene_bindings=dict((s.asset_bindings or {}).get("scene_by_id") or {}),
            task_id_factory=lambda: s.new_task_id("shot"),
        )

        b = self._bundles.get(story_id)
        if b is not None:
            for rel, payload in result.artifacts().items():
                b.write_json(rel, payload)

        self._contracts[story_id] = result.contracts
        self._director_reports[story_id] = result.continuity_report
        s.shot_summary = result.summary()
        s.log_event("SHOTS_PLANNED", shots=result.shot_count,
                    duration=result.total_duration_sec)
        if not result.passed:
            # 连续性问题（跨镜身份漂移等）→ 自动打回 Director 重绑，不停厂
            self._enter_auto_repair(
                s, "CONTINUITY",
                detail="; ".join(result.continuity_report["issues"]))

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "summary": result.summary(),
                "contracts": result.contracts}

    def run_performance(self, story_id: str) -> dict[str, Any]:
        """DIRECTING → PERFORMANCE：补表演节拍并**签发合约**。"""
        s = self._require_stage(self._get(story_id), StoryStage.DIRECTING)
        self._require_not_blocked(s, "进入表演阶段")
        contracts = self.contracts_for(story_id)
        if not contracts:
            raise OrchestratorError(f"故事 {story_id} 还没有镜头合约，先 run_director()")

        # 重跑（自修复打回）时合约可能已签发/已渲染 → 复位为 DRAFT 以便重签
        # （保留 render_result：重渲会覆盖，中途回滚也不至于让成片"没素材"）
        from director.shot_contract import STATUS_DRAFT
        for c in contracts:
            if c.get("status") != STATUS_DRAFT:
                c["status"] = STATUS_DRAFT
                c["contract_hash"] = None

        script = self._shooting_script_for(story_id)
        result = self._performance.run(contracts, shooting_script=script)

        b = self._bundles.get(story_id)
        if b is not None:
            for rel, payload in result.artifacts().items():
                b.write_json(rel, payload)
            # 合约已签发，重新落盘（带上表演字段与 contract_hash）
            for c in contracts:
                b.write_json(f"06_shots/contracts/{c['shot_id']}.json", c)

        s.performance_summary = result.summary()
        s.advance_to(StoryStage.PERFORMANCE)
        s.log_event("PERFORMANCE_ATTACHED", **result.summary())
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "summary": result.summary()}

    def run_prerender_gate(self, story_id: str) -> dict[str, Any]:
        """PreRender Commercial Gate V2：渲染前 12 项就绪总闸（PDF §8）。

        必须在 PERFORMANCE 阶段（合约已签发）跑。任一项 BLOCKED → 阻塞故事、
        给出 blocker + next_action，禁止进入渲染。
        """
        s = self._require_stage(self._get(story_id), StoryStage.PERFORMANCE)
        contracts = self.contracts_for(story_id)
        if not contracts:
            raise OrchestratorError(f"故事 {story_id} 还没有镜头合约")
        script = self._shooting_scripts.get(story_id) or {}
        chars = {c["character_id"]: c for c in script.get("characters") or []}

        result = PreRenderService(self._bundles.get(story_id)).run(
            contracts, story_id=s.story_id, characters_by_id=chars,
            script_ready=not bool((s.script_summary or {}).get("needs_human_review")),
            # 配了真实图像后端 = 真实生产模式，三大资产必须真实存在并被校验
            require_real_faces=self.image_backend is not None,
            require_real_assets=self.image_backend is not None)
        self._prerender_results[story_id] = result
        s.prerender_summary = result.summary()
        s.log_event("PRERENDER_GATE", status=result.gate.status,
                    blockers=len(result.gate.blockers))
        if not result.ready:
            # 12 项就绪门失败 → 翻译成修复码，自动打回对应模块补齐/重签
            report = result.gate.report()
            codes = repair_policy.codes_from_prerender(report)
            detail = "; ".join(f"{b['flag']}({b['shot_id']})"
                               for b in result.gate.blockers[:6])
            primary = repair_policy.pick_primary(codes)
            if primary == repair_policy.SCRIPT_REVIEW:
                # 剧本没过 = 人工点 1（理论上此刻不该发生，兜底转人工）
                self._human_hold(s, repair_policy.SCRIPT_REVIEW,
                                 gate_ids=[story_id], note=detail)
            elif primary:
                self._enter_auto_repair(s, primary, detail=detail)
            else:
                self._enter_auto_repair(s, "DIRECTOR_FIX", detail=detail)
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state,
                "summary": result.summary(), "report": result.gate.report()}

    def prerender_for(self, story_id: str):
        return self._prerender_results.get(story_id)

    def telemetry(self) -> dict[str, Any]:
        """全厂遥测（OptimizerDNA 的输入）：修复恢复率 / 人工决策 / 故事结局。"""
        stories = list(self._stories.values())
        repairs: dict[str, dict[str, int]] = {}
        for code, st in self._repair_stats.items():
            ep, ex = st.get("episodes", 0), st.get("exhausted", 0)
            repairs[code] = {"attempts": ep, "recovered": max(0, ep - ex),
                             "failed": ex}
        human: dict[str, dict[str, int]] = {}
        by_status: dict[str, int] = {}
        for s in stories:
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
            for e in s.events:
                if e.get("event") == "HUMAN_NOTE":
                    ctx = str(e.get("context", ""))
                    if ":" in ctx:
                        reason, dec = ctx.rsplit(":", 1)
                        h = human.setdefault(reason, {"approved": 0, "rejected": 0})
                        if dec in h:
                            h[dec] += 1
        return {
            "stories_total": len(stories),
            "stories_failed": sum(1 for s in stories
                                  if s.stage is StoryStage.FAILED),
            "repairs": repairs,
            "human": human,
            "by_status": by_status,
        }

    def run_optimizer(self, *, auto_apply: bool = True) -> dict[str, Any]:
        """跑一轮 OptimizerDNA：复盘遥测 → 自我验收上版 → 自动应用可逆调参。"""
        from optimizer.service import FactoryOptimizer
        if self._optimizer is None:
            self._optimizer = FactoryOptimizer(self.tuning)
        report = self._optimizer.run(self.telemetry(), auto_apply=auto_apply)
        return {**report.to_dict(), "tuning": self.tuning.to_dict()}

    def module_reviews(self, story_id: str) -> list[dict[str, Any]]:
        """统一专业审核层：9 个模块各自的最新审核结论（供控制台/审核中心）。

        不重跑审核，只把每个模块**已产出**的报告翻译成统一 ReviewResult；
        缺报告 = PENDING（尚未走到）。
        """
        from review import registry as rv
        s = self._get(story_id)

        # 三大资产 Gate：按工单类型聚合
        by_type: dict[str, list[dict[str, Any]]] = {
            "SCENE": [], "IDENTITY": [], "PROP": []}
        for wo in self.store.list_workorders(story_id=story_id):
            if wo.gate_report is not None and wo.workorder_type in by_type:
                by_type[wo.workorder_type].append(wo.gate_report)

        qa = self._qa_results.get(story_id)
        audio = self._audio_reviews.get(story_id)
        final = self._final_reviews.get(story_id)

        results = [
            rv.from_script(s.script_summary),
            rv.from_asset_gates("scene", by_type["SCENE"]),
            rv.from_asset_gates("identity", by_type["IDENTITY"]),
            rv.from_asset_gates("prop", by_type["PROP"]),
            rv.from_continuity(self._director_reports.get(story_id)),
            rv.from_qa_report("performance", qa.performance if qa else None),
            rv.from_qa_report("render", qa.vision if qa else None),
            rv.from_audio(audio.report() if audio else None),
            rv.from_final(final.report() if final else None),
        ]
        return [r.to_dict() for r in results]

    def _characters_by_id(self, story_id: str) -> dict[str, Any]:
        script = self._shooting_scripts.get(story_id) or {}
        return {c["character_id"]: c for c in script.get("characters") or []}

    def run_audio_review(self, story_id: str) -> dict[str, Any]:
        """AudioDNA 专业审核（在 PERFORMANCE 阶段，PreRender 之前）。

        声音性别绑定 / 口型策略实测 / 字幕覆盖。不通过 → 自动打回 AUDIO_FIX
        真实修复（重绑声音性别、重推口型、重签合约），不停厂、不等人。
        """
        s = self._get(story_id)
        contracts = self.contracts_for(story_id)
        if not contracts:
            return {"story_id": story_id, "state": s.state, "verdict": "PASS"}
        chars = self._characters_by_id(story_id)
        svc = AudioDNAService(self._bundles.get(story_id),
                              tts_backend=self.audio_backend)
        # 配了 TTS 后端 = 真实出声模式：先补音色，再真实 TTS 出声
        real = self.audio_backend is not None
        if real:
            svc.repair_contracts(contracts, characters_by_id=chars)  # 补音色 voice_id
            synth = svc.synthesize(contracts, characters_by_id=chars)
            if synth:
                s.log_event("AUDIO_SYNTHESIZED", shots=len(synth))
            # 因果链：真实语音生成后，用语音时长回写镜头时长（语音驱动镜头，
            # 不是固定镜头时长反裁语音）。在渲染/PreRender 之前完成并重签。
            reconciled = svc.reconcile_durations(contracts)
            if reconciled:
                s.log_event("DURATION_RECONCILED", shots=len(reconciled))
            # 落盘重签后的合约（含 duration_provenance），供审计"声音是否驱动镜头"
            b = self._bundles.get(story_id)
            if b is not None:
                for c in contracts:
                    b.write_json(f"06_shots/contracts/{c['shot_id']}.json", c)
        review = svc.review(
            contracts, story_id=story_id, characters_by_id=chars,
            require_real_audio=real)
        self._audio_reviews[story_id] = review
        s.log_event("AUDIO_REVIEW", verdict=review.verdict,
                    issues=len(review.issues))
        if not review.passed:
            self._enter_auto_repair(
                s, "AUDIO_FIX",
                detail="; ".join(i["message"] for i in review.issues[:6]))
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state,
                "verdict": review.verdict, "report": review.report()}

    def _shot_dialogue_audio(self, bundle, shot_id: str, bindings: list) -> Path:
        """取一个说话镜的对白音频（单句直接用；多句 ffmpeg 顺接成一条）。"""
        auds = [Path(b["audio_abspath"]) for b in bindings
                if b.get("audio_abspath") and Path(b["audio_abspath"]).is_file()]
        if len(auds) == 1:
            return auds[0]
        dest = bundle.path_for(f"09_downloads/lipsync/_audio/{shot_id}.wav")
        dest.parent.mkdir(parents=True, exist_ok=True)
        import subprocess
        listf = dest.with_suffix(".txt")
        listf.write_text("\n".join(f"file '{a.as_posix()}'" for a in auds), "utf-8")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(listf), "-c", "copy", str(dest)], timeout=120)
        return dest if dest.is_file() else auds[0]

    def run_lip_sync(self, story_id: str) -> dict[str, Any]:
        """④真口型：逐**说话镜**用其对白音频给渲染片重绘口型（LatentSync 本地，$0 API）。

        成功 → 该镜视频替换成对口型版（进 Post）；失败 → 记 failed（**未同步不得过说话镜**，
        交③熔断隔离）。**诚实**：只有真跑成功才 lip_synced=True；无真实后端 → 直通不动画面。
        在 run_audio_mux 之前调（音频=时长真源已定，口型贴音频）。
        """
        from audio.lipsync import resolve_lipsync
        lip = resolve_lipsync(self.lipsync_backend)
        s = self._get(story_id)
        b = self._bundles.get(story_id)
        contracts = self.contracts_for(story_id)
        if not getattr(lip, "real", False) or b is None:
            self._lip_sync[story_id] = {
                "lip_synced": False, "backend": getattr(lip, "name", "passthrough"),
                "synced": [], "failed": [], "skipped": [c["shot_id"] for c in contracts],
                "note": "未接真实唇形模型，嘴型未对齐（诚实占位）"}
            self._snapshot(s)
            return {"story_id": story_id, **self._lip_sync[story_id]}
        synced: list[str] = []
        failed: list[dict[str, Any]] = []
        skipped: list[str] = []
        for c in contracts:
            sid = c["shot_id"]
            rr = c.get("render_result") or {}
            rel = rr.get("file_relpath")
            bindings = [x for x in ((c.get("voice_contract") or {}).get("bindings") or [])
                        if x.get("audio_abspath")]
            # 只对**真实生成素材**跑口型：占位/mock 一律跳过（绝不在假片上启 GPU 子进程）
            real_media = rr.get("is_generated_footage") or rr.get("is_real_media")
            if not c.get("dialogue") or not rel or not bindings or not real_media:
                skipped.append(sid)
                continue
            face = b.path_for(rel)
            if not face.is_file():
                skipped.append(sid)
                continue
            audio = self._shot_dialogue_audio(b, sid, bindings)
            dest = b.path_for(f"09_downloads/lipsync/{sid}.mp4")
            res = lip.sync(face_video=face, audio=audio, dest=dest)
            if res.get("lip_synced"):
                c["render_result"]["file_relpath"] = f"09_downloads/lipsync/{sid}.mp4"  # 换对口型版
                c["lip_synced"] = True
                synced.append(sid)
            else:
                failed.append({"shot_id": sid, "error": res.get("error", "")})
        # 诚实：有说话镜成功且无失败才算全片对齐；失败镜绝不冒充
        lip_ok = bool(synced) and not failed
        self._lip_sync[story_id] = {
            "lip_synced": lip_ok, "backend": lip.name, "synced": synced,
            "failed": failed, "skipped": skipped,
            "note": (f"LatentSync 对口型 {len(synced)} 镜"
                     + (f"；{len(failed)} 说话镜对齐失败，不得进片（③熔断）" if failed else ""))}
        s.log_event("LIP_SYNC", backend=lip.name, synced=len(synced),
                    failed=len(failed), skipped=len(skipped))
        if b is not None:
            b.write_json("11_review/lip_sync.json", self._lip_sync[story_id])
        self._snapshot(s)
        return {"story_id": story_id, **self._lip_sync[story_id]}

    def _lip_failed_ids(self, story_id: str) -> set[str]:
        """对口型失败的说话镜 shot_id（未同步不得过说话镜 → 从 Post 剔除）。"""
        st = self._lip_sync.get(story_id) or {}
        return {f["shot_id"] for f in (st.get("failed") or [])}

    def run_audio_mux(self, story_id: str, *,
                      video_relpath: str = "10_outputs/rough_cut.mp4",
                      bgm: bool = True, ambient: bool = True) -> dict[str, Any]:
        """真实音画合成：把对白/BGM/环境音/字幕合进底片，出最终 MP4。

        需配 audio_mixer（ffmpeg）+ 底片视频已导出 + 已出真实音频。缺任一则跳过。
        """
        if self.audio_mixer is None:
            return {"story_id": story_id, "ok": False, "error": "未配 audio_mixer"}
        s = self._get(story_id)
        contracts = self.contracts_for(story_id)
        svc = AudioDNAService(self._bundles.get(story_id),
                              tts_backend=self.audio_backend)
        res = svc.compose_final(
            contracts, story_id=story_id, video_relpath=video_relpath,
            mixer=self.audio_mixer, bgm=bgm,
            ambience="room" if ambient else "none")
        # 唇形状态**只由 run_lip_sync() 真跑后记录**（绝不因"有真实后端"就冒充 True）。
        # 未跑口型 → 诚实占位（嘴型未对齐）。
        lip_state = self._lip_sync.get(story_id) or {
            "lip_synced": False, "backend": "passthrough",
            "note": "未跑口型对齐（run_lip_sync 未执行），嘴型未真正对齐"}
        self._lip_sync[story_id] = lip_state
        res["lip_synced"] = bool(lip_state.get("lip_synced"))
        s.log_event("AUDIO_MUX", ok=res.get("ok"),
                    clips=res.get("dialogue_clips"),
                    lip_synced=res["lip_synced"], error=res.get("error"))
        self._snapshot(s)
        return {"story_id": story_id, **res}

    #: ShowQuality 打回目标 → 修复码（精准打回 Director/Performance/Render）
    _SHOWQ_REPAIR = {"DIRECTING": "DIRECTOR_FIX",
                     "PERFORMANCE": "PERFORMANCE_FIX",
                     "RENDERING": "RENDER_RETRY"}

    def run_show_quality(self, story_id: str) -> dict[str, Any]:
        """成片观感审核（QA 后、成片总审前）：像短剧而非素材拼接。

        不通过 → 精准打回 Director/Performance/Render 自动重做，不停机等人。
        """
        from showquality.service import ShowQualityDNAService
        s = self._get(story_id)
        contracts = self.contracts_for(story_id)
        if not contracts:
            return {"story_id": story_id, "state": s.state, "verdict": "PASS"}
        cut = None
        b = self._bundles.get(story_id)
        if b is not None and b.exists("10_outputs/rough_cut.json"):
            import json
            cut = json.loads(
                b.path_for("10_outputs/rough_cut.json").read_text("utf-8"))
        result = ShowQualityDNAService(b).review(
            contracts, story_id=story_id, cut=cut)
        self._showquality[story_id] = result
        s.showquality_summary = {"verdict": result.verdict, "score": result.score,
                                 "issues": len(result.issues),
                                 "target": result.target_stage}
        s.log_event("SHOW_QUALITY", verdict=result.verdict, score=result.score,
                    issues=len(result.issues), target=result.target_stage)
        if not result.passed:
            code = self._SHOWQ_REPAIR.get(result.target_stage, "DIRECTOR_FIX")
            self._enter_auto_repair(
                s, code, plan_target=result.target_stage,
                detail="观感未过：" + "; ".join(
                    i["message"] for i in result.issues[:5]))
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state,
                "verdict": result.verdict, "report": result.report()}

    def showquality_for(self, story_id: str):
        return self._showquality.get(story_id)

    def run_narrative_qa(self, story_id: str) -> dict[str, Any]:
        """叙事连贯硬门（DIRECTING 阶段、渲染前）：故事不连贯不得继续。

        逐条查：每镜 story_function/因果完整、对白属主锁死、全片不重复台词、
        情绪不冲突、无静态凑秒、跨场因果相连。不过 → NARRATIVE_FIX 自动打回
        重排/重挂叙事；修不动升级成片人工终确认。诚实：不通过绝不放行到渲染。
        """
        from narrative.continuity_qa import NarrativeContinuityQAService
        s = self._get(story_id)
        contracts = self.contracts_for(story_id)
        if not contracts:
            return {"story_id": story_id, "state": s.state, "verdict": "PASS"}
        result = NarrativeContinuityQAService(self._bundles.get(story_id)).review(
            contracts, story_id=story_id)
        self._narrative_qa[story_id] = result
        # 只有**真作者化**的叙事（LLM 写了 cause/effect）才硬闸——模板内容没有可判的
        # 叙事结构，此时降为 advisory（仅记录，不阻塞机械流），生产走 LLM 即硬闸。
        authored = any((c.get("narrative") or {}).get("authored_by") == "llm"
                       for c in contracts)
        s.log_event("NARRATIVE_QA", verdict=result.verdict,
                    issues=len(result.issues), enforced=authored,
                    by_category=result.report()["by_category"])
        if not result.passed and authored:
            self._enter_auto_repair(
                s, "NARRATIVE_FIX",
                detail="叙事连贯未过：" + "; ".join(
                    i["message"] for i in result.issues[:6]))
            self._snapshot(s)
            return {"story_id": story_id, "state": s.state,
                    "verdict": result.verdict, "report": result.report()}

        # 戏剧信息密度硬门（紧凑度）：8–14 短镜、每 2–4s 一个新发展、结构节拍齐全、
        # 结尾反转。同样只对**真作者化**内容硬闸（模板无节拍结构 → advisory）。
        from narrative.density import DensityQAService
        dres = DensityQAService(self._bundles.get(story_id)).review(
            contracts, story_id=story_id)
        self._density_qa[story_id] = dres
        s.log_event("DENSITY_QA", verdict=dres.verdict, enforced=authored,
                    metrics=dres.metrics, issues=len(dres.issues))
        if not dres.passed and authored:
            self._enter_auto_repair(
                s, "NARRATIVE_FIX",
                detail="密度不足：" + "; ".join(
                    i["message"] for i in dres.issues[:6]))
            self._snapshot(s)
            return {"story_id": story_id, "state": s.state,
                    "verdict": result.verdict, "density": dres.verdict,
                    "report": result.report()}

        # 商业短剧 AI Council：**硬闸在 ScriptBrain 剧本层的「作者↔责编」自我改进闭环**
        # （判 REVISE 带意见重写直到过审）。这里只**记录**闭环终稿的评审结论，不再二次
        # 硬闸——否则 re-decompose 改不动责编分，只会死循环打回。终稿仍 REVISE（LLM 责编
        # 天生挑剔、闭环已尽力）则如实标记，交给成片人工终确认看分决定，不阻断生产。
        script = self._shooting_scripts.get(story_id) or {}
        cres = script.get("commercial_council")
        if authored and cres:
            self._council[story_id] = cres
            if self._bundles.get(story_id) is not None:
                self._bundles[story_id].write_json(
                    f"11_review/council/{story_id}.json", cres)
            s.log_event("COMMERCIAL_COUNCIL", verdict=cres.get("verdict"),
                        score=cres.get("score"), enforced=False)

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state,
                "verdict": result.verdict, "density": dres.verdict,
                "report": result.report()}

    def narrative_qa_for(self, story_id: str):
        return self._narrative_qa.get(story_id)

    def density_qa_for(self, story_id: str):
        return self._density_qa.get(story_id)

    def council_for(self, story_id: str):
        return self._council.get(story_id)

    def _audio_repair(self, story_id: str) -> None:
        """R8 真实修复：AudioDNA 就地修好声音/口型（并补缺失音频）+ 重签，重跑 PreRender。"""
        s = self._get(story_id)
        contracts = self.contracts_for(story_id)
        chars = self._characters_by_id(story_id)
        svc = AudioDNAService(self._bundles.get(story_id),
                              tts_backend=self.audio_backend)
        fixed = svc.repair_contracts(contracts, characters_by_id=chars)
        if self.audio_backend is not None:
            svc.synthesize(contracts, characters_by_id=chars, only_missing=True)
        s.log_event("AUDIO_REPAIR_APPLIED", fixed_shots=fixed)
        s.clear_auto_repair(note=f"AudioDNA 修复 {len(fixed)} 个镜头")
        self._prerender_results.pop(story_id, None)   # 合约变了，旧门结论作废

    def run_render(
        self,
        story_id: str,
        *,
        backend: "RenderBackend | None" = None,
        registry: "BackendRegistry | None" = None,
        max_cloud_ratio: float = 0.5,
    ) -> dict[str, Any]:
        """PERFORMANCE → RENDERING：路由 → 参考注入 → 执行 → 一致性检查。

        任何一张合约不合法（未过 Gate / 被篡改 / 缺表演）都会被拒渲，
        故事随即进入 BLOCKED。
        """
        s = self._require_stage(self._get(story_id), StoryStage.PERFORMANCE)
        self._require_not_blocked(s, "进入渲染阶段")
        contracts = self.contracts_for(story_id)
        if not contracts:
            raise OrchestratorError(f"故事 {story_id} 还没有镜头合约")

        # ①②硬门：无导演执行合同（可拍摄事实）/ 未过自检 → 禁止渲染。不过则已进
        # EXEC_FIX 自修复，本次不渲染，交回驱动循环。
        if not self._require_execution_contract_for_render(story_id):
            return {"story_id": story_id, "state": s.state, "blocked": "no_exec_contract"}
        # 分镜任务单硬门：启用任务单体系时，每镜必须有有效主任务单（缺块=禁生成）
        if not self._require_shot_sheets_for_render(story_id):
            return {"story_id": story_id, "state": s.state, "blocked": "no_shot_sheet"}

        # PDF 铁律：没过 PreRender Commercial Gate 禁止渲染。未跑过就先跑一次。
        pre = self._prerender_results.get(story_id)
        if pre is None:
            self.run_prerender_gate(story_id)
            pre = self._prerender_results.get(story_id)
        if pre is None or not pre.ready:
            raise InvalidStageTransition(
                f"故事 {story_id} 未过 PreRender Commercial Gate，禁止渲染"
                f"（先修复 blockers）")

        # 首帧素材闸(fail-fast)：付费 Kling 前先用便宜首帧验主角锁脸。不过 → 不烧
        # Kling 钱,本次不渲染,交回驱动循环(已升级人工/自修)。首帧落盘缓存,过闸后
        # render_all 复用不重复付费。这是 $15 教训的根治：把身份检查从渲染后前移到首帧。
        ff_gate = self.run_first_frame_gate(story_id, backend=backend, registry=registry)
        if not ff_gate.get("ready", True):
            return {"story_id": story_id, "state": self._get(story_id).state,
                    "blocked": "first_frame_identity", "report": ff_gate.get("report")}

        svc = RenderBrainService(
            None if registry is not None else backend,
            registry=registry,
            bundle=self._bundles.get(story_id),
            max_cloud_ratio=max_cloud_ratio,
        )
        result = svc.render_all(contracts)
        self._render_results[story_id] = result

        b = self._bundles.get(story_id)
        if b is not None:
            for rel, payload in result.artifacts().items():
                b.write_json(rel, payload)

        s.render_summary = result.summary()
        s.advance_to(StoryStage.RENDERING)
        s.log_event("SHOTS_RENDERED", **result.summary())
        if not result.ok:
            reasons = (
                [f"拒渲 {r['shot_id']}: {r['reason']}" for r in result.rejected]
                + [f"渲染失败 {f['shot_id']}: {f['reason']}" for f in result.failed]
                + list(result.consistency_report.get("issues") or [])
            )
            detail = "; ".join(reasons)[:800]
            # 合约非法/被篡改 → 重签；后端失败/一致性 → 按 RetryPolicy 重试降级
            code = "RENDER_RESIGN" if result.rejected else "RENDER_RETRY"
            self._enter_auto_repair(s, code, detail=detail)

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "summary": result.summary(),
                "result": result}

    def run_qa(self, story_id: str) -> dict[str, Any]:
        """镜头级 QA（在 RENDERING 阶段执行，粗剪之前）。

        - PASS   → 什么都不做，可以继续粗剪
        - REPAIR → 阻塞故事并附上修复计划，调 `apply_repair()` 精准打回
        - HUMAN  → 挂人工（存在 critical 级问题，机器判断不了）

        注意：这是**镜头级**质检。全片总审（QA Critic 汇总）尚未实现。
        """
        s = self._require_stage(self._get(story_id), StoryStage.RENDERING)
        contracts = self.contracts_for(story_id)
        if not contracts:
            raise OrchestratorError(f"故事 {story_id} 没有镜头合约")

        svc = ShotQAService(self._bundles.get(story_id))
        result = svc.run(
            contracts,
            story_id=s.story_id,
            continuity_report=(self._director_reports.get(story_id) or {}),
        )

        b = self._bundles.get(story_id)
        if b is not None:
            for rel, payload in result.artifacts().items():
                b.write_json(rel, payload)

        self._qa_results[story_id] = result
        s.qa_summary = result.summary()
        s.log_event("SHOT_QA_DONE", verdict=result.verdict,
                    blocking=result.global_report["blocking_issues"])

        # 镜头级 QA 未过 → 自动精准打回重做（REPAIR 与 HUMAN 都先自动修，
        # 修不动才由 QA_REPAIR 的升级策略转成片总审人工点）。不再停厂等人。
        if result.verdict in (VERDICT_REPAIR, VERDICT_HUMAN):
            self._enter_auto_repair(
                s, "QA_REPAIR",
                detail=result.repair_plan["reason"],
                plan_target=result.repair_plan.get("target_stage"))

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state,
                "verdict": result.verdict, "summary": result.summary(),
                "repair_plan": result.repair_plan}

    def apply_repair(self, story_id: str) -> dict[str, Any]:
        """按 Repair Planner 的结论精准打回（只回退最小必要模块）。"""
        s = self._get(story_id)
        result = self._qa_results.get(story_id)
        if result is None:
            raise OrchestratorError(f"故事 {story_id} 还没跑过 QA")
        plan = result.repair_plan
        if not plan["target_stage"]:
            raise OrchestratorError(f"故事 {story_id} 的 QA 没有给出打回目标")

        if s.status is StoryStatus.BLOCKED:
            s.unblock("按修复计划打回")
        elif s.status is StoryStatus.WAITING_HUMAN:
            raise InvalidStageTransition(
                f"故事 {story_id} 卡在人工闸门，需先 release_hold 再打回"
            )

        s.repair_to(StoryStage(plan["target_stage"]), reason=plan["reason"])
        s.log_event("REPAIR_APPLIED", target=plan["target_stage"],
                    shots=plan["shots_to_redo"])
        self._snapshot(s)
        return {"story_id": story_id, "state": s.state,
                "target_stage": plan["target_stage"],
                "shots_to_redo": plan["shots_to_redo"]}

    def assemble_rough_cut(
        self, story_id: str, *, export_media: bool = False, export_video: bool = True
    ) -> dict[str, Any]:
        """RENDERING → QA：把镜头排成粗剪时间线。

        `export_media=True` 时额外导出 EDL / WebVTT / 动态分镜 HTML，
        并在素材齐全时用 ffmpeg 拼出真实 MP4。
        """
        s = self._require_stage(self._get(story_id), StoryStage.RENDERING)
        self._require_not_blocked(s, "进入粗剪")
        contracts = self.contracts_for(story_id)
        script = self._shooting_scripts.get(story_id) or {}
        b = self._bundles.get(story_id)

        # ③ Vision 熔断：逐镜质检，坏镜隔离——**绝不进 Post**（真实素材才判，占位不误杀）。
        # 好镜照常出片＝优雅降级，不因个别坏镜整片 FAILED。
        exclude: set[str] = set()
        rr = self._render_results.get(story_id)
        if rr is not None and getattr(rr, "rendered", None):
            from render.vision_quarantine import VisionQuarantine, SemanticCheck
            from final_review.vision_judge import VisionJudge
            # ②语义 VisionQA：judge 每镜是否符合意图 + 无不当内容(接吻/亲密)→ 熔断
            #（防线,抓"演错/语义灾难";需 enable_vision_llm）
            extra = None
            if self.vision_llm is not None:
                intents = {c["shot_id"]: self._shot_intent(c)
                           for c in self.contracts_for(story_id)}
                extra = SemanticCheck(self.vision_llm, intents, bundle=b)
            # LLM 视觉深判(变脸/塑料感/乱码) + 语义(演错/不当内容) 一起熔断
            q = VisionQuarantine(
                judge=VisionJudge(llm_backend=self.vision_llm),
                extra_check=extra).screen(rr.rendered, bundle=b)
            self._quarantine[story_id] = q
            if b is not None:
                b.write_json("11_review/quarantine.json", q.report())
            if q.quarantined:
                exclude = set(q.quarantined_ids)
                s.log_event("VISION_QUARANTINE", quarantined=len(q.quarantined),
                            passed=len(q.passed), ids=q.quarantined_ids[:8])
        # ④硬规则：对口型失败的说话镜也不得进 Post（未同步不得过说话镜）
        lip_failed = self._lip_failed_ids(story_id)
        if lip_failed:
            exclude |= lip_failed
            s.log_event("LIP_SYNC_EXCLUDE", ids=sorted(lip_failed)[:8])

        cut = assemble_rough_cut(
            contracts,
            story_id=s.story_id,
            bundle_id=s.bundle_id,
            title=script.get("title", s.title),
            exclude_shot_ids=exclude,
        )
        if export_media:
            export_cut(cut, b, video=export_video)
        if b is not None:
            b.write_json("10_outputs/rough_cut.json", cut)

        s.cut_summary = {
            "shot_count": cut["shot_count"],
            "total_duration_sec": cut["total_duration_sec"],
            "duration_in_target": cut["duration_in_target"],
            "media_ready": cut["media_ready"],
            "is_generated_footage": cut["is_generated_footage"],
            "exports": {
                k: (v.get("relpath") if isinstance(v, dict) else v)
                for k, v in (cut.get("exports") or {}).items()
            },
        }
        s.advance_to(StoryStage.QA)
        s.log_event("ROUGH_CUT_ASSEMBLED", **s.cut_summary)
        self._snapshot(s)
        return cut

    # ------------------------------------------------------------------
    # 3c. 成片级 Final Review（QA Council 5 维总审）
    # ------------------------------------------------------------------

    def _safety_violations(self, story_id: str) -> list[str]:
        """成片级安全合规复查：把各资产 Gate 的红线/未授权/品牌违规汇总。"""
        out: list[str] = []
        for wo in self.store.list_workorders(story_id=story_id):
            gr = wo.gate_report or {}
            for b in gr.get("hard_blocks") or []:
                out.append(f"{wo.workorder_id}: 红线 {b}")
        for rec in self.store.scene_atoms.values():
            if rec.get("story_id") == story_id and rec.get("rights_risk", 0) > 0.30:
                out.append(f"场景 {rec.get('asset_id')}: 版权风险过高")
        return out

    def _final_review_ctx(self, story_id: str) -> dict[str, Any]:
        """从现有报告拼出 Final Review 的输入——**全部复用，零重算**。"""
        b = self._bundles.get(story_id)
        cut = {}
        if b is not None and b.exists("10_outputs/rough_cut.json"):
            import json
            cut = json.loads(
                b.path_for("10_outputs/rough_cut.json").read_text("utf-8"))
        rr = self._render_results.get(story_id)
        qa = self._qa_results.get(story_id)
        # 黑屏/缺帧 + 视觉判断：对真实成片视频跑 ffmpeg（有视频且有 ffmpeg 才做）
        media_probe = None
        vision_judge = None
        for rel in ("10_outputs/drama_audio_final.mp4",
                    f"10_outputs/{story_id}_final.mp4", "10_outputs/rough_cut.mp4"):
            if b is not None and b.exists(rel):
                video = b.path_for(rel)
                try:
                    from final_review.media_probe import MediaProbe
                    mp = MediaProbe()
                    if mp.available():
                        media_probe = mp.probe(video).to_dict()
                except Exception:
                    media_probe = None
                try:
                    from final_review.vision_judge import VisionJudge
                    vj = VisionJudge(llm_backend=self.vision_llm)
                    if vj.available():
                        vision_judge = vj.judge(video).to_dict()
                except Exception:
                    vision_judge = None
                break
        # 跨镜真实身份一致性：只对真实生成素材做（每镜抽帧比对人脸区域签名）
        identity_consistency = None
        if (b is not None and rr is not None and rr.rendered
                and all(r.get("is_generated_footage") for r in rr.rendered)):
            try:
                from identity.consistency import IdentityConsistencyService
                cmap = {c["shot_id"]: c for c in self.contracts_for(story_id)}
                items = []
                for r in rr.rendered:
                    rel = r.get("file_relpath")
                    if not rel:
                        continue
                    c = cmap.get(r["shot_id"], {})
                    dlg = c.get("dialogue") or []
                    cid = (dlg[0]["character_id"] if dlg else
                           (c.get("assets", {}).get("characters") or [{}])[0]
                           .get("character_id", "?"))
                    items.append({"shot_id": r["shot_id"], "video": b.path_for(rel),
                                  "character_id": cid,
                                  "duration_sec": c.get("duration_sec", 3)})
                if items:
                    identity_consistency = self._identity_service(b).review(
                        items, story_id=story_id).report()
            except Exception:  # noqa: BLE001 —— 比对失败不挡总审
                identity_consistency = None
        # ②可验证同脸（结构级）：各镜身份指纹的参考锚是否一致（像素级真判脸见⑤）
        reference_consistency = None
        if rr is not None and rr.rendered:
            fps = [r.get("identity_reference") for r in rr.rendered
                   if r.get("identity_reference")]
            if fps:
                from render.reference_lock import verify_reference_consistency
                reference_consistency = verify_reference_consistency(fps)
        # 镜头功能表达：动作类功能镜是否演出来（近乎静止=没表达）
        function_expression = None
        if (b is not None and rr is not None and rr.rendered
                and all(r.get("is_generated_footage") for r in rr.rendered)):
            try:
                from final_review.function_expression import FunctionExpressionService
                cmap2 = {c["shot_id"]: c for c in self.contracts_for(story_id)}
                fitems = [{"shot_id": r["shot_id"],
                           "video": b.path_for(r["file_relpath"]),
                           "story_function": cmap2.get(r["shot_id"], {}).get(
                               "story_function", "")}
                          for r in rr.rendered if r.get("file_relpath")]
                function_expression = FunctionExpressionService().review(fitems).report()
            except Exception:  # noqa: BLE001
                function_expression = None
        return {
            "contracts": self.contracts_for(story_id),
            "shooting_script": self._shooting_scripts.get(story_id) or {},
            "continuity_report": self._director_reports.get(story_id) or {},
            "consistency_report": rr.consistency_report if rr else {},
            "identity_consistency": identity_consistency,
            "reference_consistency": reference_consistency,
            "function_expression": function_expression,
            "shot_qa": {
                "vision": qa.vision, "performance": qa.performance,
                "continuity": qa.continuity,
            } if qa else {},
            "rough_cut": cut,
            "media_probe": media_probe,
            "vision_judge": vision_judge,
            "lip_sync": self._lip_sync.get(story_id),
            "safety_violations": self._safety_violations(story_id),
        }

    def run_final_review(self, story_id: str) -> dict[str, Any]:
        """POST → FINAL_REVIEW：QA Council 5 维总审。

        - PASS_FULL / PASS_STRUCTURAL → 停在 FINAL_REVIEW，可 publish
        - REJECTED  → 阻塞 + 按维度精准打回
        - HUMAN     → 挂人工（存在 critical，机器判不了）
        """
        s = self._get(story_id)
        if s.stage is StoryStage.POST:
            s.advance_to(StoryStage.FINAL_REVIEW)
        self._require_stage(s, StoryStage.FINAL_REVIEW)
        self._require_not_blocked(s, "进入成片总审")

        svc = FinalReviewService(self._bundles.get(story_id))
        review = svc.run(self._final_review_ctx(story_id), story_id=s.story_id)

        self._final_reviews[story_id] = review
        s.final_review_summary = review.summary()
        s.log_event("FINAL_REVIEW_DONE", verdict=review.verdict,
                    blocking=len(review.blocking_issues), pending=len(review.pending))

        if review.verdict == VERDICT_FR_HUMAN:
            # 成片总审 critical → 人工点 3（发不发，机器不拍板）
            self._human_hold(s, repair_policy.FINAL_CUT_REVIEW,
                             gate_ids=[s.story_id],
                             note=review.repair_plan.get("reason", "")[:400])
        elif review.verdict == VERDICT_FR_REJECTED:
            # 驳回但可机修 → 按维度精准打回自动重做；修不动才升级人工点 3
            self._enter_auto_repair(
                s, "FINAL_REPAIR",
                detail=review.repair_plan.get("reason", ""),
                plan_target=review.repair_plan.get("target_stage"))
        elif review.verdict == VERDICT_FR_PASS_STRUCTURAL:
            # 结构达标但商业清单未过 → 精准打回失败项对应模块（自动修复优先），
            # 修不动才升级成片人工终确认（COMMERCIAL_FIX 升级=final_review_critical）
            self._enter_auto_repair(
                s, "COMMERCIAL_FIX",
                plan_target=review.repair_plan.get("target_stage"),
                detail=review.repair_plan.get("reason", "")[:400])
        # PASS_FULL → 停在 FINAL_REVIEW（RUNNING/静止），等人工终确认后发布

        self._snapshot(s)
        return {"story_id": story_id, "state": s.state, "verdict": review.verdict,
                "summary": review.summary(), "report": review.report()}

    def final_review_for(self, story_id: str):
        return self._final_reviews.get(story_id)

    # ------------------------------------------------------------------
    # 3d. 自修复引擎 + 工厂自驱动（Phase D）
    #
    # 理念：除 3 个人工闸门（剧本/人脸/成片）外，一切机器 Gate 失败都是
    # **自修复触发器**——自动打回对应模块重做，不停厂、不等人。红线永不自动绕。
    # ------------------------------------------------------------------

    def _human_hold(self, s: Story, reason: str, *, gate_ids=None, note: str = "") -> None:
        """挂人工——**白名单强制**：只允许 3 个人工点，杜绝第 4 个偷偷出现。"""
        if reason not in repair_policy.HUMAN_GATES:
            raise OrchestratorError(
                f"非法人工闸门 {reason!r}；只允许 {sorted(repair_policy.HUMAN_GATES)}")
        s.hold_for_human(reason, gate_ids=gate_ids, note=note)

    def _enter_auto_repair(self, s: Story, code: str, *, detail: str = "",
                           plan_target: str | None = None) -> None:
        """把一次机器判负转成自修复态（而非裸 BLOCKED）。

        同一个故事对同一 code 的重试次数累计在 directive.attempt 里；超过上限
        由驱动器升级。红线（IDENTITY_REDLINE, max=0）会在驱动时立即升级人脸审核。
        """
        action = repair_policy.POLICY[code]
        counts = self._repair_attempts.setdefault(s.story_id, {})
        counts[code] = counts.get(code, 0) + 1
        attempt = counts[code]
        if attempt == 1:   # 新修复回合（供 OptimizerDNA 统计恢复率）
            self._repair_stats.setdefault(code, {"episodes": 0, "exhausted": 0})
            self._repair_stats[code]["episodes"] += 1
        # 重试上限：OptimizerDNA 学到的自适应覆盖优先，否则用出厂默认
        cap = self.tuning.cap_for(code, action.max_attempts)
        s.enter_auto_repair({
            "code": code,
            "agent": action.agent,
            "target_stage": plan_target or action.target_stage,
            "attempt": attempt,
            "max_attempts": cap,
            "escalation": action.escalation,
            "detail": detail[:400],
            "note": action.note,
        })
        self._snapshot(s)

    def _rollback(self, s: Story, stage: StoryStage, *, reason: str) -> None:
        """驱动器授权的阶段回滚（比 repair_to 更宽，可回到非 REPAIR_TARGET 前驱）。"""
        s.stage = stage
        s.status = StoryStatus.RUNNING
        s.blocked_reason = None
        s.log_event("AUTO_REPAIR_ROLLBACK", to=stage.value, reason=reason[:200])

    def _escalate(self, s: Story, escalation: str, reason: str) -> None:
        """自修复耗尽后的去向：人工点 / 判失败 / 降级继续。"""
        if escalation == repair_policy.ESC_FAIL:
            s.repair_directive = None
            s.fail(reason[:400])
        elif escalation == repair_policy.ESC_DEGRADE:
            # 场景/道具真实图始终出不来 → 接受降级占位，继续流转（不停厂）
            s.log_event("AUTO_REPAIR_DEGRADE", reason=reason[:200])
            s.clear_auto_repair(note="降级占位继续")
            if s.stage in (StoryStage.SCRIPT_DONE, StoryStage.ASSET_MATCHING):
                self._rollback(s, StoryStage.SCRIPT_DONE, reason="degrade→重走资产分发")
        else:
            # 升级到 3 个人工点之一
            s.repair_directive = None
            self._human_hold(s, escalation, gate_ids=[s.story_id],
                             note=f"自修复耗尽，转人工：{reason}"[:400])
        self._snapshot(s)

    def _clear_stale_directive(self, s: Story) -> None:
        """前向推进成功（仍 RUNNING）后，清掉上一次修复的残留指令（计数另存不受影响）。"""
        if s.status is StoryStatus.RUNNING and s.repair_directive is not None:
            s.repair_directive = None

    def _execute_repair(self, story_id: str) -> None:
        """执行一次自修复：回滚到目标阶段前驱，由前向驱动重做该阶段。

        超过重试上限则升级。真正的"重生成"发生在回滚后前向驱动重跑对应 run_*。
        """
        s = self._get(story_id)
        d = s.repair_directive or {}
        code = d.get("code")
        if d.get("attempt", 1) > d.get("max_attempts", 0):
            # 上限耗尽：记一次"未在带内修好"（OptimizerDNA 据此调该码上限）
            self._repair_stats.setdefault(code, {"episodes": 0, "exhausted": 0})
            self._repair_stats[code]["exhausted"] += 1
            self._escalate(s, d.get("escalation", repair_policy.ESC_FAIL),
                           d.get("detail") or code or "")
            return
        # R8：声音/口型是真实修复动作（就地改合约），不是回滚重跑
        if code == "AUDIO_FIX":
            self._audio_repair(story_id)
            return
        target = d.get("target_stage")
        if not target:
            # 无明确目标（如 QA/Final 未给 target）→ 直接按升级处理
            self._escalate(s, d.get("escalation", repair_policy.ESC_FINAL),
                           d.get("detail") or code or "")
            return
        target_stage = StoryStage(target)
        pred = _REDO_PREDECESSOR.get(target_stage, target_stage)
        s.log_event("AUTO_REPAIR_EXEC", code=code, target=target,
                    attempt=d.get("attempt"))
        self._rollback(s, pred, reason=f"{code}: 重做 {target}")

    #: 到达这些状态/阶段，工厂对该故事就"静止"了（等人工或已终态）
    def _is_quiescent(self, s: Story) -> bool:
        if s.is_terminal or s.status is StoryStatus.WAITING_HUMAN:
            return True
        # 成片总审通过后停在 FINAL_REVIEW，等人工决定发布（人工点 3）
        if s.stage is StoryStage.FINAL_REVIEW and s.status is StoryStatus.RUNNING:
            return True
        # 剧本阶段需要外部输入（brief / 人工放行），驱动器不接管
        if s.stage in (StoryStage.CREATED, StoryStage.SCRIPT_RUNNING):
            return True
        return False

    def _advance_one(self, story_id: str, *, export_media: bool = False) -> None:
        """前向驱动一步：按当前阶段跑对应 run_*，把故事往成片推进一格。"""
        s = self._get(story_id)
        stage = s.stage
        if stage is StoryStage.SCRIPT_DONE:
            self.run_director_planning(story_id)     # 总导演出总谱（宪法层上游）
        elif stage is StoryStage.DIRECTOR_PLANNING:
            self.run_director_qa(story_id)           # 验收官签发+锁定
        elif stage is StoryStage.DIRECTOR_QA:
            self.dispatch_assets(story_id)           # 发单门后并行资产生产
        elif stage is StoryStage.ASSET_READY:
            self.run_director(story_id)              # 总谱镜头化=下游只执行
        elif stage is StoryStage.DIRECTING:
            # 叙事连贯硬门（渲染前跑，省预算）：不过则自动打回重排/重挂叙事
            self.run_narrative_qa(story_id)
            if self._get(story_id).status is StoryStatus.RUNNING:
                self.run_performance(story_id)
        elif stage is StoryStage.PERFORMANCE:
            # 声音专业审核（Audio）先于渲染前总闸
            self.run_audio_review(story_id)
            if self._get(story_id).status is StoryStatus.RUNNING:
                self.run_prerender_gate(story_id)
            if self._get(story_id).status is StoryStatus.RUNNING:
                self.run_render(story_id, registry=self.render_registry)
        elif stage is StoryStage.RENDERING:
            self.run_qa(story_id)
            # ④真口型：装配前逐说话镜对齐（音频=时长真源已定，口型贴音频）。
            # 替换成对口型版进 Post；对齐失败镜并入排除集（未同步不得过说话镜）。
            if self._get(story_id).status is StoryStatus.RUNNING:
                try:
                    self.run_lip_sync(story_id)
                except Exception:  # noqa: BLE001 —— 口型失败不挡出片(诚实记未同步)
                    pass
            if self._get(story_id).status is StoryStatus.RUNNING:
                self.assemble_rough_cut(story_id, export_media=export_media)
        elif stage is StoryStage.QA:
            # 成片观感审核（像短剧而非素材拼接），过了才进成片总审
            self.run_show_quality(story_id)
            if self._get(story_id).status is StoryStatus.RUNNING:
                self.advance(story_id, StoryStage.POST)
        elif stage is StoryStage.POST:
            # 配了混流器 → 先出带声成片（记录唇形状态），再进成片总审
            if self.audio_mixer is not None:
                try:
                    self.run_audio_mux(story_id)
                except Exception:  # noqa: BLE001 —— 混流失败不挡总审
                    pass
            self.run_final_review(story_id)
        # 其它阶段（FINAL_REVIEW/终态/剧本）由 _is_quiescent 拦住，不到这里

    def drive(self, story_id: str, *, export_media: bool = False,
              max_steps: int = 60) -> dict[str, Any]:
        """把**单个**故事自动驱动到静止（人工点 / 成片待发布 / 终态）。

        - RUNNING：前向推进一格
        - AUTO_REPAIRING：执行一次自修复（回滚重做 or 升级）
        每步都会重新判定，直到静止或步数用尽（步数上限是防呆，不是正常出口）。
        """
        for _ in range(max_steps):
            s = self._get(story_id)
            if self._is_quiescent(s):
                try:
                    self.gate_chain_report(story_id)   # 静止即落逐级把关溯源报告
                except Exception:  # noqa: BLE001 溯源报告失败不影响生产
                    pass
                break
            if s.status is StoryStatus.AUTO_REPAIRING:
                self._execute_repair(story_id)
            elif s.status is StoryStatus.RUNNING:
                self._advance_one(story_id, export_media=export_media)
                self._clear_stale_directive(self._get(story_id))
            else:  # BLOCKED 瞬时态：兜底不至于死循环
                break
        s = self._get(story_id)
        return {"story_id": story_id, "state": s.state, "stage": s.stage.value,
                "status": s.status.value}

    def run_until_quiescent(self, *, export_media: bool = False,
                            max_rounds: int = 200) -> dict[str, Any]:
        """**工厂主循环**：驱动所有故事直到全部静止。

        多故事并行：每一轮扫一遍所有未静止的故事各推进一步，直到没有可动的。
        WebUI / CLI 只需调这一个方法，无需手工点"继续/重试/打回"。
        """
        rounds = 0
        while rounds < max_rounds:
            rounds += 1
            moved = False
            for sid in list(self._stories.keys()):
                s = self._stories[sid]
                if self._is_quiescent(s):
                    continue
                if s.status is StoryStatus.AUTO_REPAIRING:
                    self._execute_repair(sid); moved = True
                elif s.status is StoryStatus.RUNNING:
                    self._advance_one(sid, export_media=export_media)
                    self._clear_stale_directive(self._stories[sid]); moved = True
                elif s.status is StoryStatus.BLOCKED:
                    moved = True  # 兜底：让下一轮的逻辑处理
            if not moved:
                break
        return {"rounds": rounds, "factory": self.factory_status()}

    # ------------------------------------------------------------------
    # 4. 通用推进 / 人工闸门 / 打回（State + Human Gate Manager）
    # ------------------------------------------------------------------

    def advance(self, story_id: str, stage: StoryStage | str, *, note: str = "") -> Story:
        """通用阶段推进（下游 Director / Render / QA 等模块上报用）。"""
        s = self._get(story_id).advance_to(StoryStage(stage), note=note)
        self._snapshot(s)
        return s

    def hold_for_human(
        self, story_id: str, reason: str, *, gate_ids: list[str] | None = None,
        note: str = "",
    ) -> Story:
        s = self._get(story_id).hold_for_human(reason, gate_ids=gate_ids, note=note)
        self._snapshot(s)
        return s

    def record_human_note(
        self, story_id: str, *, note: str, decided_by: str, context: str = ""
    ) -> Story:
        """把人工意见记进故事事件流（Web 审核界面填的意见走这里）。

        意见必须落在事件流里而不是只显示在页面上，否则事后无从追责。
        """
        s = self._get(story_id)
        if note or context:
            s.log_event("HUMAN_NOTE", note=note, decided_by=decided_by,
                        context=context)
            self._snapshot(s)
        return s

    def release_hold(self, story_id: str, *, decided_by: str, note: str = "") -> Story:
        s = self._get(story_id).release_hold(decided_by=decided_by, note=note)
        self._snapshot(s)
        return s

    def request_repair(
        self, story_id: str, target_stage: StoryStage | str, *, reason: str
    ) -> Story:
        """Repair Planner 精准打回（只回退必要模块，其它故事不受影响）。"""
        s = self._get(story_id).repair_to(StoryStage(target_stage), reason=reason)
        self._snapshot(s)
        return s

    def publish_story(self, story_id: str, *, decided_by: str = "human") -> Story:
        """人工终确认发布：**只有 PASS_FULL（商业可发布）才允许进入发布包**。

        未达 PASS_FULL 一律拒绝——结构达标(PASS_STRUCTURAL)也不行。
        """
        s = self._get(story_id)
        review = self._final_reviews.get(story_id)
        if review is None:
            raise OrchestratorError(f"故事 {story_id} 还没跑成片总审，不能发布")
        if not review.commercial_ready:
            failed = [c["name"] for c in getattr(review, "failed_checklist", [])]
            raise OrchestratorError(
                f"未达 PASS_FULL（当前 {review.verdict}），不允许进入发布包；"
                f"商业清单未过：{failed}")
        if s.status is StoryStatus.WAITING_HUMAN:
            s.release_hold(decided_by=decided_by, note="成片终确认发布")
        s = s.advance_to(StoryStage.PUBLISHED, note=f"published by {decided_by}")
        self._snapshot(s)
        return s

    def fail_story(self, story_id: str, error: str) -> Story:
        s = self._get(story_id).fail(error)
        self._snapshot(s)
        return s

    def cancel_story(self, story_id: str, reason: str = "") -> Story:
        s = self._get(story_id).cancel(reason)
        self._snapshot(s)
        return s

    def archive_story(self, story_id: str) -> Story:
        """关闭 Bundle：PUBLISHED → ARCHIVED，并落一份最终清单。"""
        s = self._require_stage(self._get(story_id), StoryStage.PUBLISHED)
        s.advance_to(StoryStage.ARCHIVED)
        b = self._bundles.get(story_id)
        if b is not None:
            b.write_json("13_archive/story_final.json", s.to_dict())
        self._snapshot(s)
        return s

    # ------------------------------------------------------------------
    # 5. 查询 / 看板（State Manager + Workflow Planner）
    # ------------------------------------------------------------------

    def get_story(self, story_id: str) -> dict[str, Any]:
        return self._get(story_id).to_dict()

    def list_stories(
        self,
        *,
        stage: StoryStage | str | None = None,
        status: StoryStatus | str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        out: Iterable[Story] = self._stories.values()
        if stage is not None:
            want_stage = StoryStage(stage)
            out = (s for s in out if s.stage is want_stage)
        if status is not None:
            want_status = StoryStatus(status)
            out = (s for s in out if s.status is want_status)
        if state is not None:
            out = (s for s in out if s.state == state)
        return [s.to_dict() for s in out]

    def pending_holds(self) -> list[dict[str, Any]]:
        """Human Gate Manager 的待办队列。"""
        return [
            {"story_id": s.story_id, "stage": s.stage.value, **(s.hold or {})}
            for s in self._stories.values()
            if s.status is StoryStatus.WAITING_HUMAN
        ]

    def next_actionable(self) -> list[dict[str, Any]]:
        """Workflow Planner：此刻真正能被机器推进的故事（按优先级排序）。

        排除终态、人工挂起、机器阻塞；CREATED 只有在有槽位时才算可推进。
        """
        rank = {"critical": 0, "high": 1, "normal": 2}
        has_slot = self.can_start_new_story()
        out = [
            s
            for s in self._stories.values()
            if s.status is StoryStatus.RUNNING
            and not s.is_terminal
            and (s.stage is not StoryStage.CREATED or has_slot)
        ]
        out.sort(key=lambda s: (rank.get(s.priority, 9), s.created_at, s.story_id))
        return [s.to_dict() for s in out]

    def factory_status(self) -> dict[str, Any]:
        """factory_status.json：全厂实时看板数据。"""
        by_stage: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for s in self._stories.values():
            by_stage[s.stage.value] = by_stage.get(s.stage.value, 0) + 1
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
        return {
            "schema_version": schemas.SCHEMA_FACTORY_STATUS,
            "generated_at": schemas.utc_now_iso(),
            "stories_total": len(self._stories),
            "by_stage": by_stage,
            "by_status": by_status,
            "script_slots": {
                "max": self.max_concurrent_scripts,
                "available": self.script_slots_available(),
            },
            "pending_holds": self.pending_holds(),
            "asset_brain": self.store.stats(),
            "stories": [s.to_dict() for s in self._stories.values()],
        }


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _iter_scenes(script: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """兼容两种剧本形态：带 episodes 的完整剧本 / 只有 scenes 的片段。"""
    for ep in script.get("episodes") or []:
        yield from ep.get("scenes") or []
    yield from script.get("scenes") or []


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        v = str(it.get(key))
        out[v] = out.get(v, 0) + 1
    return out
