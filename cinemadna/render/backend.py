"""Render Backend —— 真实模型接入契约 (Phase 4)

本模块定义 Render Brain 与「实际出画的东西」之间的**唯一接口**。
换成任何真实视频模型（Kling / Seedance / 本地 SVD…）都只需实现
`RenderBackend`，Render Brain 的路由、参考注入、重试、一致性、
拒渲铁规、下载隔离全部不用改。

## 接口三件套

- `RenderRequest`  —— 渲染请求（由 Shot Contract 派生，字段全部显式）
- `MediaSink`      —— 产物落地口（强制 `09_downloads/<task_id>/` 隔离）
- `RenderBackend`  —— 后端协议：`name` + `capabilities` + `render()`

## 三个内置实现

| 实现                       | 出画 | 用途                                   |
|----------------------------|------|----------------------------------------|
| `MockRenderBackend`        | 否   | 纯结构闭环，最快，供单测与 CI          |
| `PlaceholderMediaBackend`  | 是   | 用 ffmpeg 出真实占位 MP4，供演示与拼接 |
| `BaseAsyncRenderBackend`   | —    | 真实云端模型的模板（提交→轮询→下载）   |

## 错误分类

`RetryableRenderError`（限流/超时/临时故障）会被 Render Brain 自动重试；
`FatalRenderError`（参数非法/内容拒绝/余额不足）不重试，直接判失败。
真实后端**必须**正确区分这两类，否则要么白等要么白烧钱。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

from asset_brain.common import schemas
from asset_brain.common.bundle import Bundle
from asset_brain.common.hashing import asset_hash_of, stable_score, stable_unit

# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


class RenderError(RuntimeError):
    """渲染失败基类。"""


class RetryableRenderError(RenderError):
    """临时性失败（限流、超时、节点抖动）—— Render Brain 会重试。"""


class FatalRenderError(RenderError):
    """确定性失败（参数非法、内容被拒、额度不足）—— 不重试。"""


# ---------------------------------------------------------------------------
# 能力声明
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackendCapabilities:
    """后端能力声明。Render Brain 在派单前据此拒绝做不到的活。"""

    max_duration_sec: float = 12.0
    min_duration_sec: float = 1.0
    resolutions: tuple[str, ...] = ("1080x1920",)
    fps_options: tuple[int, ...] = (24,)
    #: 是否支持注入参考图（人脸/场景/道具）—— 不支持就没法保证一致性
    supports_reference_images: bool = False
    #: 是否产出真实媒体文件（mock 后端为 False）
    produces_media: bool = False
    supports_audio: bool = False
    #: 是否为异步接口（提交后轮询）
    is_async: bool = False
    #: 模型自行决定输出分辨率/帧率（如 text2video）时置 True，跳过分辨率/帧率校验
    accepts_any_resolution: bool = False
    #: 产物时长与请求时长的允许偏差。真实视频模型"5秒"常出 5.1 秒，需放宽。
    duration_tolerance_sec: float = 0.05

    def check(self, request: "RenderRequest") -> list[str]:
        """返回该请求超出能力范围的原因列表（空 = 能做）。"""
        issues: list[str] = []
        if not self.min_duration_sec <= request.duration_sec <= self.max_duration_sec:
            issues.append(
                f"时长 {request.duration_sec}s 超出后端范围 "
                f"[{self.min_duration_sec}, {self.max_duration_sec}]"
            )
        if not self.accepts_any_resolution:
            if request.resolution not in self.resolutions:
                issues.append(f"分辨率 {request.resolution} 不在 {list(self.resolutions)}")
            if request.fps not in self.fps_options:
                issues.append(f"帧率 {request.fps} 不在 {list(self.fps_options)}")
        return issues


# ---------------------------------------------------------------------------
# 渲染请求
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderRequest:
    """一次渲染的完整输入。由 Shot Contract 派生，后端不需要再看合约。"""

    shot_id: str
    task_id: str
    story_id: str
    bundle_id: str
    contract_hash: str
    model: str
    tier: str
    duration_sec: float
    seed: int
    prompt: str
    negative_prompt: str = ""
    fps: int = 24
    resolution: str = "1080x1920"
    camera: dict[str, Any] = field(default_factory=dict)
    emotion: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    dialogue: list[dict[str, Any]] = field(default_factory=list)
    #: 参考资产的 hash（一致性凭证，必须原样回传到产物里）
    reference_hashes: dict[str, Any] = field(default_factory=dict)
    #: 参考资产的 ID（真实后端据此去取实际图片/3D 文件）
    reference_assets: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": schemas.SCHEMA_RENDER_PAYLOAD,
            "shot_id": self.shot_id,
            "task_id": self.task_id,
            "story_id": self.story_id,
            "bundle_id": self.bundle_id,
            "contract_hash": self.contract_hash,
            "model": self.model,
            "tier": self.tier,
            "duration_sec": self.duration_sec,
            "fps": self.fps,
            "resolution": self.resolution,
            "seed": self.seed,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "camera": self.camera,
            "emotion": self.emotion,
            "performance": self.performance,
            "dialogue": self.dialogue,
            "reference_hashes": self.reference_hashes,
            "reference_assets": self.reference_assets,
        }


# ---------------------------------------------------------------------------
# 产物落地口
# ---------------------------------------------------------------------------


class MediaSink:
    """渲染产物的落地口。**强制按 task_id 隔离**（主规格铁规）。

    后端不允许自己决定往哪写：拿到 sink，调 `reserve()` 换一个受控路径。
    """

    def __init__(self, bundle: Bundle | None) -> None:
        self.bundle = bundle

    @property
    def available(self) -> bool:
        return self.bundle is not None

    def reserve(self, task_id: str, filename: str) -> str:
        """预留一个受控相对路径（不创建文件）。"""
        if "/" in filename or "\\" in filename:
            raise FatalRenderError(f"文件名不得含路径分隔符: {filename!r}")
        return f"09_downloads/{task_id}/{filename}"

    def abs_path(self, relpath: str) -> Path:
        """把相对路径解析成绝对路径（供 ffmpeg / 下载器写入）。"""
        if self.bundle is None:
            raise FatalRenderError("当前没有 Bundle，无法落地媒体文件")
        p = self.bundle.path_for(relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_json(self, relpath: str, data: Any) -> str:
        if self.bundle is None:
            return relpath
        return self.bundle.write_json(relpath, data)

    def exists(self, relpath: str) -> bool:
        return self.bundle is not None and self.bundle.exists(relpath)


# ---------------------------------------------------------------------------
# 后端协议
# ---------------------------------------------------------------------------


@runtime_checkable
class RenderBackend(Protocol):
    """渲染后端协议。真实模型与 mock 共用同一签名。"""

    name: str
    capabilities: BackendCapabilities

    def render(self, request: RenderRequest, *, sink: MediaSink) -> dict[str, Any]:
        """执行渲染，返回 rendered_shot 结构（见 `build_rendered_shot`）。

        约定：
        - 产物必须写到 `sink.reserve(request.task_id, ...)` 给出的路径下
        - `contract_hash` / `shot_id` / `duration_sec` 必须原样回传（会被校验）
        - 临时故障抛 `RetryableRenderError`，确定性失败抛 `FatalRenderError`
        """
        ...


def build_rendered_shot(
    request: RenderRequest,
    *,
    backend: str,
    file_relpath: str,
    media_type: str,
    is_real_media: bool,
    metrics: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一构造 rendered_shot（所有后端都应通过它构造，保证结构一致）。"""
    rendered: dict[str, Any] = {
        "schema_version": schemas.SCHEMA_RENDERED_SHOT,
        "shot_id": request.shot_id,
        "task_id": request.task_id,
        "story_id": request.story_id,
        "bundle_id": request.bundle_id,
        "contract_hash": request.contract_hash,
        "model": request.model,
        "backend": backend,
        "file_relpath": file_relpath,
        "media_type": media_type,
        "is_real_media": is_real_media,
        "duration_sec": request.duration_sec,
        "fps": request.fps,
        "resolution": request.resolution,
        "seed": request.seed,
        "prompt": request.prompt,
        "reference_hashes": request.reference_hashes,
        "metrics": metrics,
        "generated_at": schemas.utc_now_iso(),
        **(extra or {}),
    }
    rendered["asset_hash"] = asset_hash_of(
        {k: v for k, v in rendered.items() if k != "generated_at"}
    )
    return rendered


