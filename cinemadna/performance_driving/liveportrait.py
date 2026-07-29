"""E2: LivePortraitBackend —— 本地表演复用（$0，从库调驱动 take 动画化角色脸）。

LivePortrait：**源肖像 + 驱动视频 → 按驱动的表情/头动动画化源脸(保源身份)**。用于库里
已有该表演时的本地 $0 复用（把库中驱动 take 套到当前角色 PuLID 脸）。隔离本地安装，
subprocess 调；未装则 available()=False → 服务退回 E1 云或诚实标记（绝不冒充）。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable


class LivePortraitBackend:
    real = True
    name = "liveportrait"

    def __init__(self, *, root: str | Path | None = None,
                 python: str | Path | None = None,
                 runner: Callable[[list[str], Path], int] | None = None) -> None:
        self.root = Path(root or os.environ.get(
            "LIVEPORTRAIT_ROOT", r"E:/CinemaDNA/tools/LivePortrait"))
        self.python = Path(python) if python else self.root / ".venv/Scripts/python.exe"
        self._runner = runner or self._default_runner

    def _default_runner(self, argv: list[str], cwd: Path) -> int:
        return subprocess.run(argv, cwd=str(cwd), timeout=1200).returncode

    def available(self) -> bool:
        return (self.python.is_file()
                and (self.root / "inference.py").is_file())

    def animate(self, *, source: Path, driving: Path, dest: Path) -> dict[str, Any]:
        """源肖像 source + 驱动视频 driving → 动画化视频 dest。"""
        if not self.available():
            return {"ok": False, "error": "LivePortrait 未就绪（未装）"}
        if not Path(source).is_file() or not Path(driving).is_file():
            return {"ok": False, "error": "源肖像/驱动视频缺失"}
        dest.parent.mkdir(parents=True, exist_ok=True)
        argv = [str(self.python), "inference.py",
                "-s", str(Path(source).resolve()),
                "-d", str(Path(driving).resolve()),
                "-o", str(Path(dest).resolve())]
        try:
            rc = self._runner(argv, self.root)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}
        ok = rc == 0 and dest.is_file() and dest.stat().st_size > 1000
        return {"ok": ok, "relpath": dest.name, "backend": self.name,
                "note": "LivePortrait 本地复用驱动" if ok else f"失败(rc={rc})"}


__all__ = ["LivePortraitBackend"]
