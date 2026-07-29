"""Web 控制台的操作层 (Phase 5)

把 Orchestrator / AssetBrainFacade / 人工闸门状态机包成"页面能直接用"的操作。
这一层**不新增业务规则**：所有铁规仍在原处执行（红线 Gate、合约校验、
状态机、连续性检查），Web 只是把它们的结论翻译成页面语言。

三件事在这里收敛：
1. **下一步是什么** —— 由 stage/status 推导，页面上就是一个"执行下一步"按钮
2. **人工闸门怎么裁决** —— 三种挂起原因路由到三个不同的真实入口
3. **看板/资产/工单/成片怎么读** —— 只读投影，不改状态
"""

from __future__ import annotations

from typing import Any, Callable

from asset_brain.identity_dna.gate import HardBlockOverrideError
from orchestrator.pipeline import OrchestratorError
from orchestrator.story import StoryStage, StoryStatus
from render.assembly import TARGET_MAX_SEC, TARGET_MIN_SEC

from . import simulations
from .state import FactoryWorkspace

# ---------------------------------------------------------------------------
# 下一步推导
# ---------------------------------------------------------------------------

#: stage → (动作代号, 人话说明)
_NEXT_BY_STAGE: dict[str, tuple[str, str]] = {
    "CREATED": ("scriptbrain", "跑 ScriptBrain 生成拍摄版剧本"),
    "SCRIPT_RUNNING": ("scriptbrain", "跑 ScriptBrain 生成拍摄版剧本"),
    "SCRIPT_DONE": ("assets", "把剧本分发给三大资产模型"),
    "ASSET_READY": ("director", "排镜头并签发 Shot Contract"),
    "DIRECTING": ("performance", "补表演节拍并签发合约"),
    "PERFORMANCE": ("render", "渲染全部镜头"),
    "QA": ("post", "进入后期"),
    "POST": ("final_review", "成片级总审核（QA Council）"),
    "FINAL_REVIEW": ("publish", "通过总审 → 生成发布包"),
    "PUBLISHED": ("archive", "归档并关闭 Bundle"),
}


def next_action_for(story: dict[str, Any]) -> dict[str, Any]:
    """给页面用的"下一步该点什么"。"""
    if story["status"] == StoryStatus.WAITING_HUMAN.value:
        hold = story.get("hold") or {}
        return {
            "action": "review",
            "label": f"等待人工审核（{hold.get('reason', '未知')}）",
            "blocked": True,
        }
    if story["status"] == StoryStatus.AUTO_REPAIRING.value:
        d = story.get("repair_directive") or {}
        # 自修复不是终点、也不需要人点"继续"——页面点一下即自动驱动到静止
        return {
            "action": "auto_repair",
            "label": f"自动修复中：{d.get('code', '?')} → {d.get('agent', '')}",
            "blocked": False,
        }
    if story["status"] == StoryStatus.BLOCKED.value:
        return {
            "action": "repair",
            "label": f"已阻塞：{story.get('blocked_reason') or '未知原因'}",
            "blocked": True,
        }
    stage = story["stage"]
    if stage == "RENDERING":
        if not story.get("qa_summary"):
            return {"action": "qa", "label": "跑镜头级 QA", "blocked": False}
        return {"action": "cut", "label": "拼接粗剪并导出成片", "blocked": False}
    if stage in ("ARCHIVED", "FAILED", "CANCELLED"):
        return {"action": None, "label": "已终态", "blocked": False}
    if stage == "ASSET_MATCHING":
        return {"action": None, "label": "资产阶段处理中", "blocked": False}
    act, label = _NEXT_BY_STAGE.get(stage, (None, "无可执行动作"))
    return {"action": act, "label": label, "blocked": False}


# ---------------------------------------------------------------------------
# 操作
# ---------------------------------------------------------------------------


