"""③ Vision QA 熔断 —— 逐镜视觉质检，坏镜隔离，**禁止进 Post**，单镜重跑。

复盘：坏镜（变脸/崩坏/乱码/静帧/无剧情载荷）若拼进成片 = 商业不可发布；且"一镜坏
拖垮整片"是脆性。熔断机制：渲染后**逐镜** Vision QA → 坏镜进隔离区（不进剪辑）→
有重跑器则**单镜换种子重跑**（reroll，拿不同素材）→ 仍坏则保持隔离（其余好镜照常出片，
优雅降级，不整片 FAILED）。

硬保证：**assemble 只吃 passed 镜；quarantined 镜绝不进 Post。**
只做能熔断产线的事，不引入讨论型 Agent。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


class Judge(Protocol):
    def available(self) -> bool: ...
    def judge(self, video: Path) -> Any: ...


@dataclass
class QuarantineResult:
    passed: list[str]                                   # 通过的 shot_id（可进 Post）
    quarantined: list[dict[str, Any]]                   # [{shot_id, reasons}]
    rerolled: list[str] = field(default_factory=list)   # 重跑后转通过的 shot_id

    @property
    def passed_set(self) -> set[str]:
        return set(self.passed)

    @property
    def quarantined_ids(self) -> list[str]:
        return [q["shot_id"] for q in self.quarantined]

    @property
    def all_passed(self) -> bool:
        return not self.quarantined

    def report(self) -> dict[str, Any]:
        return {"passed": self.passed, "quarantined": self.quarantined,
                "rerolled": self.rerolled, "passed_count": len(self.passed),
                "quarantined_count": len(self.quarantined), "all_passed": self.all_passed}


class VisionQuarantine:
    """逐镜熔断：坏镜隔离不进 Post。judge 可注入（测试用假判）；extra_check 叠加
    身份/功能/乱码等判据（返回 reasons 列表，非空=坏镜）。"""

    def __init__(self, judge: Judge | None = None, *,
                 extra_check: Callable[[dict[str, Any]], list[str]] | None = None) -> None:
        if judge is None:
            from final_review.vision_judge import VisionJudge
            judge = VisionJudge()
        self.judge = judge
        self.extra_check = extra_check

    def _reasons_for(self, r: dict[str, Any], bundle: Any) -> list[str]:
        reasons: list[str] = []
        rel = r.get("file_relpath")
        # 真实素材才做像素判（占位/mock 不判，避免误杀 CI）
        if (rel and bundle is not None and r.get("is_generated_footage")
                and getattr(self.judge, "available", lambda: False)()):
            try:
                reasons.extend(self._hard_defects(self.judge.judge(
                    bundle.path_for(rel))))
            except Exception:  # noqa: BLE001 判定失败不误杀，交后续总审
                pass
        if self.extra_check:
            reasons.extend(self.extra_check(r) or [])
        return reasons

    @staticmethod
    def _hard_defects(res: Any) -> list[str]:
        """**只熔断真崩坏**：静帧/纯色/手脸畸变/变脸/画内乱码。

        ⚠️ **塑料感(ai_fake)/僵硬(stiff)不逐镜熔断**——那是整片质量天花板（中端底模
        每镜都有），逐个踢会把整片踢空。塑料感由商业闸/ShowQuality 整片把关，
        跨镜漂移由 InsightFace 整片把关；这里只踢"这一镜坏得不能用"的硬崩坏。
        """
        out: list[str] = []
        checks = getattr(res, "checks", {}) or {}
        deep = getattr(res, "deep", {}) or {}
        if checks.get("static"):
            out.append("静帧/渲染崩坏")
        if checks.get("blank"):
            out.append("纯色/空白未生成")
        if deep.get("hand_face_broken"):
            out.append("手/脸崩坏畸变")
        if deep.get("face_swap"):
            out.append("变脸/多脸身份错乱")
        if deep.get("garbled_text"):
            out.append("画内乱码文字")
        return out

    def screen(self, rendered: list[dict[str, Any]], *, bundle: Any = None
               ) -> QuarantineResult:
        passed: list[str] = []
        quarantined: list[dict[str, Any]] = []
        for r in rendered or []:
            reasons = self._reasons_for(r, bundle)
            if reasons:
                quarantined.append({"shot_id": r["shot_id"], "reasons": reasons})
            else:
                passed.append(r["shot_id"])
        return QuarantineResult(passed=passed, quarantined=quarantined)

    def screen_with_reroll(self, rendered: list[dict[str, Any]], *, bundle: Any = None,
                           reroll: Callable[[str, int], dict[str, Any] | None] | None = None,
                           max_rerolls: int = 1) -> QuarantineResult:
        """先熔断，再对坏镜**单镜换种子重跑**（bounded）。reroll(shot_id, attempt)
        返回新的 rendered 记录或 None；重跑后仍坏 → 保持隔离。"""
        res = self.screen(rendered, bundle=bundle)
        if reroll is None or not res.quarantined:
            return res
        by_id = {r["shot_id"]: r for r in rendered}
        still_bad: list[dict[str, Any]] = []
        for q in res.quarantined:
            sid = q["shot_id"]
            recovered = False
            for attempt in range(1, max_rerolls + 1):
                new_r = reroll(sid, attempt)          # 单镜重跑（换种子）
                if new_r is None:
                    break
                by_id[sid] = new_r
                if not self._reasons_for(new_r, bundle):   # 重跑后过了
                    res.passed.append(sid)
                    res.rerolled.append(sid)
                    recovered = True
                    break
            if not recovered:
                still_bad.append(q)
        res.quarantined = still_bad
        return res


class SemanticCheck:
    """②语义 VisionQA：判每镜是否符合导演意图 + 有无不当内容(接吻/亲密)→ 熔断。

    这是接吻类**语义灾难**的防线（③熔断原本只查塑料感/崩坏,查不出"演错/不当内容"）。
    作为 VisionQuarantine 的 extra_check。只对**不当内容**熔断(高置信灾难);意图不符只
    记录不熔断(避免判官误读误杀)。judge=AnthropicVisionJudge(可注入假判 0 成本测)。
    """

    def __init__(self, judge: Any, intents: dict[str, str], *, bundle: Any = None,
                 ffmpeg: str = "ffmpeg",
                 runner: Callable[[list[str]], int] | None = None) -> None:
        self.judge = judge
        self.intents = intents or {}
        self.bundle = bundle
        self.ffmpeg = ffmpeg
        self._runner = runner or self._default_runner

    def _default_runner(self, argv: list[str]) -> int:
        import subprocess
        return subprocess.run(argv, capture_output=True, timeout=60).returncode

    def _mid_frame(self, video: Path, dur: float) -> Path | None:
        frame = video.parent / f"_sem_{video.stem}.jpg"
        rc = self._runner([self.ffmpeg, "-y", "-v", "error", "-ss",
                           f"{max(0.3, dur * 0.5):.2f}", "-i", str(video),
                           "-frames:v", "1", str(frame)])
        return frame if rc == 0 and frame.is_file() else None

    def __call__(self, r: dict[str, Any]) -> list[str]:
        sid = r.get("shot_id")
        intent = self.intents.get(sid)
        rel = r.get("file_relpath")
        if (not intent or not rel or self.bundle is None
                or not r.get("is_generated_footage")):
            return []
        frame = self._mid_frame(self.bundle.path_for(rel), float(r.get("duration_sec", 3)))
        if frame is None:
            return []
        try:
            v = self.judge.judge_semantic(frame, intent)
        except Exception:  # noqa: BLE001 判定失败不误杀
            return []
        if getattr(v, "inappropriate", False):
            kind = getattr(v, "inappropriate_kind", "") or getattr(v, "reason", "不当内容")
            return [f"语义灾难/不当内容({kind})"]
        return []


__all__ = ["VisionQuarantine", "QuarantineResult", "SemanticCheck"]
