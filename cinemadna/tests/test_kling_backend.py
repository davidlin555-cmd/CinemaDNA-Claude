"""KlingTextToVideoBackend 测试 (Phase 6) —— 全程 mock HTTP，不发真实请求、不花 units

重点：预算闸强制前置、提交失败不记账、轮询状态机、下载、串单校验、
以及"没预算闸建不起来"这条硬约束。
"""

from __future__ import annotations

import pytest

from cinemadna.asset_brain.common.bundle import Bundle
from cinemadna.asset_brain.common.quad import Quad
from director.shot_contract import SHOT_MEDIUM, new_shot_contract
from performance.service import PerformanceDNAService
from render.backend import FatalRenderError, MediaSink
from render.budget import (
    BudgetGate,
    BudgetNotArmedError,
    SubmissionNotConfirmedError,
    confirm_yes,
)
from render.kling_backend import KlingTextToVideoBackend
from render.service import RenderBrainService

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64

SCRIPT = {
    "script_id": "s1", "title": "T",
    "characters": [{"character_id": "char_a", "name": "林晚",
                    "personality_keywords": ["压抑"], "role_type": "主角"}],
    "props": [], "episodes": [{"episode_id": "EP001", "scenes": [
        {"scene_id": "EP001_SC001", "episode_id": "EP001", "beat_type": "CONFLICT",
         "spatial_needs": "可走动", "camera_intent": "压迫感",
         "characters": ["char_a"], "props": [], "prop_states": {}}]}],
}


def contract(duration=5.0, shot_id="EP001_SC001_SH001", order=1,
             task_id="task_shot_0001"):
    c = new_shot_contract(
        shot_id=shot_id, scene_id="EP001_SC001", episode_id="EP001",
        order=order, shot_type=SHOT_MEDIUM,
        quad=Quad(story_id="drama_0001", bundle_id="bundle_1",
                  task_id=task_id, asset_hash=None),
        camera={"shot_size": "中景", "movement": "固定", "angle": "平视", "intent": "对峙"},
        duration_sec=duration,
        emotion={"beat": "CONFLICT", "intensity": 0.82, "mood": "压抑"},
        scene_asset={"asset_id": "scene_x", "asset_hash": HASH_A,
                     "gate_passed": True, "tags": ["出租屋", "深夜"]},
        character_assets=[{"character_id": "char_a", "master_pack_id": "cmp_a",
                           "asset_hash": HASH_B, "gate_passed": True}],
        prop_assets=[], dialogue=[{"character_id": "char_a", "line": "我没有退路了。"}],
        notes="她被当众收回工牌",
    )
    PerformanceDNAService().run([c], shooting_script=SCRIPT)  # 签发合约
    return c


class FakeKlingHttp:
    """模拟 Kling API：提交返回 task_id，轮询几次后 succeed。"""

    def __init__(self, *, submit_status=200, submit_code=0, poll_to_succeed=1,
                 fail=False):
        self.submit_status = submit_status
        self.submit_code = submit_code
        self.poll_to_succeed = poll_to_succeed
        self.fail = fail
        self.calls = []
        self._polls = 0

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, body))
        if method == "POST":
            if self.submit_status != 200 or self.submit_code != 0:
                return self.submit_status, {"code": self.submit_code,
                                            "message": "rejected"}
            return 200, {"code": 0, "data": {"task_id": "909-test-task",
                                             "task_status": "submitted"}}
        # GET 轮询
        self._polls += 1
        if self.fail:
            return 200, {"code": 0, "data": {"task_status": "failed",
                                             "task_status_msg": "content blocked"}}
        if self._polls >= self.poll_to_succeed:
            return 200, {"code": 0, "data": {"task_status": "succeed",
                "task_result": {"videos": [{"duration": "5.1",
                    "url": "https://cdn.example/v.mp4"}]}}}
        return 200, {"code": 0, "data": {"task_status": "processing"}}


def gate(max_units=100.0, confirm=confirm_yes):
    return BudgetGate(max_units=max_units, confirm=confirm)


def backend(http, budget=None, **kw):
    return KlingTextToVideoBackend(
        budget=budget or gate(), api_key="test-key",
        base="https://api-test.example", http=http, **kw,
    )


# ---------------------------------------------------------------------------
# 硬约束：没预算闸建不起来
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_requires_budget_gate(self):
        with pytest.raises(FatalRenderError):
            KlingTextToVideoBackend(budget=None, api_key="k", base="https://x")  # type: ignore

    def test_capabilities_are_realistic(self):
        b = backend(FakeKlingHttp())
        assert b.capabilities.produces_media is True
        assert b.capabilities.is_async is True
        assert b.capabilities.accepts_any_resolution is True
        assert b.capabilities.duration_tolerance_sec >= 0.5


# ---------------------------------------------------------------------------
# 预算闸前置
# ---------------------------------------------------------------------------


