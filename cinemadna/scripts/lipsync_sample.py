"""给已渲样片补真口型(LatentSync 本地 GPU, $0 API) —— 现片硬门槛。

对每个**说话镜**用其干净对白音频给渲染片重绘口型 → 换成对口型版 → 更新 render payload,
之后 plan_d_refinish 重装配即用对口型版。诚实:只有 LatentSync 真跑成功才换片;失败镜保留
原片并记录(不冒充已对齐)。

    python scripts/lipsync_sample.py
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio.lipsync import LatentSyncBackend  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "workspace_plan_d"
BID = "bundle_20260724_001"


def main() -> int:
    root = OUT / BID
    lip = LatentSyncBackend()
    print(f"LatentSync available={lip.available()} root={lip.root}")
    if not lip.available():
        print("LatentSync 未就绪，终止（不冒充对齐）")
        return 1

    synced, failed = [], []
    for f in sorted(glob.glob(str(root / "06_shots/contracts/*.json"))):
        c = json.load(open(f, encoding="utf-8"))
        sid = c["shot_id"]
        dlg = c.get("dialogue") or []
        if not dlg:
            continue
        # 干净对白音频(每镜一句)
        auds = sorted(glob.glob(str(root / f"05_performance/audio/{sid}/*.mp3")))
        pay = root / f"07_payloads/render/{sid}.json"
        if not auds or not pay.is_file():
            print(f"  {sid}: 缺音频或payload → 跳过")
            continue
        rr = json.load(open(pay, encoding="utf-8"))
        face = root / rr["file_relpath"]
        if not face.is_file():
            print(f"  {sid}: 渲染片不存在 → 跳过")
            continue
        dest = root / f"09_downloads/lipsync/{sid}.mp4"
        print(f"  {sid}: 对口型 face={face.name} audio={Path(auds[0]).name} …", flush=True)
        res = lip.sync(face_video=face, audio=Path(auds[0]), dest=dest)
        if res.get("lip_synced"):
            rr["file_relpath"] = f"09_downloads/lipsync/{sid}.mp4"
            rr["lip_synced"] = True
            json.dump(rr, open(pay, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            synced.append(sid)
            print(f"    ✓ 已对齐 → {rr['file_relpath']}")
        else:
            failed.append({"shot": sid, "err": res.get("error") or res.get("note")})
            print(f"    ✗ 失败: {res.get('error') or res.get('note')}")

    print(f"\n=== 口型结果: 成功 {len(synced)} 镜 {synced} | 失败 {len(failed)} {failed} ===")
    root.joinpath("11_review").mkdir(exist_ok=True)
    json.dump({"synced": synced, "failed": failed},
              open(root / "11_review/lip_sync.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    if synced:
        print("下一步: python scripts/plan_d_refinish.py 重装配(用对口型版)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