def validate_rendered_shot(
    rendered: dict[str, Any], request: RenderRequest, *, duration_tolerance: float = 0.05
) -> dict[str, Any]:
    """校验后端返回的产物与请求一致 —— 防止后端串单 / 返回垃圾。

    duration_tolerance：真实视频模型"5秒"常出 5.1 秒，按后端能力放宽。
    """
    if not isinstance(rendered, dict):
        raise FatalRenderError(f"后端返回值必须是 dict，实际 {type(rendered).__name__}")
    for field_name, expected in (
        ("shot_id", request.shot_id),
        ("task_id", request.task_id),
        ("contract_hash", request.contract_hash),
    ):
        if rendered.get(field_name) != expected:
            raise FatalRenderError(
                f"后端返回的 {field_name}={rendered.get(field_name)!r} "
                f"与请求 {expected!r} 不符（串单？）"
            )
    if abs(float(rendered.get("duration_sec", 0)) - request.duration_sec) > duration_tolerance:
        raise FatalRenderError(
            f"{request.shot_id}: 后端返回时长 {rendered.get('duration_sec')}s "
            f"与合约 {request.duration_sec}s 不符"
        )
    if not rendered.get("file_relpath", "").startswith(f"09_downloads/{request.task_id}/"):
        raise FatalRenderError(
            f"{request.shot_id}: 产物路径 {rendered.get('file_relpath')!r} "
            f"未按 task_id 隔离"
        )
    if not rendered.get("asset_hash", "").startswith("sha256:"):
        raise FatalRenderError(f"{request.shot_id}: 产物缺少合法 asset_hash")
    return rendered


