"""启动 CinemaDNA Web 控制台。

    python scripts/run_webui.py
    python scripts/run_webui.py --port 8700 --workspace E:\\CinemaDNA-Claude\\workspace

打开 http://127.0.0.1:8700 即可在浏览器里完成：
查看故事状态 → 审批 Gate → 触发下一步 → 预览成片。
"""

from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path

# Enforce loading of the real API keys immediately upon start.
from dotenv import load_dotenv
load_dotenv(override=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

import config  # noqa: E402  —— 加载 .env，提供 WEB_HOST / WEB_PORT
from render.backend import ffmpeg_available  # noqa: E402
from webui.app import create_app  # noqa: E402
from webui.state import FactoryWorkspace  # noqa: E402


def main() -> int:
    # 默认值来自 .env（config.WEB_HOST/WEB_PORT）；命令行显式传入仍优先
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=config.WEB_HOST)
    ap.add_argument("--port", type=int, default=config.WEB_PORT)
    ap.add_argument("--workspace", default=None,
                    help="工作区目录（默认 <repo>/workspace）")
    ap.add_argument("--slots", type=int, default=2,
                    help="ScriptBrain 并发槽位")
    args = ap.parse_args()

    root = Path(args.workspace) if args.workspace else (
        Path(__file__).resolve().parents[1] / "workspace"
    )
    ws = FactoryWorkspace(root, max_concurrent_scripts=args.slots)
    app = create_app(ws)

    print("=" * 66)
    print(" CinemaDNA 工厂控制台")
    print("=" * 66)
    print(f" 地址      http://{args.host}:{args.port}")
    print(f" API 文档  http://{args.host}:{args.port}/docs")
    print(f" 工作区    {root}")
    print(f" 渲染后端  {'placeholder（ffmpeg 可出真实 MP4）' if ffmpeg_available() else 'mock（无 ffmpeg）'}")
    print(f" 剧本槽位  {args.slots}")
    print("=" * 66)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
