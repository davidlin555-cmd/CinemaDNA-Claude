"""屏幕内容道具渲染后端 (Phase 2b) —— 用无头 Chrome 把 HTML 模板截成真实图。

短剧最高频的道具是"屏幕内容"：手机界面 / 合同条款 / 付款记录 / 录音界面。
纯 text2image（FLUX）出这类道具时屏内文字必然乱码或空白，违反 qa_contract 的
"道具清晰可读、无乱码"。这里改用**模板截图合成**：

    结构化内容 → HTML/CSS 模板 → 无头 Chrome --screenshot → 真实 PNG

文字是浏览器矢量渲染，天然清晰可读、零乱码、完全确定（同输入同输出）。
本地渲染**不联网、不花钱**，因此不需要 BudgetGate（与云端 FLUX 相反）。

Chrome 对 Unicode 路径（如 `已读.png`）在个别平台不稳，所以一律先渲染到 ASCII
临时路径，再由 Python 移动到最终（可能含中文）路径，彻底绕开该坑。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

#: Windows 常见的 Chrome / Edge 安装位置（都支持 --headless --screenshot）
_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


class ScreenRenderError(RuntimeError):
    """屏幕道具渲染失败。"""


class ScreenResult:
    def __init__(self, *, kind: str, file_relpath: str, width: int, height: int,
                 bytes: int) -> None:
        self.kind = kind
        self.file_relpath = file_relpath
        self.width = width
        self.height = height
        self.bytes = bytes
        self.is_real_media = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "model": "chrome-headless-template",
            "file_relpath": self.file_relpath,
            "width": self.width, "height": self.height, "bytes": self.bytes,
            "is_real_media": True,
            "generation_method": "template_screenshot",
        }


#: runner 签名：接收 argv 列表，跑完不抛即视为成功（供测试注入假 runner）
RunnerFn = Callable[[list[str]], None]


class ChromeScreenshotBackend:
    """把 HTML 字符串用无头 Chrome 截成 PNG。本地、免费、确定。"""

    def __init__(
        self,
        chrome_path: str | None = None,
        *,
        runner: RunnerFn | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.chrome_path = chrome_path or self._autodetect()
        self._runner = runner or self._default_runner
        self.timeout = timeout

    @staticmethod
    def _autodetect() -> str | None:
        for p in _CHROME_CANDIDATES:
            if Path(p).is_file():
                return p
        # PATH 上的 chrome/chromium（Linux/Mac）
        for exe in ("chrome", "chromium", "chromium-browser", "google-chrome"):
            found = shutil.which(exe)
            if found:
                return found
        return None

    def available(self) -> bool:
        """有可用浏览器才算就绪（否则屏幕道具应降级并如实标记）。"""
        return bool(self.chrome_path)

    def _default_runner(self, argv: list[str]) -> None:
        subprocess.run(argv, timeout=self.timeout, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def render(self, *, html: str, dest: Path, kind: str = "screen",
               width: int = 768, height: int = 1024) -> ScreenResult:
        """把 html 渲染成 dest（PNG）。dest 可含中文名，内部走 ASCII 中转。"""
        if not self.available():
            raise ScreenRenderError("未找到可用的 Chrome/Edge，无法渲染屏幕道具")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # ASCII 中转名（避免 Chrome 对中文路径不稳）；同目录，去重靠内容 hash
        stem = hashlib.sha1(
            f"{dest}|{kind}".encode("utf-8")).hexdigest()[:16]
        src = dest.parent / f"_screensrc_{stem}.html"
        tmp_png = dest.parent / f"_screenshot_{stem}.png"
        src.write_text(html, encoding="utf-8")
        try:
            argv = [
                self.chrome_path, "--headless=new", "--disable-gpu",
                "--hide-scrollbars", "--force-device-scale-factor=1",
                "--default-background-color=00000000",
                f"--window-size={width},{height}",
                f"--screenshot={tmp_png}",
                src.resolve().as_uri(),
            ]
            self._runner(argv)
            if not tmp_png.is_file() or tmp_png.stat().st_size < 500:
                raise ScreenRenderError(f"{kind}: Chrome 未产出有效截图")
            os.replace(tmp_png, dest)          # 移到最终（可能含中文）路径
        finally:
            for f in (src, tmp_png):
                try:
                    if f.exists():
                        f.unlink()
                except OSError:
                    pass
        size = dest.stat().st_size
        return ScreenResult(kind=kind, file_relpath=dest.name,
                            width=width, height=height, bytes=size)


__all__ = ["ChromeScreenshotBackend", "ScreenResult", "ScreenRenderError"]
