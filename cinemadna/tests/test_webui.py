"""Web 控制台测试 (Phase 5)

覆盖用户在浏览器里要走的完整闭环：
查看故事状态 → 审批 Gate → 触发下一步 → 预览成片，
外加安全性（路径越界、红线不可推翻）与错误语义。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cinemadna.asset_brain.identity_dna import fusion_mock
from cinemadna.asset_brain.scene_dna.service import SceneDNAService
from render.backend import ffmpeg_available
from webui.app import create_app
from webui.state import FactoryWorkspace

THEME = "县城女护士被高利贷追债，最后逆袭翻身"
needs_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="需要 ffmpeg")


@pytest.fixture
def ws(tmp_path) -> FactoryWorkspace:
    return FactoryWorkspace(tmp_path / "ws", bundle_date="20260723")


@pytest.fixture
def client(ws) -> TestClient:
    return TestClient(create_app(ws))


def new_story(client, *, theme=THEME, backend="mock", **kw) -> str:
    body = {"title": "样片", "theme": theme, "backend": backend, **kw}
    r = client.post("/api/stories", json=body)
    assert r.status_code == 201, r.text
    return r.json()["story_id"]


def run_all(client, story_id, *, limit=12) -> list[str]:
    """一路点「执行下一步」，直到跑不动为止。"""
    ran = []
    for _ in range(limit):
        st = client.get(f"/api/stories/{story_id}").json()
        nxt = st["next"]
        if nxt["blocked"] or not nxt["action"]:
            break
        r = client.post(f"/api/stories/{story_id}/step", json={})
        assert r.status_code == 200, r.text
        ran.append(r.json()["ran"])
    return ran


def run_all_get(client, story_id, *, limit=12) -> dict:
    """一路点到底，返回最终故事状态。"""
    run_all(client, story_id, limit=limit)
    return client.get(f"/api/stories/{story_id}").json()


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------


class TestBasics:
    def test_index_page_served(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "CinemaDNA 工厂控制台" in r.text
        # 自包含：不许外链任何资源
        assert "http://" not in r.text.split("<script>")[0]

    def test_health(self, client, ws):
        d = client.get("/api/health").json()
        assert d["ok"] is True
        assert "mock" in d["backends"]["choices"]
        assert str(ws.root) == d["workspace"]

    def test_empty_board(self, client):
        b = client.get("/api/factory").json()
        assert b["stories_total"] == 0
        assert b["script_slots"]["available"] == b["script_slots"]["max"]

    def test_create_validates_input(self, client):
        assert client.post("/api/stories", json={"title": "x", "theme": ""}).status_code == 422
        r = client.post("/api/stories",
                        json={"title": "x", "theme": "y", "backend": "kling"})
        assert r.status_code == 400 and r.json()["code"] == "BAD_REQUEST"

    def test_unknown_story_is_404(self, client):
        r = client.get("/api/stories/drama_9999")
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# 完整闭环
# ---------------------------------------------------------------------------


class TestFullFlowFromBrowser:
    def test_mock_run_self_heals_to_final_cut_review(self, client):
        """mock 无真实素材 → 成片总审驳回 → 自动重修耗尽 → 升级成片人工审核（点3）。"""
        sid = new_story(client)          # 默认 mock 后端
        run_all(client, sid)
        st = client.get(f"/api/stories/{sid}").json()
        assert st["stage"] == "FINAL_REVIEW"
        # 不再裸停 BLOCKED：自动修复用尽后落到 3 个人工点之一（成片审核）
        assert st["status"] == "WAITING_HUMAN"
        assert st["hold"]["reason"] == "final_review_critical"
        assert st["final_review_summary"]["verdict"] == "REJECTED"

    @needs_ffmpeg
    def test_placeholder_is_not_commercially_publishable(self, client):
        """placeholder(占位色板=静帧) → PASS_STRUCTURAL，自愈也到不了 PASS_FULL
        → 落成片人工终确认，且**发布被拒**（只有 PASS_FULL 才进发布包）。"""
        sid = new_story(client, backend="placeholder")
        run_all(client, sid)
        st = client.get(f"/api/stories/{sid}").json()
        assert st["stage"] == "FINAL_REVIEW"
        assert st["status"] == "WAITING_HUMAN"
        assert st["final_review_summary"]["commercial_ready"] is False
        # 未达 PASS_FULL → 发布包拒绝
        r = client.post(f"/api/stories/{sid}/step", json={"action": "publish"})
        assert r.status_code == 409

    def test_board_reflects_progress(self, client):
        sid = new_story(client)
        client.post(f"/api/stories/{sid}/step", json={})
        b = client.get("/api/factory").json()
        row = b["stories"][0]
        assert row["story_id"] == sid
        assert row["stage"] == "SCRIPT_DONE"
        assert row["next"]["action"] == "assets"
        assert row["next"]["blocked"] is False

    def test_parallel_slots_visible_on_board(self, client):
        a, b = new_story(client), new_story(client)
        client.post(f"/api/stories/{a}/step", json={})   # a 已提交剧本
        board = client.get("/api/factory").json()
        assert board["stories_total"] == 2
        assert board["script_slots"]["available"] >= 1   # 槽位已释放

    def test_explicit_action_can_be_requested(self, client):
        sid = new_story(client)
        r = client.post(f"/api/stories/{sid}/step", json={"action": "scriptbrain"})
        assert r.json()["ran"] == "scriptbrain"

    def test_illegal_action_rejected(self, client):
        sid = new_story(client)
        r = client.post(f"/api/stories/{sid}/step", json={"action": "render"})
        assert r.status_code == 409 and r.json()["code"] == "INVALID_STAGE"

    def test_unknown_action_is_400(self, client):
        sid = new_story(client)
        r = client.post(f"/api/stories/{sid}/step", json={"action": "teleport"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# 人工审核
# ---------------------------------------------------------------------------


class TestHumanReview:
    def test_scene_gate_auto_approved_no_human(self, client, monkeypatch):
        """场景 Gate 需复核 → **自动放行**（非人工点），不再挂人工。"""
        original = SceneDNAService.mock_generate

        def patched(self, workorder):
            asset = original(self, workorder)
            asset["rights_risk"] = 0.22        # 触发"需复核"
            return asset

        monkeypatch.setattr(SceneDNAService, "mock_generate", patched)

        sid = new_story(client)
        client.post(f"/api/stories/{sid}/step", json={})   # scriptbrain
        client.post(f"/api/stories/{sid}/step", json={})   # assets → 自动放行
        st = client.get(f"/api/stories/{sid}").json()
        assert st["status"] == "RUNNING"
        assert st["stage"] == "ASSET_READY"
        assert st["asset_summary"]["auto_approved_gate_ids"]

    def test_real_face_holds_for_face_review_then_approve(self, client, monkeypatch):
        """生成真实人脸 → 人工点 2（人脸审核）→ 审核页出现 → 通过后自动续跑。"""
        from cinemadna.asset_brain.identity_dna.service import IdentityDNAService
        original = IdentityDNAService.mock_multi_face_fusion

        def patched(self, workorder):
            pack = original(self, workorder)
            pack["is_real_image"] = True   # 模拟真实人脸 → 必进人脸审核
            return pack

        monkeypatch.setattr(IdentityDNAService, "mock_multi_face_fusion", patched)

        sid = new_story(client)
        client.post(f"/api/stories/{sid}/step", json={})   # scriptbrain
        client.post(f"/api/stories/{sid}/step", json={})   # assets → 人脸审核
        st = client.get(f"/api/stories/{sid}").json()
        assert st["status"] == "WAITING_HUMAN"

        reviews = client.get("/api/reviews").json()
        assert reviews and reviews[0]["story_id"] == sid
        assert reviews[0]["reason"] == "identity_review"

        r = client.post(f"/api/stories/{sid}/review", json={
            "approved": True, "decided_by": "human_lin", "note": "人脸可用"})
        assert r.status_code == 200
        assert r.json()["story"]["stage"] == "ASSET_READY"

        events = client.get(f"/api/stories/{sid}").json()["events"]
        notes = [e for e in events if e["event"] == "HUMAN_NOTE"]
        assert notes and notes[-1]["note"] == "人脸可用"

    def test_review_when_not_waiting_is_conflict(self, client):
        sid = new_story(client)
        r = client.post(f"/api/stories/{sid}/review",
                        json={"approved": True, "decided_by": "x"})
        assert r.status_code == 409

    def test_blocked_story_appears_in_review_queue(self, client, ws):
        """红线阻塞的故事也要出现在审核页，附带修复计划。"""
        sid = new_story(client)
        client.post(f"/api/stories/{sid}/step", json={})   # 剧本
        clone = {
            "scenes": [], "props": [],
            "characters": [{
                "character_id": "char_clone", "name": "克隆脸",
                "generation_plan_override": {
                    "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
                },
            }],
        }
        ws.orc.dispatch_assets(sid, clone)
        # 红线 → 先进自修复态（审核页以 AUTO_REPAIR 信息卡呈现）
        rows = client.get("/api/reviews").json()
        auto = [r for r in rows if r["kind"] == "AUTO_REPAIR"]
        assert auto and auto[0]["story_id"] == sid
        assert "IDENTITY_REDLINE" in auto[0]["reason"]

        # 驱动：红线不自动绕过 → 升级人脸审核（人工点 2），出现在审核队列
        ws.orc.drive(sid)
        rows = client.get("/api/reviews").json()
        hold = [r for r in rows if r["kind"] == "HOLD" and r["story_id"] == sid]
        assert hold and hold[0]["reason"] == "identity_review"


# ---------------------------------------------------------------------------
# 红线：网页也不能推翻
# ---------------------------------------------------------------------------


class TestProducerConsole:
    """制片人控制台：审核中心 / 工作台 / 成本额度。"""

    def test_module_reviews_endpoint(self, client):
        sid = new_story(client)
        client.post(f"/api/stories/{sid}/step", json={})   # scriptbrain
        r = client.get(f"/api/stories/{sid}/reviews")
        assert r.status_code == 200
        body = r.json()
        assert len(body["reviews"]) == 9
        mods = {x["module"] for x in body["reviews"]}
        assert {"script", "scene", "identity", "prop", "director",
                "performance", "render", "audio", "final"} == mods
        # 剧本已跑 → 非 PENDING；渲染尚未 → PENDING
        by = {x["module"]: x for x in body["reviews"]}
        assert by["script"]["verdict"] != "PENDING"
        assert by["render"]["verdict"] == "PENDING"
        assert "summary" in body

    def test_workbench_endpoint(self, client):
        sid = new_story(client)
        run_all(client, sid)
        r = client.get(f"/api/stories/{sid}/workbench")
        assert r.status_code == 200
        body = r.json()
        assert "characters" in body and "scenes" in body and "props" in body
        # mock 模式无真实图，但结构齐全
        for ch in body["characters"]:
            assert set(ch) >= {"id", "name", "is_real", "image"}

    def test_cost_endpoint(self, client):
        r = client.get("/api/cost")
        assert r.status_code == 200
        body = r.json()
        assert "budgets" in body and "configured" in body
        assert body["configured"] is False    # 默认 mock 工厂无真实预算

    def test_optimizer_endpoints(self, client):
        sid = new_story(client)
        run_all(client, sid)
        # 只分析
        d = client.get("/api/optimizer").json()
        assert "telemetry" in d and "recommendations" in d and "tuning" in d
        assert d["telemetry"]["stories_total"] >= 1
        # 跑一轮自我优化闭环
        r = client.post("/api/optimizer/run", json={})
        assert r.status_code == 200
        assert r.json()["action"] in ("analyzed", "applied", "kept", "rolled_back")

    def test_reviews_populated_after_drive(self, client):
        """跑完全链路 → 各专业模块都有结论；mock 无真实素材故成片总审为 REPAIR。"""
        sid = new_story(client)
        run_all(client, sid)
        body = client.get(f"/api/stories/{sid}/reviews").json()
        by = {x["module"]: x for x in body["reviews"]}
        for m in ("scene", "identity", "prop", "director", "performance",
                  "render", "audio", "final"):
            assert by[m]["verdict"] != "PENDING", m
        # mock 成片过不了五维（无真实画面）→ 专业审核判 REPAIR（并已升级人工终确认）
        assert by["final"]["verdict"] == "REPAIR"


class TestSimulations:
    """模拟场景：让审核 / 阻塞 / 红线三条路径在网页上可复现。

    关键回归点：三大库跨剧共享，若上一部剧已回流同类资产，模拟请求会命中
    复用而跳过生成，模拟参数就失效。这些测试**先跑一部正常剧填满库**，
    再验证模拟仍能可靠触发 —— 正是当初漏掉、被实测抓出来的坑。
    """

    def _populate(self, client):
        """先跑一部正常剧，把三大库填满。"""
        sid = new_story(client, theme="县城女护士被高利贷追债")
        run_all(client, sid)
        return sid

    def test_options_exposed_in_health(self, client):
        sims = {s["value"] for s in client.get("/api/health").json()["simulations"]}
        assert {"none", "scene_rights_review", "scene_ai_degrade",
                "identity_clone"} <= sims

    def test_scene_review_auto_approved_even_after_library_populated(self, client):
        """场景需复核 → 自动放行（非人工点），不再挂人工；不影响后续自流转。"""
        self._populate(client)
        sid = new_story(client, theme="z", simulate="scene_rights_review")
        client.post(f"/api/stories/{sid}/step", json={})   # scriptbrain
        client.post(f"/api/stories/{sid}/step", json={})   # assets → 自动放行
        st = client.get(f"/api/stories/{sid}").json()
        assert st["status"] == "RUNNING"
        assert st["stage"] == "ASSET_READY"

    def test_ai_degrade_auto_recovers_then_continues(self, client):
        """纯 AI 场景判负 → 自动重生成（这次走自然原子）→ 自愈继续，不停厂。

        重生成不再带一次性模拟参数，故自然通过；随后 mock 成片在总审处
        落到成片人工审核（点 3）。全程无需人工点"继续"。
        """
        self._populate(client)
        sid = new_story(client, theme="z", simulate="scene_ai_degrade")
        st = run_all_get(client, sid)
        assert st["status"] == "WAITING_HUMAN"
        assert st["hold"]["reason"] == "final_review_critical"

    def test_clone_escalates_to_face_review(self, client, ws):
        """红线克隆 → 自动重修（永不自动绕）→ 升级人脸审核；克隆脸绝不入库。"""
        self._populate(client)
        sid = new_story(client, theme="z", simulate="identity_clone")
        st = run_all_get(client, sid)
        assert st["status"] == "WAITING_HUMAN"
        assert st["hold"]["reason"] == "identity_review"
        assert not any("sim" in k for k in ws.orc.store.character_registry)

    def test_simulate_flag_visible_on_board(self, client):
        sid = new_story(client, simulate="scene_rights_review")
        row = next(s for s in client.get("/api/factory").json()["stories"]
                   if s["story_id"] == sid)
        assert row["simulate"] == "scene_rights_review"

    def test_unknown_simulation_rejected(self, client):
        r = client.post("/api/stories",
                        json={"title": "x", "theme": "y", "simulate": "boom"})
        assert r.status_code == 400


class TestHardBlockOverWeb:
    def _clone_story(self, client, ws) -> tuple[str, str]:
        sid = new_story(client)
        client.post(f"/api/stories/{sid}/step", json={})
        clone = {
            "scenes": [], "props": [],
            "characters": [{
                "character_id": "char_clone", "name": "克隆脸",
                "generation_plan_override": {
                    "fusion_sources": fusion_mock.simulate_single_real_clone_sources()
                },
            }],
        }
        res = ws.orc.dispatch_assets(sid, clone)
        gate_id = res["results"]["identity"][0]["gate_id"]
        return sid, gate_id

    def test_gate_detail_marks_non_overridable(self, client, ws):
        _, gate_id = self._clone_story(client, ws)
        g = client.get(f"/api/gates/{gate_id}").json()
        assert g["hard_blocks"]
        assert g["human_overridable"] is False    # 前端据此禁用「通过」

    def test_web_approve_of_hard_block_is_refused(self, client, ws):
        """页面上点「通过」也没用：409 + HARD_BLOCK。"""
        _, gate_id = self._clone_story(client, ws)
        r = client.post(f"/api/gates/{gate_id}/decide", json={
            "approved": True, "decided_by": "human_boss", "note": "我批准"})
        assert r.status_code == 409
        assert r.json()["code"] == "HARD_BLOCK"
        assert "char_clone" not in ws.orc.store.character_registry


# ---------------------------------------------------------------------------
# 资产浏览器 / 工单
# ---------------------------------------------------------------------------


class TestAssetsAndWorkorders:
    def test_asset_browser_lists_three_libraries(self, client):
        sid = new_story(client)
        run_all(client, sid)
        scenes = client.get("/api/assets/scene").json()
        ids = client.get("/api/assets/identity").json()
        props = client.get("/api/assets/prop").json()
        assert scenes["count"] == 3 and ids["count"] == 2 and props["count"] == 2
        assert all(it["gate_passed"] for it in ids["items"])
        assert ids["items"][0]["master_pack_id"]
        assert props["items"][0]["states"]

    def test_unknown_asset_kind_is_400(self, client):
        assert client.get("/api/assets/audio").status_code == 400

    def test_workorder_list_and_filter(self, client):
        sid = new_story(client)
        run_all(client, sid)
        allw = client.get("/api/workorders").json()
        assert allw["count"] == 7
        scene_only = client.get("/api/workorders?type=SCENE").json()
        assert scene_only["count"] == 3
        assert all(w["workorder_type"] == "SCENE" for w in scene_only["items"])
        done = client.get("/api/workorders?status=BACKFLOWED").json()
        assert done["count"] == 7

    def test_workorder_detail_includes_gate(self, client):
        sid = new_story(client)
        run_all(client, sid)
        wid = client.get("/api/workorders").json()["items"][0]["workorder_id"]
        d = client.get(f"/api/workorders/{wid}").json()
        assert d["workorder_id"] == wid
        assert d["gate_detail"]["passed"] is True
        assert d["story_id"] == sid

    def test_unknown_workorder_is_conflict(self, client):
        assert client.get("/api/workorders/wo_nope").status_code == 409


# ---------------------------------------------------------------------------
# 成片预览与下载
# ---------------------------------------------------------------------------


class TestCutPreview:
    def test_cut_view_before_assembly(self, client):
        sid = new_story(client)
        c = client.get(f"/api/stories/{sid}/cut").json()
        assert c["available"] is False

    def test_cut_view_after_run(self, client):
        sid = new_story(client)
        run_all(client, sid)
        c = client.get(f"/api/stories/{sid}/cut").json()
        assert c["available"] is True
        assert c["shot_count"] == 8
        assert 30 <= c["total_duration_sec"] <= 60
        assert c["duration_in_target"] is True
        assert len(c["timeline"]) == 8
        # mock 后端没有真实素材，页面必须如实显示
        assert c["media_ready"] is False
        assert c["is_generated_footage"] is False
        assert c["exports"]["video"] is None
        assert c["exports"]["animatic"]

    def test_artifacts_listed(self, client):
        sid = new_story(client)
        run_all(client, sid)
        a = client.get(f"/api/stories/{sid}/artifacts").json()
        assert a["count"] > 30
        dirs = {i["dir"] for i in a["items"]}
        assert {"01_script", "06_shots", "10_outputs", "11_review"} <= dirs

    def test_file_download_and_path_guard(self, client):
        sid = new_story(client)
        run_all(client, sid)
        ok = client.get(f"/api/stories/{sid}/file",
                        params={"path": "10_outputs/animatic.html"})
        assert ok.status_code == 200 and "text/html" in ok.headers["content-type"]

        # 路径越界必须被拒
        bad = client.get(f"/api/stories/{sid}/file",
                         params={"path": "../../../../etc/passwd"})
        assert bad.status_code in (403, 404)
        missing = client.get(f"/api/stories/{sid}/file",
                             params={"path": "10_outputs/nope.mp4"})
        assert missing.status_code == 404

    @needs_ffmpeg
    def test_real_mp4_is_served(self, client):
        sid = new_story(client, backend="placeholder")
        # 跑到粗剪即可（避免成片总审后的自愈重渲），验证真实 MP4 被导出并可服务
        for _ in range(12):
            st = client.get(f"/api/stories/{sid}").json()
            if st["stage"] == "QA":          # cut 已产出
                break
            nxt = st["next"]
            if nxt["blocked"] or not nxt["action"]:
                break
            client.post(f"/api/stories/{sid}/step", json={})
        c = client.get(f"/api/stories/{sid}/cut").json()
        assert c["media_ready"] is True
        assert c["exports"]["video"] == "10_outputs/rough_cut.mp4"
        # 占位画面仍然要如实标注
        assert c["is_generated_footage"] is False

        r = client.get(f"/api/stories/{sid}/file", params={"path": c["exports"]["video"]})
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/mp4"
        assert len(r.content) > 10_000
