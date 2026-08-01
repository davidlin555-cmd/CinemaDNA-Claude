"""E1: RunwayActOneBackend —— 云端商业级表演驱动（首采）。

Act-One/Act-Two：角色图 + 驱动参考表演(视频/音频) → **角色演出该表演**的视频（真人级
微表情/情绪/口型）。用于库里没有该表演时的**首采**，输出既是本镜成品，也回流存库供
E2 本地复用。原始 HTTP（无 SDK 依赖），submit/poll/upload/download 均可注入 → 0 成本单测。
真实 API schema 以一次 live call 校准（RUNWAY_API_KEY 已备）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

_BASE = "https://api.dev.runwayml.com"
_VERSION = "2024-11-06"
_UNIT_COST = 1.5                # 记账用（Act-One 按秒付费，粗记）


class RunwayError(RuntimeError):
    pass


class RunwayActOneBackend:
    name = "runway-act-one"

    def __init__(self, *, api_key: str | None = None, budget: Any = None,
                 base: str = _BASE, version: str = _VERSION,
                 model: str = "act_two",
                 submit_fn: Callable[[dict], str] | None = None,
                 poll_fn: Callable[[str], tuple[bool, str | None]] | None = None,
                 upload_fn: Callable[[str], str] | None = None,
                 download_fn: Callable[[str, Path], None] | None = None,
                 sleep_fn: Callable[[float], None] = time.sleep) -> None:
        if api_key is None and submit_fn is None:
            from assets_real.image_backend import config
            api_key = config.require("RUNWAY_API_KEY")
        self.api_key = api_key
        self.budget = budget
        self.base = base.rstrip("/")
        self.version = version
        self.model = model
        self._submit = submit_fn
        self._poll = poll_fn
        self._upload = upload_fn
        self._download = download_fn
        self._sleep = sleep_fn

    def available(self) -> bool:
        return bool(self.api_key or self._submit)

    # -- 默认 HTTP 实现（可被注入覆盖） --------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "X-Runway-Version": self.version,
                "Content-Type": "application/json"}

    def _http(self, method: str, url: str, body: dict | None) -> tuple[int, Any]:
        import json as _json
        import urllib.request
        import urllib.error
        data = _json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, _json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            return e.code, {"_raw": (e.read().decode("utf-8", "replace")
                                     if e.fp else "")[:300]}

    def _do_upload(self, path: str) -> str:
        if self._upload:
            return self._upload(path)
        # Runway 接受可访问 URL 或 data URI；默认走 data URI（小图/短片）
        import base64
        b = base64.b64encode(Path(path).read_bytes()).decode()
        ext = Path(path).suffix.lstrip(".") or "png"
        mime = "video/mp4" if ext in ("mp4", "mov") else f"image/{ext}"
        return f"data:{mime};base64,{b}"

    def _do_submit(self, args: dict[str, Any]) -> str:
        if self._submit:
            return self._submit(args)
        status, data = self._http("POST", f"{self.base}/v1/character_performance", args)
        if status not in (200, 201) or not isinstance(data, dict) or not data.get("id"):
            raise RunwayError(f"Runway 提交失败 HTTP {status} {str(data)[:200]}")
        return str(data["id"])

    def _do_poll(self, task_id: str) -> tuple[bool, str | None]:
        if self._poll:
            return self._poll(task_id)
        status, data = self._http("GET", f"{self.base}/v1/tasks/{task_id}", None)
        st = (data or {}).get("status")
        if st in ("SUCCEEDED", "COMPLETE"):
            out = (data or {}).get("output") or []
            return True, (out[0] if out else None)
        if st in ("FAILED", "ERROR", "CANCELLED"):
            raise RunwayError(f"Runway 任务失败: {(data or {}).get('failure') or st}")
        return False, None

    def _do_download(self, url: str, dest: Path) -> None:
        if self._download:
            self._download(url, dest)
            return
        import urllib.request
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r:
            dest.write_bytes(r.read())

    # -- 表演驱动 -------------------------------------------------------

    def perform(self, *, character_image: str | Path, reference: str | Path,
                dest: Path, item_id: str = "act_one", reference_type: str = "video",
                character_type: str = "image", ratio: str = "720:1280",
                body_control: bool = True, expression_intensity: int = 3,
                max_polls: int = 60) -> Path:
        """角色(图/视频) + 驱动参考(视频/音频) → 角色演出视频，落 dest。付费前过预算闸。

        reference_type="audio" → 对白音频驱动 Act-Two（角色开口说话，全自动无需录真人）。
        """
        ch, ref = Path(character_image), Path(reference)
        if not ch.is_file() or not ref.is_file():
            raise RunwayError(f"{item_id}: 角色图/驱动参考缺失")
        est = None
        if self.budget is not None:
            est = self.budget.authorize_generic(
                item_id=item_id, est_units=_UNIT_COST, kind="video", model=self.name)
        args = {
            "model": self.model,
            "character": {"type": character_type, "uri": self._do_upload(str(ch))},
            "reference": {"type": reference_type, "uri": self._do_upload(str(ref))},
            "ratio": ratio,
            "bodyControl": body_control,
            "expressionIntensity": expression_intensity,
        }
        task = self._do_submit(args)
        url = None
        for _ in range(max_polls):
            self._sleep(10)
            done, u = self._do_poll(task)
            if done:
                url = u
                break
        if not url:
            raise RunwayError(f"{item_id}: Runway 轮询超时")
        self._do_download(url, dest)
        if not dest.is_file() or dest.stat().st_size < 1000:
            raise RunwayError(f"{item_id}: Runway 下载失败/过小")
        if self.budget is not None and est is not None:
            self.budget.record(shot_id=item_id, units=est)
        return dest


__all__ = ["RunwayActOneBackend", "RunwayError"]