class FactoryService:
    """页面按钮 → 真实后端调用。"""

    def __init__(self, ws: FactoryWorkspace) -> None:
        self.ws = ws
        self.orc = ws.orc
        #: story_id → 建剧时填的题材/参数（"执行下一步"不再需要额外输入）
        self.briefs: dict[str, dict[str, Any]] = {}
        #: story_id → 模拟场景（默认 none，仅用于在网页上复现审核/阻塞/红线路径）
        self.simulations: dict[str, str] = {}

    # -- 故事 -----------------------------------------------------------

    def create_story(
        self,
        *,
        title: str,
        theme: str,
        priority: str = "normal",
        episode_count: int = 1,
        scenes_per_episode: int = 3,
        backend: str | None = None,
        simulate: str | None = None,
    ) -> dict[str, Any]:
        # 先校验后端与模拟场景，避免建完剧才发现跑不起来
        _, backend_name = self.ws.registry_for(backend)
        if simulate and simulate not in simulations.SIMULATIONS:
            raise ValueError(f"未知模拟场景 {simulate!r}")

        def run() -> dict[str, Any]:
            s = self.orc.create_story(title=title, priority=priority)
            self.briefs[s.story_id] = {
                "theme": theme,
                "episode_count": episode_count,
                "scenes_per_episode": scenes_per_episode,
            }
            self.ws.backend_choice[s.story_id] = backend_name
            self.simulations[s.story_id] = simulate or simulations.SIM_NONE
            return s.to_dict()

        return self.ws.call(run)

    def story_detail(self, story_id: str) -> dict[str, Any]:
        story = self.orc.get_story(story_id)
        story["next"] = next_action_for(story)
        story["brief"] = self.briefs.get(story_id)
        story["backend"] = self.ws.backend_choice.get(story_id)
        story["simulate"] = self.simulations.get(story_id, simulations.SIM_NONE)
        story["shot_count"] = len(self.orc.contracts_for(story_id))
        qa = self.orc._qa_results.get(story_id)
        story["repair_plan"] = qa.repair_plan if qa else None
        return story

    def factory_board(self) -> dict[str, Any]:
        """多故事工厂看板。"""
        status = self.orc.factory_status()
        stories = []
        for s in status["stories"]:
            row = {
                k: s[k]
                for k in (
                    "story_id", "bundle_id", "title", "priority", "stage",
                    "status", "state", "occupies_script_slot", "blocked_reason",
                    "hold", "updated_at",
                )
            }
            row["next"] = next_action_for(s)
            row["backend"] = self.ws.backend_choice.get(s["story_id"])
            row["simulate"] = self.simulations.get(s["story_id"], simulations.SIM_NONE)
            row["shots"] = (s.get("shot_summary") or {}).get("shot_count", 0)
            row["cut"] = s.get("cut_summary") or {}
            row["qa"] = s.get("qa_summary") or {}
            stories.append(row)
        status["stories"] = stories
        status["backends"] = self.ws.available_backends()
        status["target_range_sec"] = [TARGET_MIN_SEC, TARGET_MAX_SEC]
        return status

    # -- 流程推进 --------------------------------------------------------

    def run_step(self, story_id: str, action: str | None = None) -> dict[str, Any]:
        """执行下一步（或指定某一步）。"""
        story = self.orc.get_story(story_id)
        decided = action or (next_action_for(story)["action"])
        if decided is None:
            raise OrchestratorError(
                f"故事 {story_id} 当前（{story['state']}）没有可执行的下一步"
            )

        runners: dict[str, Callable[[], Any]] = {
            "scriptbrain": lambda: self._scriptbrain(story_id),
            "assets": lambda: self._assets(story_id),
            "director": lambda: self.orc.run_director(story_id),
            "performance": lambda: self.orc.run_performance(story_id),
            "render": lambda: self._render(story_id),
            "qa": lambda: self.orc.run_qa(story_id),
            "cut": lambda: self._cut(story_id),
            "post": lambda: self.orc.advance(story_id, StoryStage.POST, note="webui").to_dict(),
            "final_review": lambda: self.orc.run_final_review(story_id),
            "publish": lambda: self._publish(story_id),
            "archive": lambda: self.orc.archive_story(story_id).to_dict(),
            "repair": lambda: self.orc.apply_repair(story_id),
            # 自修复：把这个故事自动驱动到静止（人工点 / 成片待发布 / 终态）
            "auto_repair": lambda: self.orc.drive(story_id, export_media=True),
        }
        if decided not in runners:
            raise ValueError(f"未知动作 {decided!r}，可选 {sorted(runners)}")

        result = self.ws.call(runners[decided])
        return {
            "ran": decided,
            "result": _jsonable(result),
            "story": self.story_detail(story_id),
        }

    def _scriptbrain(self, story_id: str) -> dict[str, Any]:
        brief = self.briefs.get(story_id)
        if not brief:
            raise OrchestratorError(f"故事 {story_id} 没有题材，无法跑 ScriptBrain")
        return self.orc.run_scriptbrain(story_id, brief)

    def _assets(self, story_id: str) -> dict[str, Any]:
        """资产分发。若选了模拟场景，把模拟参数注入 scene_export 再分发。"""
        sim = self.simulations.get(story_id, simulations.SIM_NONE)
        if sim == simulations.SIM_NONE:
            return self.orc.dispatch_assets(story_id)
        export = self.orc.scene_export_for(story_id)
        if export is None:
            raise OrchestratorError(f"故事 {story_id} 还没有 scene_export")
        return self.orc.dispatch_assets(
            story_id, simulations.apply_to_export(export, sim, story_id=story_id)
        )

    def _render(self, story_id: str) -> dict[str, Any]:
        registry, name = self.ws.registry_for(self.ws.backend_choice.get(story_id))
        self.ws.backend_choice[story_id] = name
        res = self.orc.run_render(story_id, registry=registry)
        return {"story_id": story_id, "state": res["state"],
                "summary": res["summary"], "backend": name}

    def _cut(self, story_id: str) -> dict[str, Any]:
        return self.orc.assemble_rough_cut(story_id, export_media=True)

    def _publish(self, story_id: str) -> dict[str, Any]:
        """FINAL_REVIEW → PUBLISHED。**只有 PASS_FULL（商业可发布）才放行**。"""
        return self.orc.publish_story(story_id, decided_by="webui").to_dict()

    # -- 人工审核 --------------------------------------------------------

    def pending_reviews(self) -> list[dict[str, Any]]:
        """全厂待人工处理的闸门（含被阻塞、需要决策的故事）。"""
        rows: list[dict[str, Any]] = []
        for hold in self.orc.pending_holds():
            story = self.orc.get_story(hold["story_id"])
            rows.append(
                {
                    "kind": "HOLD",
                    "story_id": hold["story_id"],
                    "title": story["title"],
                    "reason": hold.get("reason"),
                    "stage": hold.get("stage"),
                    "gate_ids": hold.get("gate_ids") or [],
                    "note": hold.get("note"),
                    "since": hold.get("since"),
                    "gates": [
                        self.gate_detail(g)
                        for g in (hold.get("gate_ids") or [])
                        if self.orc.store.get_gate_report(g)
                    ],
                }
            )
        # 自修复中的故事：信息卡（不需要人工点"继续"，系统自己修）
        for s in self.orc.list_stories(status=StoryStatus.AUTO_REPAIRING):
            d = s.get("repair_directive") or {}
            rows.append(
                {
                    "kind": "AUTO_REPAIR",
                    "story_id": s["story_id"],
                    "title": s["title"],
                    "reason": f"{d.get('code')}：{d.get('note') or ''}",
                    "stage": s["stage"],
                    "gate_ids": [],
                    "repair_directive": d,
                }
            )
        for s in self.orc.list_stories(status=StoryStatus.BLOCKED):
            qa = self.orc._qa_results.get(s["story_id"])
            rows.append(
                {
                    "kind": "BLOCKED",
                    "story_id": s["story_id"],
                    "title": s["title"],
                    "reason": s["blocked_reason"],
                    "stage": s["stage"],
                    "gate_ids": [],
                    "repair_plan": qa.repair_plan if qa else None,
                }
            )
        return rows

    # -- 制片人控制台：审核中心 / 工作台 / 成本 --------------------------

    def module_reviews(self, story_id: str) -> dict[str, Any]:
        """统一专业审核层视图 + 汇总（供审核中心）。"""
        reviews = self.orc.module_reviews(story_id)
        counts: dict[str, int] = {}
        for r in reviews:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        story = self.orc.get_story(story_id)
        return {
            "story_id": story_id,
            "state": story["state"],
            "reviews": reviews,
            "summary": counts,
            # 待人工终确认的模块（专业审核已过、等人按钮）
            "awaiting_confirm": [r for r in reviews if r["verdict"] == "CONFIRM"],
            "repairing": [r for r in reviews if r["verdict"] == "REPAIR"],
        }

    def story_workbench(self, story_id: str) -> dict[str, Any]:
        """角色/场景/道具真实资产预览：给出可 /file 拉取的相对路径。"""
        orc = self.orc
        script = orc._shooting_scripts.get(story_id) or {}

        characters = []
        for ch in script.get("characters") or []:
            pack = orc.store.character_registry.get(ch["character_id"]) or {}
            characters.append({
                "id": ch["character_id"],
                "name": ch.get("name") or pack.get("name"),
                "gender": ch.get("gender"),
                "is_real": bool(pack.get("is_real_image")),
                "image": pack.get("base_face_image"),   # 相对路径，配 /file 用
                "generation_method": pack.get("generation_method"),
            })

        scenes = [
            {"id": aid, "is_real": bool(r.get("is_real_image")),
             "image": r.get("scene_image"), "tags": r.get("tags") or [],
             "quality": r.get("quality_score")}
            for aid, r in orc.store.scene_atoms.items()
            if r.get("story_id") == story_id
        ]

        props = [
            {"id": pid, "name": r.get("name"),
             "is_real": bool(r.get("is_real_image")),
             "screen_kind": r.get("screen_kind"),
             "render_method": r.get("prop_render_method"),
             "images_by_state": r.get("prop_images_by_state") or {}}
            for pid, r in orc.store.prop_library.items()
            if r.get("story_id") == story_id
        ]
        return {"story_id": story_id, "characters": characters,
                "scenes": scenes, "props": props}

    def optimizer_view(self, *, apply: bool = False) -> dict[str, Any]:
        """OptimizerDNA 复盘：遥测 + 建议 + 已应用调参 + 自我验收结论。

        apply=False 只分析出建议（不改档）；apply=True 跑一轮自我优化闭环。
        """
        tel = self.orc.telemetry()
        report = self.ws.call(lambda: self.orc.run_optimizer(auto_apply=apply))
        return {"telemetry": tel, **report}

    def cost_view(self) -> dict[str, Any]:
        """成本额度：把可用的预算闸汇总（默认 mock 工厂无真实预算）。"""
        budgets = []
        img = getattr(self.orc, "image_backend", None)
        if img is not None and getattr(img, "budget", None) is not None:
            budgets.append({"name": "图像(Together FLUX)",
                            **img.budget.summary()})
        return {
            "budgets": budgets,
            "configured": bool(budgets),
            "note": ("默认工厂为 mock 模式，无真实计费预算。接入真实图像/视频后端后，"
                     "此处显示各预算闸的已花/上限/剩余与逐笔提交。"),
        }

    def gate_detail(self, gate_id: str) -> dict[str, Any] | None:
        report = self.orc.store.get_gate_report(gate_id)
        if report is None:
            return None
        wo = self.orc.store.get_workorder(report["workorder_id"])
        return {
            **report,
            "workorder_type": wo.workorder_type if wo else None,
            "workorder_status": wo.status.value if wo else None,
            "requirement": wo.requirement if wo else {},
            #: 硬拦截不可被人工推翻 —— 页面据此禁用"通过"按钮
            "human_overridable": not report.get("hard_blocks"),
        }

    def review(
        self,
        story_id: str,
        *,
        approved: bool,
        decided_by: str,
        note: str = "",
        gate_id: str | None = None,
    ) -> dict[str, Any]:
        """人工裁决。按挂起原因路由到三个真实入口，绝不绕过状态机。"""
        story = self.orc.get_story(story_id)
        hold = story.get("hold") or {}
        reason = hold.get("reason")
        if story["status"] != StoryStatus.WAITING_HUMAN.value:
            raise OrchestratorError(
                f"故事 {story_id} 当前不在等待人工审核（{story['state']}）"
            )
        decision = {"approved": approved, "decided_by": decided_by, "note": note}

        def run() -> dict[str, Any]:
            if reason == "script_gate3":
                out = self.orc.approve_script_gate(story_id, decision)
            elif reason in ("identity_review", "asset_gate_review"):
                # 人脸/角色审核（人工点 2）：逐个裁决人脸 Gate
                targets = [gate_id] if gate_id else list(hold.get("gate_ids") or [])
                if not targets:
                    raise OrchestratorError("该故事没有待裁决的人脸 Gate")
                out = {}
                for g in targets:
                    out = self.orc.resolve_asset_gate(story_id, g, decision)
            elif reason in ("shot_qa_critical", "final_review_critical"):
                self.orc.release_hold(story_id, decided_by=decided_by, note=note)
                # 拒绝 = 按修复计划精准打回；通过 = 人工认可，继续往下走
                out = (
                    {"released": True}
                    if approved
                    else self.orc.apply_repair(story_id)
                )
            else:
                self.orc.release_hold(story_id, decided_by=decided_by, note=note)
                out = {"released": True}
            self.orc.record_human_note(
                story_id, note=note, decided_by=decided_by,
                context=f"{reason}:{'approved' if approved else 'rejected'}",
            )
            return out

        result = self.ws.call(run)
        return {"result": _jsonable(result), "story": self.story_detail(story_id)}

    # -- 资产浏览器 ------------------------------------------------------

    def assets(self, kind: str) -> list[dict[str, Any]]:
        store = self.orc.store
        if kind == "scene":
            return [
                {
                    "id": aid,
                    "asset_type": r.get("asset_type"),
                    "tags": r.get("tags") or [],
                    "quality_score": r.get("quality_score"),
                    "gate_passed": r.get("gate_passed"),
                    "reusable": r.get("reusable"),
                    "story_id": r.get("story_id"),
                    "asset_hash": r.get("asset_hash"),
                    "created_at": r.get("created_at"),
                }
                for aid, r in store.scene_atoms.items()
            ]
        if kind == "identity":
            return [
                {
                    "id": cid,
                    "master_pack_id": p.get("master_pack_id"),
                    "base_face_asset_id": p.get("base_face_asset_id"),
                    "age_line": sorted((p.get("age_line") or {}).keys()),
                    "family_pack": sorted((p.get("family_pack") or {}).keys()),
                    "gate_passed": p.get("gate_passed"),
                    "reusable": p.get("reusable"),
                    "story_id": p.get("story_id"),
                    "asset_hash": p.get("asset_hash"),
                    "created_at": p.get("backflow_at"),
                }
                for cid, p in store.character_registry.items()
            ]
        if kind == "prop":
            return [
                {
                    "id": pid,
                    "states": sorted((r.get("asset_ids_by_state") or {}).keys()),
                    "continuity_supported": r.get("continuity_supported"),
                    "quality_score": r.get("quality_score"),
                    "gate_passed": r.get("gate_passed"),
                    "reusable": r.get("reusable"),
                    "story_id": r.get("story_id"),
                    "asset_hash": r.get("asset_hash"),
                    "created_at": r.get("created_at"),
                }
                for pid, r in store.prop_library.items()
            ]
        raise ValueError(f"未知资产类型 {kind!r}，可选 scene / identity / prop")

    # -- 工单 -----------------------------------------------------------

    def workorders(
        self,
        *,
        story_id: str | None = None,
        workorder_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            w.to_dict()
            for w in self.orc.store.list_workorders(
                story_id=story_id, workorder_type=workorder_type, status=status
            )
        ]
        for r in rows:
            gate = r.get("gate_report") or {}
            r["gate_id"] = gate.get("gate_id")
            r["gate_passed"] = gate.get("passed")
            r["hard_blocks"] = gate.get("hard_blocks") or []
            r["awaiting_review"] = bool(
                r["status"] == "GATE_REVIEW"
                and gate.get("passed")
                and gate.get("human_review_required")
            )
        return rows

    def workorder_detail(self, workorder_id: str) -> dict[str, Any]:
        wo = self.orc.store.get_workorder(workorder_id)
        if wo is None:
            raise OrchestratorError(f"工单不存在: {workorder_id}")
        d = wo.to_dict()
        if d.get("gate_report"):
            d["gate_detail"] = self.gate_detail(d["gate_report"]["gate_id"])
        return d

    def decide_gate(
        self, gate_id: str, *, approved: bool, decided_by: str, note: str = ""
    ) -> dict[str, Any]:
        """在工单页直接裁决某个资产 Gate（等价于审核页的单条裁决）。"""
        report = self.orc.store.get_gate_report(gate_id)
        if report is None:
            raise OrchestratorError(f"Gate 不存在: {gate_id}")
        wo = self.orc.store.get_workorder(report["workorder_id"])
        if wo is None:
            raise OrchestratorError(f"工单不存在: {report['workorder_id']}")
        story_id = wo.quad.story_id
        story = self.orc.get_story(story_id)
        if story["status"] == StoryStatus.WAITING_HUMAN.value:
            return self.review(
                story_id, approved=approved, decided_by=decided_by,
                note=note, gate_id=gate_id,
            )
        # 故事没挂起时直接走 facade（硬拦截仍会抛错）
        result = self.ws.call(
            lambda: self.orc.facade_for(story_id).approve_gate(
                gate_id, {"approved": approved, "decided_by": decided_by, "note": note}
            )
        )
        return {"result": _jsonable(result), "story": self.story_detail(story_id)}

    # -- 成片 -----------------------------------------------------------

    def cut_view(self, story_id: str) -> dict[str, Any]:
        """成片预览所需的一切：时间线、字幕、导出产物、诚实标注。"""
        story = self.orc.get_story(story_id)
        root = self.ws.bundle_root_for(story_id)
        cut: dict[str, Any] = {}
        if root is not None and (root / "10_outputs/rough_cut.json").exists():
            import json

            cut = json.loads(
                (root / "10_outputs/rough_cut.json").read_text(encoding="utf-8")
            )
        exports = cut.get("exports") or {}
        video = exports.get("video") if isinstance(exports.get("video"), dict) else None
        return {
            "story_id": story_id,
            "title": story["title"],
            "available": bool(cut),
            "shot_count": cut.get("shot_count", 0),
            "total_duration_sec": cut.get("total_duration_sec", 0),
            "duration_in_target": cut.get("duration_in_target"),
            "target_range_sec": cut.get("target_range_sec",
                                        [TARGET_MIN_SEC, TARGET_MAX_SEC]),
            "media_ready": cut.get("media_ready", False),
            "is_generated_footage": cut.get("is_generated_footage", False),
            "backend": self.ws.backend_choice.get(story_id),
            "timeline": cut.get("timeline", []),
            "subtitles": cut.get("subtitles", []),
            "edl": cut.get("edl", []),
            "exports": {
                "video": video.get("relpath") if video and video.get("ok") else None,
                "video_error": None if (video or {}).get("ok") else (video or {}).get("reason"),
                "animatic": exports.get("animatic"),
                "subtitles": exports.get("subtitles"),
                "edl_text": exports.get("edl"),
            },
            "notes": cut.get("notes"),
        }


def _jsonable(value: Any) -> Any:
    """把 dataclass / 对象结果压成可 JSON 化的东西。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "summary"):
        return value.summary()
    return str(value)


__all__ = ["FactoryService", "next_action_for", "HardBlockOverrideError"]
