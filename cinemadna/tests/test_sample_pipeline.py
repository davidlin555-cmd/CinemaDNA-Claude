"""端到端样片通路测试 (Phase 3)

「假剧本 → 资产匹配 → Shot Contract → mock 渲染 → 简单拼接」整条链路，
并对照主规格的标杆样片指标：**1 场景、1 主角、5–8 镜头、30–60 秒**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cinemadna.asset_brain.common import schemas
from cinemadna.asset_brain.identity_dna import fusion_mock
from director.shot_contract import STATUS_RENDERED
from orchestrator.pipeline import OrchestratorError, PipelineOrchestrator
from orchestrator.story import InvalidStageTransition, StoryStage
from qa.repair import VERDICT_HUMAN, VERDICT_PASS, VERDICT_REPAIR
from render.backend import MockRenderBackend, ffmpeg_available
from render.registry import placeholder_registry

needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="需要 ffmpeg")

THEME = "县城女护士被高利贷追债，最后逆袭翻身"

#: 标杆样片：1 场景、1 主角、40 秒
BENCHMARK_SCRIPT = {
    "schema_version": schemas.SCHEMA_SHOOTING_SCRIPT,
    "script_id": "script_benchmark",
    "title": "标杆样片",
    "characters": [
        {
            "character_id": "char_linwan", "name": "林晚", "role_type": "主角",
            "age_range": "25-28", "gender": "female",
            "personality_keywords": ["压抑", "坚韧", "疲惫"],
            "need_age_line": True, "need_family": False,
        }
    ],
    "props": [
        {
            "prop_id": "prop_hospital_bill", "name": "医院缴费单",
            "description": "旧的纸质医院缴费单", "states_needed": ["完整"],
            "story_function": "建立经济压力", "continuity_critical": True,
        }
    ],
    "episodes": [
        {
            "episode_id": "EP001",
            "scenes": [
                {
                    "scene_id": "EP001_SC001", "episode_id": "EP001",
                    "beat_type": "CONFLICT",
                    "location_description": "县城老旧出租屋",
                    "time_of_day": "深夜", "mood": "压抑、疲惫",
                    "camera_intent": "中近景，压迫感",
                    "required_atoms": ["室内布局", "光线", "家具陈设", "窗户"],
                    "spatial_needs": "可支持人物走动与特写",
                    "estimated_duration_sec": 40,
                    "characters": ["char_linwan"],
                    "props": ["prop_hospital_bill"],
                    "prop_states": {"prop_hospital_bill": "完整"},
                    "dialogue": [{"character_id": "char_linwan", "line": "我没有退路了。"}],
                }
            ],
        }
    ],
}


def factory(tmp_path, **kw) -> PipelineOrchestrator:
    return PipelineOrchestrator(bundle_root=tmp_path, bundle_date="20260723", **kw)


def run_to_assets(orc: PipelineOrchestrator, *, theme=THEME, script=None, title="A"):
    s = orc.create_story(title=title)
    if script is None:
        orc.run_scriptbrain(s.story_id, theme)
        orc.dispatch_assets(s.story_id)
    else:
        orc.start_script(s.story_id)
        orc.submit_script(s.story_id, script_ref="handwritten.json")
        orc.dispatch_assets(s.story_id, script)
    return s


def run_full(orc: PipelineOrchestrator, story_id: str, *, backend=None):
    orc.run_director(story_id)
    orc.run_performance(story_id)
    orc.run_render(story_id, backend=backend)
    return orc.assemble_rough_cut(story_id)


# ---------------------------------------------------------------------------
# 一、完整通路
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_theme_to_rough_cut(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        cut = run_full(orc, s.story_id)

        assert cut["schema_version"] == schemas.SCHEMA_ROUGH_CUT
        assert cut["shot_count"] == 8
        assert cut["total_duration_sec"] == 42.0
        assert cut["duration_in_target"] is True     # 落在 30–60 秒
        assert cut["missing_shots"] == []
        # 诚实：mock 渲染下没有真实素材
        assert cut["media_ready"] is False

        story = orc.get_story(s.story_id)
        assert story["stage"] == "QA"
        assert story["render_summary"]["rendered"] == 8
        assert story["cut_summary"]["duration_in_target"] is True

    def test_stage_progression(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        assert orc.get_story(s.story_id)["stage"] == "ASSET_READY"
        orc.run_director(s.story_id)
        assert orc.get_story(s.story_id)["stage"] == "DIRECTING"
        orc.run_performance(s.story_id)
        assert orc.get_story(s.story_id)["stage"] == "PERFORMANCE"
        orc.run_render(s.story_id)
        assert orc.get_story(s.story_id)["stage"] == "RENDERING"
        orc.assemble_rough_cut(s.story_id)
        assert orc.get_story(s.story_id)["stage"] == "QA"

    def test_all_contracts_bound_to_quad_and_rendered(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        run_full(orc, s.story_id)

        for c in orc.contracts_for(s.story_id):
            assert c["status"] == STATUS_RENDERED
            assert c["story_id"] == s.story_id
            assert c["bundle_id"] == s.bundle_id
            # task_id 现按 shot_id 稳定派生（重渲不重复扣费）：task_<shot_id>
            assert c["task_id"] == f"task_{c['shot_id']}"
            assert c["asset_hash"].startswith("sha256:")   # 渲染产物的 hash
            # 合约里的每个资产都带 hash 且已过 Gate
            assert c["assets"]["scene"]["gate_passed"] is True
            for ch in c["assets"]["characters"]:
                assert ch["gate_passed"] and ch["asset_hash"]

    def test_bundle_contains_every_stage_artifact(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        run_full(orc, s.story_id)
        root = orc.bundle_for(s.story_id).root

        for rel in (
            "01_script/shooting_script.json",
            "02_cast/char_linwan/master_pack.json",
            "06_shots/shot_strategy.json",
            "06_shots/continuity_ledger.json",
            "10_outputs/rough_cut.json",
            "08_submissions/routing_decisions.json",
            "11_review/consistency/report.json",
        ):
            assert (root / rel).exists(), f"缺少 {rel}"

        assert list((root / "06_shots" / "contracts").glob("*.json"))
        assert list((root / "05_performance" / "acting_beats").glob("*.json"))
        assert list((root / "05_performance" / "bible").glob("*.json"))
        # 每个镜头一个 task_id 目录
        download_dirs = sorted(p.name for p in (root / "09_downloads").iterdir())
        assert len(download_dirs) == 8

        cut = json.loads((root / "10_outputs/rough_cut.json").read_text("utf-8"))
        assert cut["shot_count"] == 8

    def test_performance_bible_shared_across_stories(self, tmp_path):
        """表演圣经是工厂级资产：第二部剧沿用同一角色的标志性微表情。"""
        orc = factory(tmp_path, max_concurrent_scripts=2)
        a = run_to_assets(orc, title="A")
        run_full(orc, a.story_id)
        shots_a = len(orc._performance.bibles["char_linwan"]["source_shots"])

        b = run_to_assets(orc, title="B")
        orc.run_director(b.story_id)
        orc.run_performance(b.story_id)
        assert len(orc._performance.bibles["char_linwan"]["source_shots"]) > shots_a


# ---------------------------------------------------------------------------
# 二、标杆样片指标（1 场景 / 1 主角 / 5–8 镜头 / 30–60 秒）
# ---------------------------------------------------------------------------


class TestBenchmarkSample:
    def test_single_scene_single_lead(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc, script=BENCHMARK_SCRIPT, title="标杆")
        cut = run_full(orc, s.story_id)

        assert 5 <= cut["shot_count"] <= 8
        assert 30.0 <= cut["total_duration_sec"] <= 60.0
        assert cut["duration_in_target"] is True
        assert cut["scenes"] == ["EP001_SC001"]

        contracts = orc.contracts_for(s.story_id)
        # 只有一个主角，全片不应出现需要对手的反应镜
        assert all(c["shot_type"] != "REACTION" for c in contracts)
        assert {ch["character_id"] for c in contracts
                for ch in c["assets"]["characters"]} == {"char_linwan"}
        # 角色全程同一个 Master Pack
        packs = {ch["master_pack_id"] for c in contracts
                 for ch in c["assets"]["characters"]}
        assert len(packs) == 1

    def test_benchmark_continuity_and_consistency_pass(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc, script=BENCHMARK_SCRIPT, title="标杆")
        d = orc.run_director(s.story_id)
        assert d["summary"]["continuity_passed"] is True
        orc.run_performance(s.story_id)
        r = orc.run_render(s.story_id)
        assert r["summary"]["consistency_passed"] is True
        assert r["summary"]["rejected"] == 0


# ---------------------------------------------------------------------------
# 三、失败路径（该拦的必须拦住）
# ---------------------------------------------------------------------------


class TestFailurePaths:
    def test_render_failure_blocks_story_and_stops_assembly(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        orc.run_director(s.story_id)
        orc.run_performance(s.story_id)

        doomed = orc.contracts_for(s.story_id)[2]["shot_id"]
        res = orc.run_render(s.story_id, backend=MockRenderBackend(fail_shots={doomed}))

        # 渲染失败 → 自修复态（RENDER_RETRY），拼接被拦
        assert res["state"] == "AUTO_REPAIRING"
        assert res["summary"]["failed"] == 1
        assert orc.get_story(s.story_id)["repair_directive"]["code"] == "RENDER_RETRY"
        with pytest.raises(InvalidStageTransition):
            orc.assemble_rough_cut(s.story_id)

    def test_identity_blocked_story_never_reaches_director(self, tmp_path):
        """人物没过 IdentityDNA 红线 → 资产阶段就 BLOCKED → 不可能签出合约。"""
        orc = factory(tmp_path)
        clone_script = {
            "scenes": [],
            "characters": [{
                "character_id": "char_clone", "name": "克隆脸",
                "generation_plan_override": {
                    "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
                },
            }],
            "props": [],
        }
        s = orc.create_story(title="克隆")
        orc.start_script(s.story_id)
        orc.submit_script(s.story_id, script_ref="x.json")
        res = orc.dispatch_assets(s.story_id, clone_script)
        assert res["state"] == "AUTO_REPAIRING"      # 红线 → 自修复态（不放行）
        with pytest.raises(InvalidStageTransition):
            orc.run_director(s.story_id)

    def test_director_refuses_when_assets_missing(self, tmp_path):
        """跳过资产阶段直接排镜头 → 资产查不到 → 拒绝签合约。"""
        orc = factory(tmp_path)
        s = orc.create_story(title="X")
        orc.start_script(s.story_id)
        orc.submit_script(s.story_id, script_ref="x.json")
        s.advance_to(StoryStage.DIRECTOR_PLANNING)   # 经宪法层新阶段
        s.advance_to(StoryStage.DIRECTOR_QA)
        s.advance_to(StoryStage.ASSET_MATCHING)
        s.advance_to(StoryStage.ASSET_READY)
        with pytest.raises(Exception):
            orc.run_director(s.story_id, BENCHMARK_SCRIPT)

    def test_render_requires_performance_stage(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        orc.run_director(s.story_id)
        with pytest.raises(InvalidStageTransition):
            orc.run_render(s.story_id)      # 还没跑 PerformanceDNA

    def test_performance_refused_while_blocked(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc)
        orc.run_director(s.story_id)
        orc._get(s.story_id).block("人为阻塞")
        with pytest.raises(InvalidStageTransition):
            orc.run_performance(s.story_id)


# ---------------------------------------------------------------------------
# 四、并行性没有被后续阶段破坏
# ---------------------------------------------------------------------------


class TestQAInPipeline:
    """镜头级 QA 在渲染之后、粗剪之前拦一道。"""

    def _rendered_story(self, tmp_path):
        orc = factory(tmp_path)
        s = run_to_assets(orc, script=BENCHMARK_SCRIPT, title="标杆")
        orc.run_director(s.story_id)
        orc.run_performance(s.story_id)
        orc.run_render(s.story_id)
        return orc, s

    def test_clean_qa_passes_and_allows_assembly(self, tmp_path):
        orc, s = self._rendered_story(tmp_path)
        res = orc.run_qa(s.story_id)

        assert res["verdict"] == VERDICT_PASS
        assert res["state"] == "RENDERING"          # 没被阻塞
        # mock 素材会被记成 info，但不阻塞
        assert res["summary"]["info_notes"] >= 1
        assert res["summary"]["blocking_issues"] == 0
        cut = orc.assemble_rough_cut(s.story_id)
        assert cut["shot_count"] >= 5

    def test_repair_verdict_blocks_and_targets_performance(self, tmp_path):
        """表演层面的问题只打回 PerformanceDNA，不重跑资产。"""
        orc, s = self._rendered_story(tmp_path)
        for c in orc.contracts_for(s.story_id)[:2]:
            c["performance"]["acting_beats"] = []

        res = orc.run_qa(s.story_id)
        assert res["verdict"] == VERDICT_REPAIR
        # 镜头级 QA 判负 → 自修复态（QA_REPAIR，打回 PERFORMANCE），不停厂
        assert res["state"] == "AUTO_REPAIRING"
        assert res["repair_plan"]["target_stage"] == "PERFORMANCE"
        assert res["repair_plan"]["shots_to_redo"] == 2     # 只重跑这两个镜头
        d = orc.get_story(s.story_id)["repair_directive"]
        assert d["code"] == "QA_REPAIR" and d["target_stage"] == "PERFORMANCE"

        # 自修复态不许拼接
        with pytest.raises(InvalidStageTransition):
            orc.assemble_rough_cut(s.story_id)

    def test_render_level_problem_targets_rendering_only(self, tmp_path):
        orc, s = self._rendered_story(tmp_path)
        orc.contracts_for(s.story_id)[0]["render_result"]["duration_sec"] = 99.0
        res = orc.run_qa(s.story_id)
        assert res["repair_plan"]["target_stage"] == "RENDERING"
        assert res["repair_plan"]["affected_shots"] == [
            orc.contracts_for(s.story_id)[0]["shot_id"]
        ]

    def test_critical_issue_goes_to_human(self, tmp_path):
        """产物层身份漂移 = critical，机器不自动修，转人工。"""
        orc, s = self._rendered_story(tmp_path)
        contracts = orc.contracts_for(s.story_id)
        contracts[1]["render_result"]["reference_hashes"]["characters"][
            "char_linwan"
        ] = "sha256:" + "f" * 64

        res = orc.run_qa(s.story_id)
        assert res["verdict"] == VERDICT_HUMAN
        # 镜头级 critical 也先自动打回重做（QA_REPAIR）；修不动才升级成片总审。
        # 不再当场挂人工——工厂 3 个人工点里没有"镜头级 QA"。
        assert res["state"] == "AUTO_REPAIRING"
        assert orc.get_story(s.story_id)["repair_directive"]["code"] == "QA_REPAIR"

    def test_qa_reports_land_in_bundle(self, tmp_path):
        orc, s = self._rendered_story(tmp_path)
        orc.run_qa(s.story_id)
        root = orc.bundle_for(s.story_id).root
        for name in ("vision", "performance", "continuity", "global", "repair_plan"):
            assert (root / f"11_review/qa/{s.story_id}_{name}.json").exists()

    def test_apply_repair_requires_qa_first(self, tmp_path):
        orc, s = self._rendered_story(tmp_path)
        with pytest.raises(OrchestratorError):
            orc.apply_repair(s.story_id)


class TestFakeCutExport:
    """「假成片」导出：EDL / 字幕 / 动态分镜 / 真实 MP4。"""

    def _story(self, tmp_path, registry=None):
        orc = factory(tmp_path)
        s = run_to_assets(orc, script=BENCHMARK_SCRIPT, title="标杆")
        orc.run_director(s.story_id)
        orc.run_performance(s.story_id)
        orc.run_render(s.story_id, registry=registry)
        orc.run_qa(s.story_id)
        return orc, s

    def test_exports_without_media(self, tmp_path):
        orc, s = self._story(tmp_path)
        cut = orc.assemble_rough_cut(s.story_id, export_media=True)
        root = orc.bundle_for(s.story_id).root

        assert (root / "10_outputs/rough_cut.edl").exists()
        assert (root / "10_outputs/subtitles.vtt").exists()
        assert (root / "10_outputs/animatic.html").exists()
        # mock 素材拼不出视频，如实说明原因
        assert cut["exports"]["video"]["ok"] is False
        assert cut["media_ready"] is False

    @needs_ffmpeg
    def test_real_mp4_from_placeholder_media(self, tmp_path):
        orc, s = self._story(tmp_path, registry=placeholder_registry())
        cut = orc.assemble_rough_cut(s.story_id, export_media=True)
        root = orc.bundle_for(s.story_id).root

        assert cut["media_ready"] is True
        assert cut["is_generated_footage"] is False   # 占位色板，不是生成画面
        video = cut["exports"]["video"]
        assert video["ok"] is True
        mp4 = root / video["relpath"]
        assert mp4.exists() and mp4.stat().st_size > 10_000
        assert 30.0 <= cut["total_duration_sec"] <= 60.0

        story = orc.get_story(s.story_id)
        assert story["cut_summary"]["exports"]["video"] == "10_outputs/rough_cut.mp4"

    @needs_ffmpeg
    def test_qa_flags_placeholder_footage(self, tmp_path):
        """占位素材必须被质检如实标出来，不能当成片。"""
        orc, s = self._story(tmp_path, registry=placeholder_registry())
        codes = {i["code"] for i in orc._qa_results[s.story_id].vision["issues"]}
        assert "PLACEHOLDER_FOOTAGE" in codes


class TestParallelStillHolds:
    def test_second_story_scripts_while_first_renders(self, tmp_path):
        orc = factory(tmp_path, max_concurrent_scripts=1)
        a = run_to_assets(orc, title="A")
        orc.run_director(a.story_id)
        orc.run_performance(a.story_id)
        orc.run_render(a.story_id)

        # A 正在渲染，槽位早已释放，B 照常开工
        assert orc.can_start_new_story() is True
        b = orc.create_story(title="B")
        res = orc.run_scriptbrain(b.story_id, "婆媳矛盾与养老困局")
        assert res["parallel_signal"]["new_story_allowed"] is True

        st = orc.factory_status()
        assert st["by_stage"] == {"RENDERING": 1, "SCRIPT_DONE": 1}

    def test_two_stories_reach_rough_cut_independently(self, tmp_path):
        orc = factory(tmp_path, max_concurrent_scripts=2)
        a = run_to_assets(orc, title="A")
        b = run_to_assets(orc, theme="婆媳矛盾与养老困局", title="B")
        cut_a = run_full(orc, a.story_id)
        cut_b = run_full(orc, b.story_id)

        assert cut_a["story_id"] != cut_b["story_id"]
        assert cut_a["shot_count"] >= 5 and cut_b["shot_count"] >= 5
        # Bundle 各自独立
        assert (orc.bundle_for(a.story_id).root / "10_outputs/rough_cut.json").exists()
        assert (orc.bundle_for(b.story_id).root / "10_outputs/rough_cut.json").exists()
