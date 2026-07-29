"""配置层测试 (Phase 5)

重点：变量名统一、密钥值绝不泄漏、缺密钥能正确降级。
"""

from __future__ import annotations

import importlib

import pytest

import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每个测试都在干净的密钥环境下跑，避免本机 .env 影响断言。"""
    for name in config.ALL_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


class TestRegistry:
    def test_env_example_covers_every_registered_key(self):
        """.env.example 必须列全 config 里登记的每一个密钥变量。"""
        text = (config.ROOT / ".env.example").read_text(encoding="utf-8")
        for name in config.ALL_KEY_NAMES:
            assert f"{name}=" in text, f".env.example 缺少 {name}"

    def test_env_and_example_have_same_keys(self):
        """.env 与 .env.example 的变量集合一致（防止两边漂移）。"""
        def keys(path):
            out = set()
            for line in (config.ROOT / path).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    out.add(line.split("=", 1)[0].strip())
            return out
        assert keys(".env") == keys(".env.example")

    def test_expected_variable_names(self):
        assert config.API_KEYS["video"] == (
            "KLING_API_KEY", "SEEDANCE_API_KEY", "RUNWAY_API_KEY")
        assert "OPENAI_API_KEY" in config.API_KEYS["llm"]
        assert "ELEVENLABS_API_KEY" in config.API_KEYS["audio"]


class TestReading:
    def test_missing_key_is_absent(self):
        assert config.has("KLING_API_KEY") is False
        assert config.get("KLING_API_KEY") == ""
        assert config.get("KLING_API_KEY", "fallback") == "fallback"

    def test_present_key_detected(self, monkeypatch):
        monkeypatch.setenv("KLING_API_KEY", "sk-abc")
        assert config.has("KLING_API_KEY") is True
        assert config.get("KLING_API_KEY") == "sk-abc"

    def test_whitespace_is_stripped(self, monkeypatch):
        monkeypatch.setenv("KLING_API_KEY", "  sk-abc  ")
        assert config.get("KLING_API_KEY") == "sk-abc"

    def test_require_raises_when_missing(self):
        with pytest.raises(RuntimeError) as ei:
            config.require("KLING_API_KEY")
        assert "KLING_API_KEY" in str(ei.value)

    def test_require_returns_when_present(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-1")
        assert config.require("OPENAI_API_KEY") == "sk-1"


class TestCapabilities:
    def test_all_disabled_when_no_keys(self):
        for group in config.API_KEYS:
            assert config.capability_enabled(group) is False

    def test_one_key_enables_the_group(self, monkeypatch):
        monkeypatch.setenv("SEEDANCE_API_KEY", "sk-x")
        assert config.capability_enabled("video") is True
        assert config.capability_enabled("image") is False

    def test_unknown_group_rejected(self):
        with pytest.raises(KeyError):
            config.capability_enabled("hologram")


class TestNoLeak:
    def test_describe_reports_status_not_values(self, monkeypatch):
        monkeypatch.setenv("KLING_API_KEY", "super-secret-value")
        import json
        blob = json.dumps(config.describe())
        assert "super-secret-value" not in blob      # 值绝不出现
        assert config.describe()["capabilities"]["video"]["keys"]["KLING_API_KEY"] is True

    def test_health_endpoint_exposes_config_without_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RUNWAY_API_KEY", "leak-me-if-you-can")
        from fastapi.testclient import TestClient
        from webui.app import create_app
        from webui.state import FactoryWorkspace

        client = TestClient(create_app(FactoryWorkspace(tmp_path / "ws")))
        r = client.get("/api/health")
        assert r.status_code == 200
        assert "leak-me-if-you-can" not in r.text
        cfg = r.json()["config"]
        assert cfg["capabilities"]["video"]["keys"]["RUNWAY_API_KEY"] is True


class TestRuntimeEnv:
    def test_web_port_is_int(self):
        assert isinstance(config.WEB_PORT, int)

    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("CINEMA_DNA_ENV", raising=False)
        monkeypatch.delenv("WEB_PORT", raising=False)
        reloaded = importlib.reload(config)
        assert reloaded.WEB_PORT == 8700
        assert reloaded.ENV in ("development", "staging", "production")
        # 恢复模块状态，避免影响其它测试
        importlib.reload(config)
