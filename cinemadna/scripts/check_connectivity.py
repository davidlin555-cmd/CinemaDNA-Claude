"""对已配置的真实 API Key 做最小连通性验证。

    python scripts/check_connectivity.py            # 测所有已配 key
    python scripts/check_connectivity.py openai     # 只测某一家

只对已配置的 key 发请求，只打只读 / 免费端点（列模型），
绝不提交视频生成等计费任务，输出绝不含密钥值（只有掩码指纹）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
import connectivity  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", nargs="?", help="只测某一家（openai/anthropic/google/kling）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    if args.json:
        data = (
            {"results": [connectivity.probe(args.provider).to_dict()]}
            if args.provider else connectivity.summary()
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print("=" * 70)
    print(" API 连通性验证（只读端点，不触发计费；输出不含密钥值）")
    print("=" * 70)
    cfg = config.describe()
    print(f" 环境: {cfg['env']}   .env 已加载: {cfg['dotenv_loaded']}")
    print("-" * 70)

    results = (
        [connectivity.probe(args.provider).to_dict()]
        if args.provider else connectivity.summary()["results"]
    )

    icon = {"OK": "✅", "AUTH_FAILED": "🔑", "UNKNOWN_PROVIDER": "❓",
            "HTTP_ERROR": "⚠️", "NETWORK_ERROR": "🌐", "SKIPPED": "⏭️"}
    ok = 0
    for r in results:
        mark = icon.get(r["status"], "•")
        print(f"\n{mark} {r['provider']:10} [{r['capability']}]  {r['status']}")
        if not r["configured"]:
            print(f"   {r['key_var']} 未配置（跳过，未发请求）")
            continue
        print(f"   指纹 {r['key_fingerprint']}")
        if r["endpoint"]:
            print(f"   端点 {r['endpoint']}  →  HTTP {r['http_status']}  ({r['latency_ms']}ms)")
        if r["ok"]:
            ok += 1
            print(f"   响应结构 {json.dumps(r['response_shape'], ensure_ascii=False)}")
        if r["error"]:
            print(f"   错误 {r['error']}")
        if r["hint"]:
            print(f"   提示 {r['hint']}")

    print("\n" + "=" * 70)
    print(f" 成功 {ok} 家；未配置的已跳过。密钥值全程未打印。")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