def seed_from(contract_hash: str, shot_id: str) -> int:
    """由合约 hash 派生渲染种子：同一合约永远同一 seed，可复现。"""
    return int(stable_unit(contract_hash, shot_id) * 2**31)


def mock_metrics(seed: int) -> dict[str, Any]:
    """mock 质量指标。确定性伪随机，**不具备真实质量评估意义**。"""
    return {
        "identity_consistency": stable_score(0.82, 0.97, seed, "identity"),
        "scene_consistency": stable_score(0.80, 0.96, seed, "scene"),
        "motion_quality": stable_score(0.70, 0.93, seed, "motion"),
        "mock": True,
    }


# ---------------------------------------------------------------------------
# 实现 1：纯结构 mock（不出画）
# ---------------------------------------------------------------------------


class MockRenderBackend:
    """只产出结构清单，不产出任何画面。CI 与单测默认用它。"""

    name = "mock"
    capabilities = BackendCapabilities(
        max_duration_sec=12.0,
        supports_reference_images=True,
        produces_media=False,
    )

    def __init__(
        self,
        *,
        fail_shots: set[str] | None = None,
        flaky_shots: set[str] | None = None,
    ) -> None:
        #: 必然致命失败的镜头（验证失败路径）
        self.fail_shots = set(fail_shots or ())
        #: 第一次失败、重试成功的镜头（验证重试路径）
        self.flaky_shots = set(flaky_shots or ())
        self.calls: list[str] = []
        self._attempts: dict[str, int] = {}

    def render(self, request: RenderRequest, *, sink: MediaSink) -> dict[str, Any]:
        self.calls.append(request.shot_id)
        n = self._attempts.get(request.shot_id, 0) + 1
        self._attempts[request.shot_id] = n

        if request.shot_id in self.fail_shots:
            raise FatalRenderError(f"{request.shot_id}: mock 致命失败（人为注入）")
        if request.shot_id in self.flaky_shots and n == 1:
            raise RetryableRenderError(f"{request.shot_id}: mock 限流（人为注入）")

        relpath = sink.reserve(request.task_id, f"{request.shot_id}.mock.json")
        rendered = build_rendered_shot(
            request,
            backend=self.name,
            file_relpath=relpath,
            media_type="mock/none",     # 诚实标注：这里没有一帧画面
            is_real_media=False,
            metrics=mock_metrics(request.seed),
            extra={"attempts": n},
        )
        sink.write_json(relpath, rendered)
        return rendered


# ---------------------------------------------------------------------------
# 实现 2：占位媒体（真出 MP4，用于演示与拼接）
# ---------------------------------------------------------------------------

