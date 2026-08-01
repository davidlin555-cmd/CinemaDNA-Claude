"""商业级生产就绪度审计 —— 诚实回答"能出商业级短剧了吗？"

    python scripts/check_readiness.py
    python scripts/check_readiness.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import readiness  # noqa: E402

_ICON = {"REAL": "✅", "MOCK": "🟡", "MISSING": "⛔"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    a = readiness.audit()

    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print(f" 商业级就绪度：{a['verdict']}   (commercial_ready={a['commercial_ready']})")
    print(f" 已达等级：{a['tier_reached']}  →  下一级：{a['next_tier']}")
    print(f" 能力：REAL {a['summary']['real']} / MOCK {a['summary']['mock']} / "
          f"MISSING {a['summary']['missing']}；"
          f"商业必需达标 {a['summary']['required_real']}/{a['summary']['required_total']}")
    print("=" * 68)
    print(" 就绪度阶梯：")
    for t in a["tiers"]:
        print(f"   [{'✓' if t['complete'] else ' '}] {t['tier']:14} {t['desc']}")
    print("-" * 68)
    print(" 全部能力：")
    for c in a["capabilities"]:
        req = "★" if c["commercial_required"] else " "
        print(f"   {_ICON[c['status']]} {req} {c['dimension']:4} {c['desc']:24} {c['note']}")
    print("-" * 68)
    print(" 阻塞商业级的缺口（★=商业必需）：")
    for g in a["blocking_gaps"]:
        print(f"   {_ICON[g['status']]} {g['dimension']:4} {g['desc']:24} — {g['note']}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
