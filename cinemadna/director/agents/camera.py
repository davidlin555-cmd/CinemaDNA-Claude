"""CameraLanguageAgent —— 镜头语言：景别/角度/运动/时长意图 + 功能镜策略。"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan


# 景别梯:治"全片正面站街中景"单调。台词镜只在说话景别里换(不用全景/insert);非台词镜可含建立/环境。
_DLG_SIZES = ["特写", "近景", "中近景", "中景"]
_WIDE_SIZES = ["全景", "中景", "中近景", "近景", "特写"]
_MOVES = ["固定", "缓推", "缓拉", "手持", "轻摇"]


class CameraLanguageAgent:
    name = "camera"

    def refine(self, plan: DirectorMasterPlan) -> None:
        # 钩子镜给更强的抓眼策略；信息镜强制大特写
        for s in plan.shots:
            if s.story_function == "钩子":
                s.camera["intent"] = "前3秒视觉抓眼：" + s.camera.get("intent", "")
            if s.story_function == "揭示" and s.prop_usage.get("prop_id"):
                s.camera["shot_size"] = "大特写"
            # 台词镜避免大特写:极特写脸易被裁/某帧检不到 → LatentSync 逐帧对口型整镜失败
            # (实测 SH005 大特写"Face not detected")。降到近景,脸全程可见、对口型友好。
            if s.dialogue_owner and s.camera.get("shot_size") == "大特写":
                s.camera["shot_size"] = "近景"
        self._diversify(plan.shots)

    @staticmethod
    def _diversify(shots: list[Any]) -> None:
        """主动打散镜头语言:连续同景别→换相邻景别、连续同运镜→轮换。

        根因(实测):景别/角度/运镜是"节拍类型→固定查表"一对一,同类节拍必然同景别 →
        全片单调("人正面站街中景")。这里在锁定前主动拉开:尊重台词镜说话景别约束、
        保留揭示镜大特写,其余打散。角度不强改(过肩/俯视含语义),靠景别+运镜制造动感。
        """
        prev: dict[str, Any] | None = None
        rot = 0                                   # 不重置的轮转计数 → 同景别串能铺开≥3种
        for i, s in enumerate(shots):
            cam = s.camera
            # 揭示道具大特写 = 有意信息镜,不动
            if s.story_function == "揭示" and s.prop_usage.get("prop_id"):
                prev = cam
                continue
            size = cam.get("shot_size", "")
            if prev is not None and size and size == prev.get("shot_size"):
                rot += 1
                ladder = _DLG_SIZES if s.dialogue_owner else _WIDE_SIZES
                if size in ladder:
                    alt = ladder[(ladder.index(size) + rot) % len(ladder)]
                    if alt == prev.get("shot_size"):
                        alt = ladder[(ladder.index(size) + rot + 1) % len(ladder)]
                    cam["shot_size"] = alt
            # 运镜:与前镜相同 → 轮换到不同的一个(靠 index 错开,避免全片同一运镜)
            if prev is not None and cam.get("movement") == prev.get("movement"):
                mv = cam.get("movement", "固定")
                base = _MOVES.index(mv) if mv in _MOVES else 0
                cam["movement"] = _MOVES[(base + 1 + i) % len(_MOVES)]
            prev = cam

    def review(self, plan: DirectorMasterPlan) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        shots = plan.shots
        for s in shots:
            for k in ("shot_size", "angle", "movement", "intent"):
                if not (s.camera.get(k) or "").strip():
                    issues.append(_i(s.shot_id, f"镜头语言缺 {k}"))
            d = s.camera.get("duration_sec")
            if not d or not (1.5 <= float(d) <= 5.0):
                issues.append(_i(s.shot_id, f"镜头时长 {d} 越界(1.5–5s)"))
        # 覆盖度：不能全片一个景别（呆板）
        sizes = {s.camera.get("shot_size") for s in shots}
        if shots and len(sizes) < 2:
            issues.append(_i("", "景别单一（全片同景别），缺镜头语言变化"))
        return issues


def _i(sid, msg):
    return {"agent": "camera", "category": "camera", "shot_id": sid, "message": msg}


__all__ = ["CameraLanguageAgent"]