_FFMPEG: Final = shutil.which("ffmpeg")
_DEFAULT_FONTS: Final[tuple[str, ...]] = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)

#: 镜头类型 → 板色（不同景别一眼可辨）
_SLATE_COLORS: Final[dict[str, str]] = {
    "ESTABLISHING": "0x1b263b",
    "MEDIUM": "0x2b2d42",
    "CLOSEUP": "0x3d2c3f",
    "REACTION": "0x2f3e46",
    "INSERT": "0x40342b",
}


def ffmpeg_available() -> bool:
    return _FFMPEG is not None


def _find_font() -> str | None:
    for f in _DEFAULT_FONTS:
        if Path(f).exists():
            return f
    return None


def _esc(path: str) -> str:
    """ffmpeg filter 里的路径转义：反斜杠转正斜杠，冒号加反斜杠。"""
    return str(path).replace("\\", "/").replace(":", r"\:")


class PlaceholderMediaBackend:
    """用 ffmpeg 生成**真实可播放**的占位 MP4（色板 + 文字信息）。

    它仍然不是"生成的画面"：没有模型参与，画面是纯色板加字。
    但它是真实媒体文件，因此可以真的拼接、真的播放、真的量时长 ——
    用来验证"拼接与出片链路"本身是否成立。
    """

    name = "placeholder-media"
    capabilities = BackendCapabilities(
        max_duration_sec=12.0,
        supports_reference_images=False,   # 色板不吃参考图
        produces_media=True,
    )

    def __init__(self, *, font: str | None = None, resolution: str = "1080x1920",
                 timeout_sec: int = 120) -> None:
        if not ffmpeg_available():
            raise FatalRenderError("未找到 ffmpeg，无法产出占位媒体")
        self.font = font or _find_font()
        self.resolution = resolution
        self.timeout_sec = timeout_sec
        self.calls: list[str] = []

    def _slate_text(self, request: RenderRequest) -> str:
        cam = request.camera
        line = request.dialogue[0]["line"] if request.dialogue else ""
        beats = request.performance.get("acting_beats") or [{}]
        expr = (beats[0].get("micro_expression") or {}).get("primary", "")
        return "\n".join(
            [
                request.shot_id,
                f"{cam.get('shot_size', '')}  {cam.get('movement', '')}  "
                f"{cam.get('angle', '')}",
                f"{request.duration_sec:.1f}s   {request.model}",
                "",
                request.prompt[:38],
                "",
                f"「{line}」" if line else "（无台词）",
                expr[:24],
            ]
        )

    def render(self, request: RenderRequest, *, sink: MediaSink) -> dict[str, Any]:
        self.calls.append(request.shot_id)
        if not sink.available:
            raise FatalRenderError("PlaceholderMediaBackend 需要 Bundle 才能落地文件")

        relpath = sink.reserve(request.task_id, f"{request.shot_id}.mp4")
        out = sink.abs_path(relpath)
        color = _SLATE_COLORS.get(
            (request.camera or {}).get("shot_type", ""), None
        ) or _SLATE_COLORS.get(request.emotion.get("shot_type", ""), "0x22223b")

        with tempfile.TemporaryDirectory() as td:
            txt = Path(td) / "slate.txt"
            txt.write_text(self._slate_text(request), encoding="utf-8")

            vf = (
                f"drawtext=textfile='{_esc(txt)}'"
                f":fontcolor=white:fontsize=44:line_spacing=18"
                f":x=(w-text_w)/2:y=(h-text_h)/2"
            )
            if self.font:
                vf += f":fontfile='{_esc(self.font)}'"

            cmd = [
                _FFMPEG, "-y", "-loglevel", "error",
                "-f", "lavfi",
                "-i", f"color=c={color}:s={self.resolution}:r={request.fps}"
                      f":d={request.duration_sec}",
                "-vf", vf,
                "-t", f"{request.duration_sec}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(out),
            ]
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=self.timeout_sec
                )
            except subprocess.TimeoutExpired as e:
                raise RetryableRenderError(f"{request.shot_id}: ffmpeg 超时") from e
            if proc.returncode != 0:
                raise FatalRenderError(
                    f"{request.shot_id}: ffmpeg 失败 -> {proc.stderr.strip()[:300]}"
                )

        metrics = mock_metrics(request.seed)
        metrics["media_bytes"] = out.stat().st_size
        return build_rendered_shot(
            request,
            backend=self.name,
            file_relpath=relpath,
            media_type="video/mp4",
            # 是真实文件，但不是"模型生成的画面"——两件事分开标注
            is_real_media=True,
            metrics=metrics,
            extra={"is_generated_footage": False, "placeholder": True},
        )