class TestBudgetGateFirst:
    def test_disarmed_budget_blocks_before_any_http(self, tmp_path):
        http = FakeKlingHttp()
        b = backend(http, budget=BudgetGate())     # 未开预算
        sink = MediaSink(Bundle(tmp_path, "b1").ensure())
        with pytest.raises(BudgetNotArmedError):
            b.submit(_req(contract()))
        assert http.calls == []                    # 关键：一个请求都没发出

    def test_unconfirmed_blocks_before_http(self, tmp_path):
        http = FakeKlingHttp()
        b = backend(http, budget=BudgetGate(max_units=100, confirm=None))
        with pytest.raises(SubmissionNotConfirmedError):
            b.submit(_req(contract()))
        assert http.calls == []

    def test_records_only_after_successful_submit(self):
        http = FakeKlingHttp()
        g = gate(max_units=100)
        b = backend(http, budget=g)
        b.submit(_req(contract()))
        assert g.spent_units == 2.0               # 提交成功 → 记账
        assert g.submissions[0]["shot_id"] == "EP001_SC001_SH001"

    def test_rejected_submit_does_not_record(self):
        """提交被拒（400）不记账——不该为失败的提交扣预算。"""
        http = FakeKlingHttp(submit_status=400, submit_code=1201)
        g = gate(max_units=100)
        b = backend(http, budget=g)
        with pytest.raises(FatalRenderError):
            b.submit(_req(contract()))
        assert g.spent_units == 0.0

    def test_budget_cap_stops_second_shot(self, tmp_path):
        """预算只够一次，第二个镜头被预算闸拦——防止批量误烧。"""
        g = gate(max_units=3)
        http = FakeKlingHttp()
        registry_backend = backend(http, budget=g)
        registry_backend.download = lambda url, dest: dest.write_bytes(b"\x00" * 5000)
        c1 = contract()
        c2 = contract(shot_id="EP001_SC001_SH002", order=2, task_id="task_shot_0002")
        from render.registry import BackendRegistry
        svc = RenderBrainService(registry=BackendRegistry(default=registry_backend),
                                 bundle=Bundle(tmp_path, "b1").ensure())
        result = svc.render_all([c1, c2])
        assert len(result.rendered) == 1            # 第一个成功
        assert result.failed and "预算闸拦截" in result.failed[0]["reason"]
        assert g.spent_units == 2.0                # 只扣了一次

    def test_retry_does_not_resubmit_or_double_charge(self, tmp_path):
        """下载瞬断触发 service 重试 —— 绝不能重复提交/重复扣费。"""
        http = FakeKlingHttp()
        g = gate(max_units=100)
        b = backend(http, budget=g)
        attempts = {"n": 0}
        def flaky_download(url, dest):
            attempts["n"] += 1
            if attempts["n"] == 1:
                from render.backend import RetryableRenderError
                raise RetryableRenderError("下载瞬断")
            dest.write_bytes(b"\x00" * 5000)
        b.download = flaky_download
        from render.registry import BackendRegistry
        svc = RenderBrainService(registry=BackendRegistry(default=b),
                                 bundle=Bundle(tmp_path, "b1").ensure(), max_attempts=2)
        result = svc.render_all([contract()])
        assert result.rendered                       # 重试后成功
        posts = [c for c in http.calls if c[0] == "POST"]
        assert len(posts) == 1                        # 只提交了一次
        assert g.spent_units == 2.0                  # 只扣了一次


# ---------------------------------------------------------------------------
# 提交 / 轮询 / 下载
# ---------------------------------------------------------------------------


class TestFlow:
    def test_submit_posts_correct_body(self):
        http = FakeKlingHttp()
        b = backend(http)
        b.submit(_req(contract()))
        method, url, body = http.calls[0]
        assert method == "POST" and url.endswith("/v1/videos/text2video")
        assert body["model_name"] == "kling-v1"
        assert body["prompt"]                       # 提示词来自合约
        assert body["aspect_ratio"] == "9:16"

    def test_poll_processing_then_succeed(self):
        http = FakeKlingHttp(poll_to_succeed=3)
        b = backend(http)
        job = b.submit(_req(contract()))
        assert b.poll(job) == (False, None)         # processing
        assert b.poll(job) == (False, None)
        done, url = b.poll(job)
        assert done and url == "https://cdn.example/v.mp4"

    def test_poll_failed_is_fatal(self):
        http = FakeKlingHttp(fail=True)
        b = backend(http)
        job = b.submit(_req(contract()))
        with pytest.raises(FatalRenderError):
            b.poll(job)

    def test_full_render_via_service(self, tmp_path):
        """整条：合约 → KlingBackend → 下载 → rendered_shot（mock 下载）。"""
        http = FakeKlingHttp(poll_to_succeed=1)
        g = gate()
        b = backend(http, budget=g)
        # 让 download 写个假文件而不真的联网
        b.download = lambda url, dest: dest.write_bytes(b"\x00" * 5000)
        from render.registry import BackendRegistry
        bundle = Bundle(tmp_path, "b1").ensure()
        svc = RenderBrainService(registry=BackendRegistry(default=b), bundle=bundle)
        c = contract()
        result = svc.render_all([c])
        assert result.rendered, result.failed
        r = result.rendered[0]
        assert r["is_real_media"] is True
        assert r["is_generated_footage"] is True     # 真实模型出的画面
        assert r["backend"] == "kling-text2video"
        assert bundle.exists(r["file_relpath"])
        assert result.summary()["is_generated_footage"] is True


def _req(c):
    from render.router import route_shot
    return RenderBrainService.build_request(c, route_shot(c))
