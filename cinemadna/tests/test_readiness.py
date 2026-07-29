"""商业级就绪度审计测试 (Phase 6)

重点：诚实性——mock/占位绝不被算作"具备能力"；商业裁决与真实状态一致。
"""

from __future__ import annotations

import readiness


class TestAudit:
    def test_not_commercial_ready_now(self):
        """当前真实状态：尚未达到商业级（资产 mock、无音频、无多模态判断）。"""
        a = readiness.audit()
        assert a["commercial_ready"] is False
        assert a["verdict"] == "尚未达到商业级"

    def test_reached_t1_not_t2(self):
        """骨架 + 单片段已达，多镜一致未达。"""
        a = readiness.audit()
        tiers = {t["tier"]: t["complete"] for t in a["tiers"]}
        assert tiers["T0_骨架"] is True
        assert tiers["T1_单片段"] is True
        assert tiers["T2_多镜一致"] is False
        assert tiers["T3_有声成片"] is False
        assert tiers["T4_商业判断"] is False

    def test_mock_or_missing_not_counted_as_capable(self):
        """核心诚实性：场景/道具仍 mock、音频/多模态仍缺，绝不标 REAL。"""
        a = readiness.audit()
        by_id = {c["id"]: c for c in a["capabilities"]}
        for cid in ("scene_images", "prop_images", "script_llm"):
            assert by_id[cid]["status"] == readiness.MOCK
        for cid in ("audio_tts", "lipsync", "bgm_subtitle", "qa_judgment"):
            assert by_id[cid]["status"] == readiness.MISSING

    def test_what_is_actually_real(self):
        """如实认可已具备的：含 Phase A 的真实人脸 + image2video。"""
        a = readiness.audit()
        by_id = {c["id"]: c for c in a["capabilities"]}
        for cid in ("pipeline", "budget", "redline", "video_backend",
                    "final_review_struct", "identity_faces", "video_consistency"):
            assert by_id[cid]["status"] == readiness.REAL

    def test_blocking_gaps_listed(self):
        a = readiness.audit()
        gap_ids = {g["id"] for g in a["blocking_gaps"]}
        # Phase A 已补上 identity_faces/video_consistency；剩余缺口仍在阻塞清单
        assert {"scene_images", "audio_tts", "script_llm", "qa_judgment"} <= gap_ids

    def test_required_capabilities_mostly_unmet(self):
        a = readiness.audit()
        assert a["summary"]["required_real"] < a["summary"]["required_total"]

    def test_api_endpoint(self, tmp_path):
        from fastapi.testclient import TestClient
        from webui.app import create_app
        from webui.state import FactoryWorkspace
        client = TestClient(create_app(FactoryWorkspace(tmp_path / "ws")))
        r = client.get("/api/readiness")
        assert r.status_code == 200
        assert r.json()["commercial_ready"] is False