# ---------------------------------------------------------------------------
# 实现 3：真实云端模型的模板
# ---------------------------------------------------------------------------


class BaseAsyncRenderBackend:
    """真实异步模型后端的骨架：提交 → 轮询 → 下载 → 组装产物。

    接真实模型时继承本类，只实现三个抽象点：

        class KlingBackend(BaseAsyncRenderBackend):
            name = "cloud-face-v1"
            capabilities = BackendCapabilities(
                max_duration_sec=10, supports_reference_images=True,
                produces_media=True, is_async=True,
            )
            def submit(self, request): ...      # 调 API，返回 job_id
            def poll(self, job_id): ...         # 返回 (done, media_url) 或抛错
            def download(self, url, dest): ...  # 下载到 dest（绝对路径）

    重试、限流、路径隔离、产物结构、hash 绑定都由基类处理，
    子类只关心与厂商 API 的对话。
    """

    name = "base-async"
    capabilities = BackendCapabilities(is_async=True, produces_media=True)
    poll_interval_sec: float = 3.0
    max_poll_attempts: int = 100
    media_suffix: str = ".mp4"
    media_type: str = "video/mp4"

    # -- 子类必须实现 ---------------------------------------------------

    def submit(self, request: RenderRequest) -> str:
        raise NotImplementedError("真实后端必须实现 submit()")

    def poll(self, job_id: str) -> tuple[bool, str | None]:
        raise NotImplementedError("真实后端必须实现 poll()")

    def download(self, url: str, dest: Path) -> None:
        raise NotImplementedError("真实后端必须实现 download()")

    def sleep(self, seconds: float) -> None:
        """默认不真的睡（便于测试）；真实实现应 time.sleep。"""
        return None

    # -- 基类流程 -------------------------------------------------------

    #: 已提交任务缓存：task_id → job_id。**防止重试重复提交（重复扣费）**。
    _submitted_jobs: dict[str, str]

    def _resume_or_submit(self, request: RenderRequest) -> str:
        """已为该 task_id 提交过就复用 job_id，否则提交一次。

        service 层的重试会重新调 render()；若每次都 submit()，付费后端就会
        **重复提交、重复扣费**。这里按 task_id 缓存，保证一个镜头只提交一次，
        重试只恢复轮询/下载。
        """
        cache = self.__dict__.setdefault("_submitted_jobs", {})
        key = request.task_id
        if key in cache:
            return cache[key]
        job_id = self.submit(request)
        cache[key] = job_id
        return job_id

    def render(self, request: RenderRequest, *, sink: MediaSink) -> dict[str, Any]:
        issues = self.capabilities.check(request)
        if issues:
            raise FatalRenderError(f"{request.shot_id}: 超出后端能力 -> {issues}")
        if not sink.available:
            raise FatalRenderError("真实后端需要 Bundle 才能落地下载文件")

        job_id = self._resume_or_submit(request)
        url: str | None = None
        for _ in range(self.max_poll_attempts):
            done, url = self.poll(job_id)
            if done:
                break
            self.sleep(self.poll_interval_sec)
        else:
            raise RetryableRenderError(
                f"{request.shot_id}: 轮询 {self.max_poll_attempts} 次仍未完成"
            )
        if not url:
            raise FatalRenderError(f"{request.shot_id}: 任务完成但没有产物地址")

        relpath = sink.reserve(request.task_id, f"{request.shot_id}{self.media_suffix}")
        self.download(url, sink.abs_path(relpath))

        return build_rendered_shot(
            request,
            backend=self.name,
            file_relpath=relpath,
            media_type=self.media_type,
            is_real_media=True,
            metrics={"source_job_id": job_id, "mock": False},
            extra={"is_generated_footage": True, "job_id": job_id},
        )
