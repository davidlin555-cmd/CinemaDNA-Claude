"""连通性验证测试 (Phase 5)

关键：**测试绝不发真实网络请求**（会 flaky，且 CI 里没有真实 key）。
一律 mock HTTP 层，专注验证：
- 未配 key → SKIPPED，且**不发任何请求**
- 200 → OK + 记录响应结构
- 401/403 → AUTH_FAILED，清晰错误
- KLING 官方域名 404 → UNKNOWN_PROVIDER + 指引 KLING_API_BASE
- 密钥值**永不**出现在任何输出里
"""

from __future__ import annotations

import json

import pytest

import connectivity


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    import config
    for name in config.ALL_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def fake_http(status, payload):
    def _fn(url, headers, timeout=20):
        _fn.last_call = {"url": url, "headers": headers}
        return status, payload
    _fn.last_call = None
    return _fn


# ---------------------------------------------------------------------------
# 掩码
# ---------------------------------------------------------------------------


class TestMask:
    def test_masks_never_reveal_plaintext(self):
        secret = "sk-proj-SUPER-SECRET-abcdefghijklmnop"
        m = connectivity.mask(secret)
        assert secret not in m
        assert "SUPER" not in m and "SECRET" not in m
        assert m.startswith("sha256:") and "164" not in m  # 长度是本串长度不是别的

    def test_same_key_same_fingerprint(self):
        assert connectivity.mask("abc123") == connectivity.mask("abc123")
        assert connectivity.mask("abc123") != connectivity.mask("abc124")

    def test_empty_key(self):
        assert connectivity.mask("") == "(未配置)"


# ---------------------------------------------------------------------------
# 未配 key → 不发请求
# ---------------------------------------------------------------------------


class TestSkip:
    def test_unconfigured_is_skipped_without_network(self, monkeypatch):
        called = {"n": 0}
        def boom(*a, **k):
            called["n"] += 1
            raise AssertionError("未配 key 不应发请求")
        monkeypatch.setattr(connectivity, "_get_json", boom)

        r = connectivity.probe("openai")
        assert r.status == "SKIPPED"
        assert r.configured is False
        assert r.http_status is None
        assert called["n"] == 0

    def test_probe_all_skips_unconfigured(self):
        results = connectivity.probe_all()
        assert all(r["status"] == "SKIPPED" for r in results)
        assert {r["provider"] for r in results} == set(connectivity.PROBES)


# ---------------------------------------------------------------------------
# 成功
# ---------------------------------------------------------------------------


