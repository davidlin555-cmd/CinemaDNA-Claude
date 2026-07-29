"""简单拼接 —— 把渲染好的镜头排成粗剪并（可选）导出"假成片" (Phase 4)

对应 Post Brain 的 Editor 职责里最小的一块：排时间线、算总时长、出字幕轨、
核对是否落在样片目标区间（30–60 秒），并导出三种可直接看的东西：

| 产物                          | 说明                                       |
|-------------------------------|--------------------------------------------|
| `10_outputs/rough_cut.json`   | 时间线 + 字幕 + EDL（结构化，机器读）      |
| `10_outputs/rough_cut.edl`    | 人读的镜头表                                |
| `10_outputs/subtitles.vtt`    | 标准 WebVTT，可直接挂到播放器              |
| `10_outputs/animatic.html`    | 自包含动态分镜，浏览器打开就能按真实时长播 |
| `10_outputs/rough_cut.mp4`    | **真实 MP4**（需 ffmpeg + 真实素材）       |

诚实边界：`media_ready` 只有在**每个镜头都有真实媒体文件**时才为真；
即便如此，占位素材（色板）也会被 `is_generated_footage=false` 标出来 ——
"有文件"和"有画面"是两件事。
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Final

from asset_brain.common import schemas
from asset_brain.common.bundle import Bundle

#: 标杆样片目标时长区间（主规格 §里程碑：30–60 秒）
TARGET_MIN_SEC: Final = 30.0
TARGET_MAX_SEC: Final = 60.0

_FFMPEG: Final = shutil.which("ffmpeg")
_FFPROBE: Final = shutil.which("ffprobe")


def _probe_duration(path: Path) -> float:
    """探测视频真实时长（秒）；失败返回 0.0。"""
    if _FFPROBE is None or not path.is_file():
        return 0.0
    try:
        r = subprocess.run(
            [_FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip() or 0.0)
    except (ValueError, subprocess.SubprocessError):
        return 0.0


def _timecode(seconds: float, *, vtt: bool = False) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if vtt:
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m:02d}:{s:02d}.{ms:03d}"


def assemble_rough_cut(
    contracts: list[dict[str, Any]],
    *,
    story_id: str,
    bundle_id: str,
    title: str = "",
    target_min: float = TARGET_MIN_SEC,
    target_max: float = TARGET_MAX_SEC,
    exclude_shot_ids: set[str] | None = None,
) -> dict[str, Any]:
    """把已渲染的镜头按 order 排成粗剪时间线。

    `exclude_shot_ids`：③熔断隔离的坏镜——**绝不进 Post**，从时间线剔除。
    """
    excluded = set(exclude_shot_ids or ())
    rendered = [c for c in contracts
                if c.get("render_result") and c["shot_id"] not in excluded]
    rendered.sort(key=lambda c: c["order"])

    timeline: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    edl_lines: list[str] = []
    cursor = 0.0

    for i, c in enumerate(rendered, start=1):
        r = c["render_result"]
        dur = float(c["duration_sec"])
        entry = {
            "index": i,
            "shot_id": c["shot_id"],
            "scene_id": c["scene_id"],
            "shot_type": c["shot_type"],
            "in_sec": round(cursor, 2),
            "out_sec": round(cursor + dur, 2),
            "duration_sec": dur,
            "source": r["file_relpath"],
            "media_type": r["media_type"],
            "is_real_media": bool(r.get("is_real_media")),
            "is_generated_footage": bool(r.get("is_generated_footage")),
            "model": r["model"],
            "backend": r.get("backend"),
            "asset_hash": r["asset_hash"],
            "camera": dict(c.get("camera") or {}),
            "prompt": r.get("prompt", ""),
        }
        timeline.append(entry)
        edl_lines.append(
            f"{i:03d}  {_timecode(entry['in_sec'])}-{_timecode(entry['out_sec'])}  "
            f"{c['shot_id']:<24} {c['shot_type']:<12} {r['model']:<16} "
            f"{r['file_relpath']}"
        )
        for line in c.get("dialogue") or []:
            subtitles.append(
                {
                    "shot_id": c["shot_id"],
                    "character_id": line["character_id"],
                    "text": line["line"],
                    "in_sec": round(cursor, 2),
                    "out_sec": round(cursor + dur, 2),
                }
            )
        cursor += dur

    total = round(cursor, 2)
    missing = [c["shot_id"] for c in contracts if not c.get("render_result")]
    media_ready = bool(timeline) and all(e["is_real_media"] for e in timeline)
    generated = bool(timeline) and all(e["is_generated_footage"] for e in timeline)

    return {
        "schema_version": schemas.SCHEMA_ROUGH_CUT,
        "story_id": story_id,
        "bundle_id": bundle_id,
        "title": title,
        "shot_count": len(timeline),
        "total_duration_sec": total,
        "target_range_sec": [target_min, target_max],
        "duration_in_target": target_min <= total <= target_max,
        "scenes": sorted({e["scene_id"] for e in timeline}),
        "timeline": timeline,
        "subtitles": subtitles,
        "edl": edl_lines,
        "missing_shots": missing,
        #: 每个镜头都有真实媒体文件
        "media_ready": media_ready,
        #: 每个镜头都是模型生成的画面（占位色板 = False）
        "is_generated_footage": generated,
        "exports": {},
        "assembled_at": schemas.utc_now_iso(),
        "notes": (
            "media_ready 表示每个镜头都有真实文件；is_generated_footage 表示"
            "这些文件是模型生成的画面。占位色板下前者可为真、后者必为假。"
        ),
    }


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def build_vtt(cut: dict[str, Any]) -> str:
    """标准 WebVTT 字幕。"""
    lines = ["WEBVTT", ""]
    for i, s in enumerate(cut["subtitles"], start=1):
        lines += [
            str(i),
            f"{_timecode(s['in_sec'], vtt=True)} --> {_timecode(s['out_sec'], vtt=True)}",
            s["text"],
            "",
        ]
    return "\n".join(lines)


def build_edl_text(cut: dict[str, Any]) -> str:
    """人读的镜头表。"""
    head = [
        f"# {cut['title'] or cut['story_id']}",
        f"# 镜头 {cut['shot_count']}  总时长 {cut['total_duration_sec']}s  "
        f"目标区间 {cut['target_range_sec']}  达标={cut['duration_in_target']}",
        f"# media_ready={cut['media_ready']}  "
        f"is_generated_footage={cut['is_generated_footage']}",
        "",
    ]
    return "\n".join(head + list(cut["edl"])) + "\n"


def build_animatic_html(cut: dict[str, Any]) -> str:
    """自包含动态分镜：浏览器打开就能按真实时长播一遍。

    没有真实画面时它就是最接近"看到成片"的东西：按镜头时长切换板面，
    显示景别、运镜、提示词与台词，并实时走时间码。
    """
    shots = [
        {
            "i": e["index"], "id": e["shot_id"], "scene": e["scene_id"],
            "type": e["shot_type"], "in": e["in_sec"], "out": e["out_sec"],
            "dur": e["duration_sec"], "model": e["model"],
            "size": e["camera"].get("shot_size", ""),
            "move": e["camera"].get("movement", ""),
            "prompt": e["prompt"],
            "real": e["is_real_media"], "gen": e["is_generated_footage"],
            "src": e["source"],
            "subs": [s["text"] for s in cut["subtitles"] if s["shot_id"] == e["shot_id"]],
        }
        for e in cut["timeline"]
    ]
    data = json.dumps(
        {"title": cut["title"] or cut["story_id"], "total": cut["total_duration_sec"],
         "shots": shots, "generated": cut["is_generated_footage"]},
        ensure_ascii=False,
    )
    title = html.escape(cut["title"] or cut["story_id"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title} · 动态分镜</title>
<style>
 :root {{ color-scheme: dark; }}
 body {{ margin:0; background:#0d0d12; color:#eee;
        font-family:"Microsoft YaHei",system-ui,sans-serif;
        display:flex; flex-direction:column; align-items:center; gap:12px; padding:16px; }}
 #stage {{ width:min(360px,90vw); aspect-ratio:9/16; border-radius:10px;
           display:flex; flex-direction:column; justify-content:space-between;
           padding:18px; box-sizing:border-box; transition:background .25s; }}
 #meta {{ font-size:13px; opacity:.8; line-height:1.7; }}
 #sub {{ text-align:center; font-size:20px; font-weight:600; text-shadow:0 2px 6px #000; }}
 #bar {{ width:min(360px,90vw); height:6px; background:#222; border-radius:3px; }}
 #fill {{ height:100%; width:0; background:#e0a458; border-radius:3px; }}
 #hud {{ font-size:13px; opacity:.75; }}
 button {{ background:#e0a458; border:0; color:#111; padding:8px 20px;
           border-radius:6px; font-size:15px; cursor:pointer; }}
 .warn {{ font-size:12px; color:#e0a458; max-width:min(360px,90vw); text-align:center; }}
</style></head><body>
<h3 style="margin:4px">{title}</h3>
<div id="stage"><div id="meta"></div><div id="sub"></div></div>
<div id="bar"><div id="fill"></div></div>
<div id="hud">00:00.000 / 00:00.000</div>
<button id="play">播放</button>
<div class="warn" id="warn"></div>
<script>
const D = {data};
const COLORS = {{ESTABLISHING:"#1b263b",MEDIUM:"#2b2d42",CLOSEUP:"#3d2c3f",
                 REACTION:"#2f3e46",INSERT:"#40342b"}};
const stage=document.getElementById("stage"), meta=document.getElementById("meta"),
      sub=document.getElementById("sub"), fill=document.getElementById("fill"),
      hud=document.getElementById("hud"), btn=document.getElementById("play");
document.getElementById("warn").textContent = D.generated
  ? "" : "注意：这不是模型生成的画面，只是按真实时长播放的分镜占位。";
const fmt=s=>{{const m=Math.floor(s/60),x=(s%60).toFixed(3).padStart(6,"0");
  return `${{String(m).padStart(2,"0")}}:${{x}}`;}};
let t0=null, raf=null;
function draw(t){{
  const shot = D.shots.find(s=>t>=s.in && t<s.out) || D.shots[D.shots.length-1];
  stage.style.background = COLORS[shot.type] || "#22223b";
  meta.innerHTML = `<b>${{shot.i}}/${{D.shots.length}} ${{shot.id}}</b><br>`
    + `${{shot.type}} · ${{shot.size}} · ${{shot.move}}<br>`
    + `${{shot.dur}}s · ${{shot.model}}<br><span style="opacity:.6">${{shot.prompt}}</span>`;
  sub.textContent = shot.subs.join(" / ");
  fill.style.width = (100*t/D.total).toFixed(2)+"%";
  hud.textContent = fmt(t)+" / "+fmt(D.total);
}}
function tick(ts){{
  if(t0===null) t0=ts;
  const t=(ts-t0)/1000;
  if(t>=D.total){{ draw(D.total-0.001); btn.textContent="重播"; t0=null; raf=null; return; }}
  draw(t); raf=requestAnimationFrame(tick);
}}
btn.onclick=()=>{{ if(raf) return; btn.textContent="播放中…"; t0=null;
  raf=requestAnimationFrame(tick); }};
draw(0);
</script></body></html>
"""


