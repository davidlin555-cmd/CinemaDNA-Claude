"""真实 API 连通性验证 (Phase 5) —— 最小真实调用，绝不泄漏密钥

目的：证明"从 config 读真实 Key → 发出真实认证请求 → 拿到结构化响应 →
成功/失败都有清晰结论"这条链路真的通，而**不**触发任何计费的重活。

## 安全约定（硬性）

1. **绝不打印密钥值**。结果里只出现掩码指纹 `mask()`（sha256 前 8 位 + 长度），
   用于让人确认"是我填的那把 key"，但不可逆。
2. **只打只读 / 免费端点**（列模型、账户查询），绝不提交视频生成等计费任务。
3. **密钥只发给该厂商的官方域名**。对来源不明的 key（如第三方 KLING 网关），
   不猜测主机乱发——那等于把密钥泄漏给错误的第三方。

## 探测端点（都是最小、只读、通常免费）

| 厂商      | 端点                                    | 认证方式              |
|-----------|-----------------------------------------|-----------------------|
| OpenAI    | GET /v1/models                          | Authorization: Bearer |
| Anthropic | GET /v1/models                          | x-api-key + version   |
| Google    | GET /v1beta/models                      | x-goog-api-key 头     |
| KLING     | GET {KLING_API_BASE}/v1/models（尽力）  | Authorization: Bearer |

Google 用请求头而非 URL query 传 key（避免密钥出现在 URL / 日志里）。
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import config

DEFAULT_TIMEOUT = 20


# ---------------------------------------------------------------------------
# 掩码：可确认身份、不可还原
# ---------------------------------------------------------------------------


def mask(value: str) -> str:
    """密钥指纹：sha256 前 8 位 + 长度。绝不暴露任何明文片段。"""
    if not value:
        return "(未配置)"
    fp = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"sha256:{fp}…({len(value)}位)"


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    provider: str
    capability: str
    key_var: str
    configured: bool
    key_fingerprint: str = "(未配置)"
    ok: bool = False
    #: SKIPPED（未配 key） / OK / AUTH_FAILED / HTTP_ERROR / NETWORK_ERROR / UNKNOWN_PROVIDER
    status: str = "SKIPPED"
    http_status: int | None = None
    latency_ms: int | None = None
    endpoint: str | None = None
    #: 成功时记录响应结构（顶层键、条目数、样例 id），绝不含密钥
    response_shape: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# HTTP 小工具
# ---------------------------------------------------------------------------


#: 正常 User-Agent。urllib 默认的 "Python-urllib/x" 常被 Cloudflare 按
#: 浏览器签名封禁（返回 403 "error code: 1010"），必须显式带一个真实 UA。
_USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CinemaDNA-Connectivity/0.5"
)


def _get_json(
    url: str, headers: dict[str, str], timeout: int = DEFAULT_TIMEOUT
) -> tuple[int, Any]:
    """发一个 GET，返回 (http_status, 解析后的 JSON 或原始文本)。"""
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json", **headers}
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, _try_json(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, _try_json(body)


def _try_json(body: str) -> Any:
    try:
        return json.loads(body)
    except Exception:
        return {"_raw": body[:300]}


def _shape(data: Any, list_key: str | None = None, id_key: str = "id") -> dict[str, Any]:
    """提取响应结构（不含任何敏感内容）：顶层键 + 列表长度 + 前几个 id。"""
    if not isinstance(data, dict):
        return {"type": type(data).__name__}
    shape: dict[str, Any] = {"top_level_keys": sorted(data.keys())[:12]}
    items = data.get(list_key) if list_key else None
    if isinstance(items, list):
        shape["count"] = len(items)
        shape["sample_ids"] = [
            str(it.get(id_key) or it.get("name") or "")[:40]
            for it in items[:3]
            if isinstance(it, dict)
        ]
    return shape


# ---------------------------------------------------------------------------
# 各厂商探针
# ---------------------------------------------------------------------------


def _probe_openai(key: str) -> tuple[str, int, dict[str, Any], str | None]:
    url = "https://api.openai.com/v1/models"
    status, data = _get_json(url, {"Authorization": f"Bearer {key}"})
    return url, status, _shape(data, "data"), _err(data)


def _probe_anthropic(key: str) -> tuple[str, int, dict[str, Any], str | None]:
    url = "https://api.anthropic.com/v1/models"
    status, data = _get_json(
        url, {"x-api-key": key, "anthropic-version": "2023-06-01"}
    )
    return url, status, _shape(data, "data"), _err(data)


def _probe_google(key: str) -> tuple[str, int, dict[str, Any], str | None]:
    # 用请求头传 key，避免出现在 URL / 日志里
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    status, data = _get_json(url, {"x-goog-api-key": key})
    return url, status, _shape(data, "models", id_key="name"), _err(data)


def _probe_kling(key: str) -> tuple[str, int, dict[str, Any], str | None]:
    # Kling 海外 API（api-singapore.klingai.com）。用**查询一个不存在的任务**来
    # 验证认证——纯 GET，绝不提交任何生成任务、绝不消耗 units：
    #   有效 key → HTTP 400 code 1201「Task not found」= 认证通过（视作 OK）
    #   无效 key → HTTP 401 code 1002「api key not found」= 认证失败
    base = config.get("KLING_API_BASE").rstrip("/")
    url = f"{base}/v1/videos/text2video/connectivity-probe-nonexistent"
    status, data = _get_json(url, {"Authorization": f"Bearer {key}"})
    code = data.get("code") if isinstance(data, dict) else None
    if status == 400 and code == 1201:
        # 认证通过，只是任务不存在（符合预期）——合成 200 表示 key 有效
        return url, 200, {"auth": "ok", "probe": "task-not-found（预期，未提交任务）"}, None
    return url, status, _shape(data), _err(data)


def _err(data: Any) -> str | None:
    """从响应体里提取错误信息（若有），绝不含密钥。"""
    if isinstance(data, dict):
        for k in ("error", "message", "detail", "_raw"):
            v = data.get(k)
            if isinstance(v, dict):
                return str(v.get("message") or v.get("type") or v)[:300]
            if v:
                return str(v)[:300]
    return None


#: provider → (capability, key_var, probe_fn, base_var)
#: base_var=None 表示官方托管端点（fn 自带 URL）；非 None 表示第三方网关，
#: 需要该环境变量给出网关地址，未配则跳过（不猜主机、不发请求）。
PROBES: dict[str, tuple[str, str, Callable[[str], tuple], str | None]] = {
    "openai": ("llm", "OPENAI_API_KEY", _probe_openai, None),
    "anthropic": ("llm", "ANTHROPIC_API_KEY", _probe_anthropic, None),
    "google": ("llm", "GOOGLE_API_KEY", _probe_google, None),
    "kling": ("video", "KLING_API_KEY", _probe_kling, "KLING_API_BASE"),
}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def probe(provider: str, *, timeout: int = DEFAULT_TIMEOUT) -> ProbeResult:
    """探测单个厂商。未配 key → SKIPPED（不发任何请求）。"""
    if provider not in PROBES:
        raise KeyError(f"未知厂商 {provider!r}，可选 {sorted(PROBES)}")
    capability, key_var, fn, base_var = PROBES[provider]
    key = config.get(key_var)
    result = ProbeResult(
        provider=provider, capability=capability, key_var=key_var,
        configured=bool(key),
    )
    if not key:
        result.hint = f"{key_var} 未配置，跳过（不发请求）"
        return result

    # 第三方网关必须先有网关地址，否则跳过——绝不猜主机乱发密钥
    if base_var and not config.has(base_var):
        result.status = "NO_BASE_URL"
        result.key_fingerprint = mask(key)
        result.error = f"{base_var} 未配置"
        result.hint = f"该 key 走第三方网关，请在 .env 设 {base_var} 指向网关地址后再验证。"
        return result

    result.key_fingerprint = mask(key)
    t0 = time.monotonic()
    try:
        url, http_status, shape, err = fn(key)
    except urllib.error.URLError as e:
        result.status = "NETWORK_ERROR"
        result.error = f"网络不可达: {getattr(e, 'reason', e)}"
        return result
    except Exception as e:  # noqa: BLE001 —— 探测层要兜住一切，给出可读结论
        result.status = "NETWORK_ERROR"
        result.error = f"{type(e).__name__}: {str(e)[:200]}"
        return result

    result.latency_ms = int((time.monotonic() - t0) * 1000)
    result.endpoint = url
    result.http_status = http_status

    if http_status == 200:
        result.ok = True
        result.status = "OK"
        result.response_shape = shape
    elif http_status in (401, 403):
        result.status = "AUTH_FAILED"
        result.error = err or f"HTTP {http_status}：密钥被拒"
        if base_var:
            result.hint = (
                f"网关 {config.get(base_var)} 拒绝了该 key —— 确认 key 有效、"
                f"额度未用尽。"
            )
    elif http_status in (404, 405) and base_var:
        result.status = "UNKNOWN_PROVIDER"
        result.error = f"HTTP {http_status}：网关无此端点"
        result.hint = (
            f"确认 {base_var}={config.get(base_var)} 正确，"
            f"且该网关提供 OpenAI 兼容的 /v1/models 端点。"
        )
    else:
        result.status = "HTTP_ERROR"
        result.error = err or f"HTTP {http_status}"
        result.response_shape = shape
    return result


def probe_all(*, only_configured: bool = True) -> list[dict[str, Any]]:
    """探测所有厂商。默认只测已配 key 的（未配的返回 SKIPPED 而不发请求）。"""
    out: list[ProbeResult] = []
    for name, (_, key_var, _, _) in PROBES.items():
        if only_configured and not config.has(key_var):
            r = ProbeResult(
                provider=name, capability=PROBES[name][0], key_var=key_var,
                configured=False, hint=f"{key_var} 未配置",
            )
            out.append(r)
            continue
        out.append(probe(name))
    return [r.to_dict() for r in out]


def summary() -> dict[str, Any]:
    """给看板 / CLI 的汇总。"""
    results = probe_all()
    return {
        "checked_at": _now(),
        "results": results,
        "reachable": [r["provider"] for r in results if r["ok"]],
        "configured_but_failed": [
            r["provider"] for r in results
            if r["configured"] and not r["ok"] and r["status"] != "SKIPPED"
        ],
        "not_configured": [r["provider"] for r in results if not r["configured"]],
    }


def _now() -> str:
    from cinemadna.asset_brain.common import schemas
    return schemas.utc_now_iso()


__all__ = ["probe", "probe_all", "summary", "mask", "ProbeResult", "PROBES"]