class TestSuccess:
    def test_openai_ok_records_shape(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        payload = {"object": "list", "data": [{"id": "gpt-4o"}, {"id": "whisper-1"}]}
        monkeypatch.setattr(connectivity, "_get_json", fake_http(200, payload))

        r = connectivity.probe("openai")
        assert r.ok is True and r.status == "OK"
        assert r.http_status == 200
        assert r.response_shape["count"] == 2
        assert "gpt-4o" in r.response_shape["sample_ids"]
        assert r.latency_ms is not None

    def test_auth_header_carries_key_but_result_does_not(self, monkeypatch):
        """请求头带明文 key（必须，才能认证），但结果对象里绝无明文。"""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-xyz")
        fn = fake_http(200, {"data": []})
        monkeypatch.setattr(connectivity, "_get_json", fn)

        r = connectivity.probe("openai")
        # 发出去的请求头确实带了 key（否则没法认证）
        assert "sk-secret-xyz" in fn.last_call["headers"]["Authorization"]
        # 但返回给调用方的结果里绝无明文
        assert "sk-secret-xyz" not in json.dumps(r.to_dict())

    def test_google_uses_header_not_url_query(self, monkeypatch):
        """Google key 走请求头，不进 URL query（避免出现在日志 / URL 里）。"""
        monkeypatch.setenv("GOOGLE_API_KEY", "goog-secret")
        fn = fake_http(200, {"models": [{"name": "models/gemini-2.5-pro"}]})
        monkeypatch.setattr(connectivity, "_get_json", fn)

        r = connectivity.probe("google")
        assert "goog-secret" not in fn.last_call["url"]   # URL 里没有 key
        assert fn.last_call["headers"]["x-goog-api-key"] == "goog-secret"
        assert r.ok and r.response_shape["count"] == 1


# ---------------------------------------------------------------------------
# 失败：清晰错误
# ---------------------------------------------------------------------------


class TestFailures:
    def test_auth_failed_on_401(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bad-key")
        payload = {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}
        monkeypatch.setattr(connectivity, "_get_json", fake_http(401, payload))

        r = connectivity.probe("anthropic")
        assert r.ok is False and r.status == "AUTH_FAILED"
        assert r.http_status == 401
        assert r.error and "invalid" in r.error

    def test_kling_without_base_is_skipped_no_network(self, monkeypatch):
        """KLING 走第三方网关：没配 KLING_API_BASE 就跳过，绝不猜主机乱发。"""
        monkeypatch.setenv("KLING_API_KEY", "api-key-kling-xxx")
        monkeypatch.delenv("KLING_API_BASE", raising=False)
        def boom(*a, **k):
            raise AssertionError("没配网关地址不应发请求")
        monkeypatch.setattr(connectivity, "_get_json", boom)

        r = connectivity.probe("kling")
        assert r.status == "NO_BASE_URL"
        assert r.ok is False
        assert "KLING_API_BASE" in (r.hint or "")

    def test_kling_valid_key_via_task_not_found_is_ok(self, monkeypatch):
        """Kling 用假任务查询验证：有效 key → 400 code 1201「任务不存在」= OK。"""
        monkeypatch.setenv("KLING_API_KEY", "real-key")
        monkeypatch.setenv("KLING_API_BASE", "https://api-singapore.klingai.com")
        fn = fake_http(400, {"code": 1201, "message": "Task not found by id: xxx"})
        monkeypatch.setattr(connectivity, "_get_json", fn)

        r = connectivity.probe("kling")
        # 只查询、不提交：URL 是任务查询端点，不是生成端点
        assert "/videos/text2video/" in fn.last_call["url"]
        assert r.ok is True and r.status == "OK"
        assert r.response_shape["auth"] == "ok"

    def test_kling_invalid_key_is_auth_failed(self, monkeypatch):
        """无效 key → 401 code 1002「api key not found」= AUTH_FAILED。"""
        monkeypatch.setenv("KLING_API_KEY", "bad-key")
        monkeypatch.setenv("KLING_API_BASE", "https://api-singapore.klingai.com")
        monkeypatch.setattr(connectivity, "_get_json",
                            fake_http(401, {"code": 1002, "message": "api key not found"}))
        r = connectivity.probe("kling")
        assert r.ok is False and r.status == "AUTH_FAILED"

    def test_kling_probe_never_submits_a_task(self, monkeypatch):
        """铁律：连通性验证绝不能是 POST（不提交任务、不消耗 units）。"""
        monkeypatch.setenv("KLING_API_KEY", "real-key")
        monkeypatch.setenv("KLING_API_BASE", "https://api-singapore.klingai.com")
        # 若探针试图 POST 生成端点，这里会捕捉到
        posted = {"n": 0}
        def watch(url, headers, timeout=20):
            # _get_json 只做 GET；这里断言 URL 不是纯生成端点（末尾无 task id 才危险）
            if url.rstrip("/").endswith("/videos/text2video"):
                posted["n"] += 1
            return 400, {"code": 1201, "message": "Task not found"}
        monkeypatch.setattr(connectivity, "_get_json", watch)
        connectivity.probe("kling")
        assert posted["n"] == 0   # 绝不打生成端点

    def test_get_json_sends_real_user_agent(self, monkeypatch):
        """必须带真实 User-Agent，否则 Cloudflare 会按 Python-urllib 签名封禁。"""
        captured = {}
        class FakeResp:
            status = 200
            def read(self): return b'{"data":[]}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake_urlopen(req, timeout=None):
            captured["ua"] = req.headers.get("User-agent")
            return FakeResp()
        monkeypatch.setattr(connectivity.urllib.request, "urlopen", fake_urlopen)
        connectivity._get_json("https://x.example/v1/models", {"Authorization": "Bearer k"})
        assert captured["ua"] and "Python-urllib" not in captured["ua"]

    def test_network_error_is_caught(self, monkeypatch):
        import urllib.error
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        def boom(*a, **k):
            raise urllib.error.URLError("name resolution failed")
        monkeypatch.setattr(connectivity, "_get_json", boom)

        r = connectivity.probe("openai")
        assert r.status == "NETWORK_ERROR"
        assert r.ok is False
        assert "不可达" in (r.error or "")

    def test_http_error_other_status(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
        monkeypatch.setattr(connectivity, "_get_json", fake_http(500, {"error": "server"}))
        r = connectivity.probe("openai")
        assert r.status == "HTTP_ERROR" and r.http_status == 500


# ---------------------------------------------------------------------------
# 汇总 / API
# ---------------------------------------------------------------------------


class TestSummaryAndApi:
    def test_summary_classifies(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-ok")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "bad")

        def router(url, headers, timeout=20):
            if "openai" in url:
                return 200, {"data": [{"id": "gpt-4o"}]}
            return 401, {"error": {"message": "no"}}
        monkeypatch.setattr(connectivity, "_get_json", router)

        s = connectivity.summary()
        assert "openai" in s["reachable"]
        assert "anthropic" in s["configured_but_failed"]
        assert set(s["not_configured"]) >= {"google", "kling"}

    def test_unknown_provider_rejected(self):
        with pytest.raises(KeyError):
            connectivity.probe("midjourney")

    def test_api_endpoint_never_leaks_keys(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-leak-test")
        monkeypatch.setattr(connectivity, "_get_json",
                            fake_http(200, {"data": [{"id": "gpt-4o"}]}))
        from fastapi.testclient import TestClient
        from webui.app import create_app
        from webui.state import FactoryWorkspace

        client = TestClient(create_app(FactoryWorkspace(tmp_path / "ws")))
        r = client.post("/api/connectivity")
        assert r.status_code == 200
        assert "sk-super-secret-leak-test" not in r.text
        body = r.json()
        assert "openai" in body["reachable"]