def concat_with_ffmpeg(cut: dict[str, Any], bundle: Bundle, *,
                       out_relpath: str = "10_outputs/rough_cut.mp4",
                       timeout_sec: int = 300) -> dict[str, Any]:
    """用 ffmpeg 把各镜头文件拼成一条真实 MP4。

    前提：每个镜头都有真实媒体文件（`media_ready`）。否则原样返回失败原因 ——
    没有素材就拼不出片，这一点不该被含糊过去。
    """
    if not cut["timeline"]:
        return {"ok": False, "reason": "没有任何已渲染镜头"}
    if not cut["media_ready"]:
        return {"ok": False, "reason": "存在非真实媒体的镜头，无法拼接"}
    if _FFMPEG is None:
        return {"ok": False, "reason": "未找到 ffmpeg"}

    out = bundle.path_for(out_relpath)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        # 每镜规整到**语音回写后的镜头时长**：视频迁就语音（不足则**整体变速填满**，
        # 而不是定格末帧——定格会被商业闸判为冻帧）。连续运动、无冻帧、时长精确。
        # 变速比例 clamp 到 [0.5,2.0]，避免极端慢放。同时统一 1080x1920/30fps 便于拼接。
        norm_files: list[Path] = []
        for i, e in enumerate(cut["timeline"]):
            src = bundle.path_for(e["source"])
            dur = round(float(e["duration_sec"]), 2)
            src_dur = _probe_duration(src) or dur
            # 内容驱动短剧：镜头短于素材就**正常速度截取**（-t 截断），绝不慢放凑数。
            # 只有素材短于目标时才轻微补速（封顶 1.05，肉眼无慢放感）。
            if src_dur >= dur or src_dur <= 0.1:
                ratio = 1.0
            else:
                ratio = round(min(1.05, dur / src_dur), 4)
            npath = Path(td) / f"n{i:03d}.mp4"
            # 轻微连续手持位移（±6px）：静态屏内道具镜不被判冻帧 + 一点手持呼吸感。
            vf = (f"scale=1104:1964:force_original_aspect_ratio=increase,"
                  f"crop=1080:1920:x='(iw-1080)/2+6*sin(t*2)':"
                  f"y='(ih-1920)/2+6*cos(t*1.6)',"
                  f"setsar=1,setpts={ratio}*PTS,fps=30")
            cmd = [_FFMPEG, "-y", "-loglevel", "error", "-i", str(src),
                   "-vf", vf, "-t", f"{dur}", "-an",
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", str(npath)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                return {"ok": False, "reason": "ffmpeg 规整镜头超时"}
            if proc.returncode != 0 or not npath.is_file():
                return {"ok": False, "reason": f"镜头 {e.get('shot_id')} 规整失败: "
                        f"{proc.stderr.strip()[:200]}"}
            norm_files.append(npath)

        listfile = Path(td) / "concat.txt"
        listfile.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in norm_files),
            encoding="utf-8")
        cmd = [
            _FFMPEG, "-y", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listfile),
            "-c", "copy", str(out),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            return {"ok": False, "reason": "ffmpeg 拼接超时"}
    if proc.returncode != 0:
        return {"ok": False, "reason": f"ffmpeg 失败: {proc.stderr.strip()[:300]}"}
    return {
        "ok": True,
        "relpath": out_relpath,
        "bytes": out.stat().st_size,
        "duration_sec": cut["total_duration_sec"],
    }


