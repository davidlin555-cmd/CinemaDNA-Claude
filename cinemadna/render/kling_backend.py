"""KlingTextToVideoBackend (Phase 6) —— 接真实 Kling 出片，预算闸强制前置

对应架构复盘 §8 第 2 步：把已验证可用的 Kling（api-singapore.klingai.com）
写成 `BaseAsyncRenderBackend` 子类，证明"合约 → 真实视频 → 拼接"整条真链路。

## 硬性安全约束

1. **必须传 BudgetGate**：`__init__` 无预算闸直接报错。没有预算闸就没有真实提交。
2. **提交前过闸**：`submit()` 的第一件事是 `budget.authorize(...)`，不过闸不 POST。
3. **只在提交成功后记账**：拿到 task_id 才 `budget.record(...)`；被拒（400）不记账。
4. **只支持 text2video + 5 秒**：先打通最小链路，成功后再扩展（image2video 需真实参考图）。

## 认证与端点（已实测确认）

- 认证：`Authorization: Bearer <KLING_API_KEY>`
- 提交：`POST {base}/v1/videos/text2video`，body `{model_name, prompt, aspect_ratio}`
- 轮询：`GET  {base}/v1/videos/text2video/{task_id}`，`task_status ∈ submitted/processing/succeed/failed`
- 产物：`data.task_result.videos[0].url`（Kling CDN，带签名有效期）

HTTP 层可注入（`http=`），便于测试用 mock 而不发真实网络请求。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import config

from .backend import (
    BackendCapabilities,
    BaseAsyncRenderBackend,
    FatalRenderError,
    RenderRequest,
    RetryableRenderError,
)
from .budget import BudgetGate

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CinemaDNA-Render/0.6"

#: HTTP 调用签名：(method, url, headers, json_body|None) → (status, parsed_json)
HttpFn = Callable[[str, str, dict[str, str], dict[str, Any] | None], "tuple[int, Any]"]


def _real_http(
    method: str, url: str, headers: dict[str, str], body: dict[str, Any] | None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, _parse(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read().decode("utf-8", "replace") if e.fp else "")


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:300]}


class KlingTextToVideoBackend(BaseAsyncRenderBackend):
    """Kling 文生视频后端（5 秒最小链路，预算闸强制前置）。"""

    name = "kling-text2video"
    poll_interval_sec = 6.0
    max_poll_attempts = 60  # 6s × 60 = 6 分钟
    media_suffix = ".mp4"
    media_type = "video/mp4"

    def __init__(
        self,
        *,
        budget: BudgetGate,
        api_key: str | None = None,
        base: str | None = None,
        model_name: str = "kling-v1",
        mode: str = "std",
        aspect_ratio: str = "9:16",
        http: HttpFn | None = None,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(budget, BudgetGate):
            raise FatalRenderError("KlingBackend 必须传入 BudgetGate（无预算闸不允许真实提交）")
        self.budget = budget
        self.api_key = api_key or config.require("KLING_API_KEY")
        self.base = (base or config.require("KLING_API_BASE")).rstrip("/")
        self.model_name = model_name
        self.mode = mode
        self.aspect_ratio = aspect_ratio
        self._http = http or _real_http
        self._sleep = sleep_fn
        # 输出由模型决定分辨率/帧率；"5秒"实际约 5.1 秒，放宽时长校验
        self.capabilities = BackendCapabilities(
            min_duration_sec=5.0,
            max_duration_sec=10.0,
            supports_reference_images=False,
            produces_media=True,
            is_async=True,
            accepts_any_resolution=True,
            duration_tolerance_sec=0.6,
        )
        self.last_task_id: str | None = None

    # -- 请求头 ---------------------------------------------------------

    def _headers(self, *, json_body: bool = False) -> dict[str, str]:
        h = {"User-Agent": _UA, "Authorization": f"Bearer {self.api_key}",
             "Accept": "application/json"}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    # -- BaseAsyncRenderBackend 三件套 ---------------------------------

    def submit(self, request: RenderRequest) -> str:
        # ① 预算闸：不过闸绝不 POST（这是全模块最关键的一行顺序）
        est = self.budget.authorize(
            shot_id=request.shot_id,
            duration_sec=request.duration_sec,
            model_name=self.model_name,
            mode=self.mode,
        )
        # ② 真实提交
        body = {
            "model_name": self.model_name,
            "prompt": request.prompt[:2500],
            "aspect_ratio": self.aspect_ratio,
        }
        status, data = self._http(
            "POST", f"{self.base}/v1/videos/text2video",
            self._headers(json_body=True), body,
        )
        code = data.get("code") if isinstance(data, dict) else None
        if status != 200 or code != 0:
            # 提交被拒（参数非法/被拦），不记账、不重试
            raise FatalRenderError(
                f"{request.shot_id}: Kling 提交失败 HTTP {status} "
                f"code={code} msg={_msg(data)}"
            )
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise FatalRenderError(f"{request.shot_id}: 提交成功但无 task_id: {data}")
        # ③ 提交成功才记账
        self.budget.record(shot_id=request.shot_id, units=est)
        self.last_task_id = task_id
        return str(task_id)

    def poll(self, job_id: str) -> tuple[bool, str | None]:
        status, data = self._http(
            "GET", f"{self.base}/v1/videos/text2video/{job_id}",
            self._headers(), None,
        )
        if status >= 500:
            raise RetryableRenderError(f"轮询 {job_id} 服务端错误 HTTP {status}")
        d = (data.get("data") or {}) if isinstance(data, dict) else {}
        st = d.get("task_status")
        if st == "succeed":
            videos = (d.get("task_result") or {}).get("videos") or []
            if not videos or not videos[0].get("url"):
                raise FatalRenderError(f"{job_id}: 成功但无视频 URL")
            return True, videos[0]["url"]
        if st == "failed":
            raise FatalRenderError(
                f"{job_id}: Kling 生成失败 {d.get('task_status_msg') or ''}"
            )
        return False, None  # submitted / processing

    def download(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
                f.write(resp.read())
        except urllib.error.URLError as e:
            raise RetryableRenderError(f"下载失败: {e}") from e
        if dest.stat().st_size < 1000:
            raise FatalRenderError(f"下载的视频过小（{dest.stat().st_size} 字节）")

    def sleep(self, seconds: float) -> None:
        if self._sleep is not None:
            self._sleep(seconds)
        # 默认不真的睡（测试友好）；真实使用可传 sleep_fn=time.sleep


class KlingImageToVideoBackend(KlingTextToVideoBackend):
    """Kling 图生视频后端 —— 用真实人脸参考图驱动，保证跨镜头角色一致。

    与 text2video 唯一区别：提交时带一张参考图（合约里人物资产的 base_face_image，
    base64 编码）。同一角色的所有镜头用同一张脸 → 角色一致性有根。

    合约里若没有真实人脸图，直接 FatalRenderError（不允许"假装 image2video"）。
    """

    name = "kling-image2video"

    def __init__(self, *, bundle_root: Path | None = None, **kw) -> None:
        super().__init__(**kw)
        # capabilities 是 frozen dataclass，重建一个（标注支持参考图）
        self.capabilities = BackendCapabilities(
            min_duration_sec=5.0, max_duration_sec=10.0,
            supports_reference_images=True, produces_media=True, is_async=True,
            accepts_any_resolution=True, duration_tolerance_sec=0.6)
        #: 解析合约里 base_face_image 相对路径用的 Bundle 根
        self._bundle_root = Path(bundle_root) if bundle_root else None

    def _reference_image_b64(self, request: RenderRequest) -> str:
        refs = request.reference_assets or {}
        face_rel = (refs.get("character_images") or {})
        # 取第一个人物的真实人脸图
        for cid, rel in face_rel.items():
            if rel:
                path = self._resolve_image(request, rel)
                import base64
                return base64.b64encode(path.read_bytes()).decode()
        raise FatalRenderError(
            f"{request.shot_id}: image2video 需要真实人脸参考图，但合约里没有"
            f"（先跑 IdentityDNA 真实出图）")

    def _tail_image_b64(self, request: RenderRequest) -> str | None:
        """起帧+尾帧驱动的尾帧（默认无；场景内首帧后端在 foundry 动作镜时覆盖）。"""
        return None

    def _resolve_image(self, request: RenderRequest, relpath: str) -> Path:
        if self._bundle_root is not None:
            p = (self._bundle_root / request.bundle_id / relpath)
            if p.is_file():
                return p
        p = Path(relpath)
        if p.is_file():
            return p
        raise FatalRenderError(f"{request.shot_id}: 找不到参考图 {relpath}")

    def submit(self, request: RenderRequest) -> str:
        est = self.budget.authorize(
            shot_id=request.shot_id, duration_sec=request.duration_sec,
            model_name=self.model_name, mode=self.mode)
        image_b64 = self._reference_image_b64(request)
        # ②参考锁：主角镜必须有参考图（禁纯 T2V）；缺参考锚 → 拒渲（跨镜必变脸）
        from .reference_lock import enforce_reference_lock
        image_b64 = enforce_reference_lock(request, image_b64)
        body = {
            "model_name": self.model_name,
            "image": image_b64,
            "prompt": request.prompt[:2500],
        }
        # A·起帧+尾帧：有尾帧则起/末帧插值 → 动作真发生+表情弧线+镜内更连贯
        tail_b64 = self._tail_image_b64(request)
        if tail_b64:
            body["image_tail"] = tail_b64
        status, data = self._http(
            "POST", f"{self.base}/v1/videos/image2video",
            self._headers(json_body=True), body)
        code = data.get("code") if isinstance(data, dict) else None
        if status != 200 or code != 0:
            raise FatalRenderError(
                f"{request.shot_id}: Kling image2video 提交失败 HTTP {status} "
                f"code={code} msg={_msg(data)}")
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise FatalRenderError(f"{request.shot_id}: 提交成功但无 task_id")
        self.budget.record(shot_id=request.shot_id, units=est)
        self.last_task_id = task_id
        return str(task_id)

    def poll(self, job_id: str) -> tuple[bool, str | None]:
        status, data = self._http(
            "GET", f"{self.base}/v1/videos/image2video/{job_id}",
            self._headers(), None)
        if status >= 500:
            raise RetryableRenderError(f"轮询 {job_id} 服务端错误 HTTP {status}")
        d = (data.get("data") or {}) if isinstance(data, dict) else {}
        st = d.get("task_status")
        if st == "succeed":
            videos = (d.get("task_result") or {}).get("videos") or []
            if not videos or not videos[0].get("url"):
                raise FatalRenderError(f"{job_id}: 成功但无视频 URL")
            return True, videos[0]["url"]
        if st == "failed":
            raise FatalRenderError(
                f"{job_id}: Kling 生成失败 {d.get('task_status_msg') or ''}")
        return False, None


def _msg(data: Any) -> str:
    if isinstance(data, dict):
        return str(data.get("message") or data.get("_raw") or data)[:200]
    return str(data)[:200]


__all__ = ["KlingTextToVideoBackend", "KlingImageToVideoBackend"]
