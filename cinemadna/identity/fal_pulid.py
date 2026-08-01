"""FAL PuLID-FLUX 后端 —— ID 条件生成（云端，躲本地 GPU 重启风险）。

给一张**锚脸** + 提示词 → 生成保持同一身份的图（任意景别/角度/表情）。用于合成身份
铸造：每角色一张 novel 合成锚脸，所有镜的首帧都由 PuLID 锁这张脸 → **跨镜真同脸**
（根治漂移）+ 自然（FLUX 脸质优于 Kling）+ novel 人（避肖像权）。

云端 API（fal-ai/flux-pulid），FAL_KEY 从 config 注入。可注入 run/upload/download 做
0 成本单测。付费前经 BudgetGate（每图约 $0.02-0.05）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

ENDPOINT = "fal-ai/flux-pulid"
_IMG_UNIT_COST = 0.05           # 记账用（PuLID-FLUX 每图约几分钱）


class FalPuLIDError(RuntimeError):
    pass


class FalPuLIDBackend:
    """PuLID-FLUX ID 条件生成后端。"""

    name = "fal-flux-pulid"

    def __init__(self, *, api_key: str | None = None, budget: Any = None,
                 run_fn: Callable[..., dict] | None = None,
                 upload_fn: Callable[[str], str] | None = None,
                 download_fn: Callable[[str, Path], None] | None = None) -> None:
        if api_key is None and run_fn is None:
            from assets_real.image_backend import config
            api_key = config.require("FAL_KEY")
        if api_key:
            os.environ.setdefault("FAL_KEY", api_key)
        self.budget = budget
        self._run = run_fn
        self._upload = upload_fn
        self._download = download_fn

    # -- 可注入的三个 IO（默认走 fal_client / urllib） -----------------

    def _do_upload(self, path: str) -> str:
        if self._upload:
            return self._upload(path)
        import fal_client
        return fal_client.upload_file(path)

    def _do_run(self, args: dict[str, Any]) -> dict[str, Any]:
        if self._run:
            return self._run(args)
        import fal_client
        return fal_client.subscribe(ENDPOINT, arguments=args, with_logs=False)

    def _do_download(self, url: str, dest: Path) -> None:
        if self._download:
            self._download(url, dest)
            return
        import urllib.request
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=180) as r:
            dest.write_bytes(r.read())

    # -- 生成 -----------------------------------------------------------

    def generate(self, *, reference_face: str | Path, prompt: str, dest: Path,
                 item_id: str = "pulid", width: int = 768, height: int = 1360,
                 id_weight: float = 1.0, num_steps: int = 20,
                 negative: str = "") -> Path:
        """锚脸 + 提示词 → 同身份图，落到 dest。付费前过预算闸。"""
        ref = Path(reference_face)
        if not ref.is_file():
            raise FalPuLIDError(f"{item_id}: 锚脸不存在 {ref}")
        est = None
        if self.budget is not None:
            est = self.budget.authorize_generic(
                item_id=item_id, est_units=_IMG_UNIT_COST, kind="image",
                model=self.name)
        ref_url = self._do_upload(str(ref))
        args = {
            "prompt": prompt, "reference_image_url": ref_url,
            "image_size": {"width": width, "height": height},
            "num_inference_steps": num_steps, "guidance_scale": 4.0,
            "id_weight": id_weight, "num_images": 1, "enable_safety_checker": True,
        }
        if negative:
            args["negative_prompt"] = negative
        data = self._do_run(args)
        images = (data or {}).get("images") or []
        if not images or not images[0].get("url"):
            raise FalPuLIDError(f"{item_id}: PuLID 无图返回 {str(data)[:200]}")
        self._do_download(images[0]["url"], dest)
        if not dest.is_file() or dest.stat().st_size < 500:
            raise FalPuLIDError(f"{item_id}: PuLID 下载失败/过小")
        if self.budget is not None and est is not None:
            self.budget.record(shot_id=item_id, units=est)
        return dest


__all__ = ["FalPuLIDBackend", "FalPuLIDError", "ENDPOINT"]