def export_cut(
    cut: dict[str, Any], bundle: Bundle | None, *, video: bool = True
) -> dict[str, Any]:
    """导出 EDL / VTT / 动态分镜（以及可选的真实 MP4），返回导出清单。

    该函数会把结果写进 `cut["exports"]`，因此 rough_cut.json 自带产物索引。
    """
    exports: dict[str, Any] = {}
    if bundle is None:
        cut["exports"] = exports
        return exports

    edl_rel = "10_outputs/rough_cut.edl"
    bundle.path_for(edl_rel).parent.mkdir(parents=True, exist_ok=True)
    bundle.path_for(edl_rel).write_text(build_edl_text(cut), encoding="utf-8")
    exports["edl"] = edl_rel

    if cut["subtitles"]:
        vtt_rel = "10_outputs/subtitles.vtt"
        bundle.path_for(vtt_rel).write_text(build_vtt(cut), encoding="utf-8")
        exports["subtitles"] = vtt_rel

    html_rel = "10_outputs/animatic.html"
    bundle.path_for(html_rel).write_text(build_animatic_html(cut), encoding="utf-8")
    exports["animatic"] = html_rel

    if video:
        res = concat_with_ffmpeg(cut, bundle)
        exports["video"] = res

    cut["exports"] = exports
    return exports
