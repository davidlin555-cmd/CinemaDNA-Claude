"""真实图像生成后端 (Phase A) —— Together FLUX 文生图，预算闸强制前置

给 IdentityDNA 出真实人脸（也可扩到场景/道具）。和 KlingBackend 同款安全约束：

1. **必须传 BudgetGate**：没有预算闸建不起来。
2. **提交前过闸**：`generate()` 第一件事是 `budget.authorize_generic(...)`。
3. **只在成功后记账**：拿到图片才 record。

图像很便宜（FLUX.1-schnell ≈ $0.003/张），但同样纳入预算保护，绝不裸奔。
HTTP 层可注入（`http=`），测试用 mock，不发真实请求、不花钱。
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

import config

from render.budget import BudgetGate

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CinemaDNA-Image/0.6"

#: FLUX.1-schnell 每张预估成本（记进预算闸；真实以账单为准）。用一个小的正数。
_IMAGE_UNIT_COST = 0.01

HttpFn = Callable[[str, str, dict[str, str], dict[str, Any] | None], "tuple[int, Any]"]


class ImageGenError(RuntimeError):
    """图像生成失败。"""


@dataclass
class ImageResult:
    prompt: str
    model: str
    file_relpath: str
    width: int
    height: int
    bytes: int
    seed: int | None = None
    is_real_media: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt, "model": self.model,
            "file_relpath": self.file_relpath, "width": self.width,
            "height": self.height, "bytes": self.bytes, "seed": self.seed,
            "is_real_media": self.is_real_media,
        }


@runtime_checkable
class ImageBackend(Protocol):
    name: str

    def generate(self, *, prompt: str, dest: Path, item_id: str,
                 width: int, height: int, seed: int | None = None) -> ImageResult:
        ...


def _real_http(method: str, url: str, headers: dict[str, str],
               body: dict[str, Any] | None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, _parse(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, _parse(e.read().decode("utf-8", "replace") if e.fp else "")


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:300]}


class TogetherImageBackend:
    """Together FLUX 文生图后端。"""

    name = "together-flux"

    def __init__(
        self,
        *,
        budget: BudgetGate,
        api_key: str | None = None,
        base: str = "https://api.together.xyz",
        model: str = "black-forest-labs/FLUX.1-schnell",
        steps: int = 4,
        http: HttpFn | None = None,
        downloader: Callable[[str, Path], None] | None = None,
        max_retries: int = 5,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(budget, BudgetGate):
            raise ImageGenError("图像后端必须传入 BudgetGate（真实付费前置保护）")
        self.budget = budget
        self.api_key = api_key or config.require("TOGETHER_API_KEY")
        self.base = base.rstrip("/")
        self.model = model
        self.steps = steps
        self._http = http or _real_http
        self._download = downloader or _download_url
        self.max_retries = max_retries
        self._sleep = sleep_fn

    def generate(self, *, prompt: str, dest: Path, item_id: str,
                 width: int = 768, height: int = 1024,
                 seed: int | None = None) -> ImageResult:
        # ① 预算闸：不过闸绝不 POST
        est = self.budget.authorize_generic(
            item_id=item_id, est_units=_IMAGE_UNIT_COST, kind="image",
            model=self.model)
        # ② 真实提交（对 429 限流做指数退避重试；不重复扣费——est 只算一次）
        body = {"model": self.model, "prompt": prompt, "width": width,
                "height": height, "steps": self.steps, "n": 1}
        if seed is not None:                # 固定种子锁脸（同角色跨镜同一张脸）
            body["seed"] = int(seed)
        headers = {"User-Agent": _UA, "Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json", "Accept": "application/json"}
        status, data = 0, None
        for attempt in range(self.max_retries):
            status, data = self._http(
                "POST", f"{self.base}/v1/images/generations", headers, body)
            if status != 429:
                break
            self._sleep(min(2 ** attempt, 10))     # 1,2,4,8,10…秒
        if status != 200:
            raise ImageGenError(f"{item_id}: 图像生成失败 HTTP {status} {_msg(data)}")
        items = (data or {}).get("data") or []
        if not items:
            raise ImageGenError(f"{item_id}: 无图像返回 {data}")
        entry = items[0]

        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("b64_json"):
            dest.write_bytes(base64.b64decode(entry["b64_json"]))
        elif entry.get("url"):
            self._download(entry["url"], dest)
        else:
            raise ImageGenError(f"{item_id}: 返回既无 url 也无 b64_json")
        size = dest.stat().st_size
        if size < 500:
            raise ImageGenError(f"{item_id}: 图像过小（{size} 字节）")

        # ③ 成功才记账
        self.budget.record(shot_id=item_id, units=est)
        return ImageResult(
            prompt=prompt, model=self.model,
            file_relpath=dest.name, width=width, height=height, bytes=size,
            seed=entry.get("seed"))


def _download_url(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as f:
        f.write(resp.read())


def _msg(data: Any) -> str:
    if isinstance(data, dict):
        e = data.get("error")
        if isinstance(e, dict):
            return str(e.get("message") or e)[:200]
        return str(data.get("message") or data.get("_raw") or data)[:200]
    return str(data)[:200]


__all__ = ["ImageBackend", "ImageResult", "ImageGenError", "TogetherImageBackend"]
