"""StagingBlockingAgent —— 场面调度：空间布局/站位/走位 + 身体与道具接触逻辑
+ **每镜在场角色（actors）**。

在场角色是场面调度的核心决定：谁在这一镜的画面里。总谱骨架只给了说话人，
这里按"叙事功能 + 场景在场人物"落实每镜 actors，供 Execution Engine 编译成
ActorSlot 在场事实——**根治"对手全程缺席/独角戏"**（施压/反转等对峙镜双人同框）。
"""

from __future__ import annotations

from typing import Any

from director.master_plan import DirectorMasterPlan

#: 需要对手同框的叙事功能（双人对峙/反打），治"追债人全程不出现"
_TWO_PERSON_FUNCS = ("施压", "反转", "揭示", "决策", "反击", "对峙")


class StagingBlockingAgent:
    name = "staging"

    def refine(self, plan: DirectorMasterPlan) -> None:
        # 给每场补一个可执行走位说明（基于在场人数 + 权力关系）
        layout_by_scene = {x["scene_id"]: x for x in plan.scene_layout}
        for sc in plan.scene_layout:
            if sc.get("blocking"):
                continue
            n = len(sc.get("characters") or [])
            sc["blocking"] = ("单人：靠近镜头制造压迫" if n <= 1
                              else "双人对峙：施压方站高位/近景，承压方退守边角")
        # 每镜落实在场角色 + 道具接触 + 前后景
        for s in plan.shots:
            self._assign_actors(s, layout_by_scene.get(s.scene_id) or {})
            if s.prop_usage and not s.prop_usage.get("contact"):
                s.prop_usage["contact"] = "手持并正对镜头呈现"
            s.camera.setdefault("foreground",
                                "人物在前景" if s.camera.get("shot_size") in
                                ("特写", "大特写", "近景", "中近景") else "场景在景深")

    def _assign_actors(self, s: Any, scene: dict[str, Any]) -> None:
        """决定这一镜画面里有谁（写进每个 action_beat 的 actors）。"""
        scene_chars = [c for c in (scene.get("characters") or []) if c]
        protagonist = scene_chars[0] if scene_chars else s.dialogue_owner
        size = s.camera.get("shot_size", "")
        # 注：prop_usage 由 PropInformationAgent 稍后填，staging 阶段还看不到，
        # 故插入镜按"叙事功能+景别"判（无台词的信息/铺垫特写 = 道具插入镜，无人脸）。
        # 顺序要紧：**先判插入镜**，否则 揭示/铺垫 会被下面双人规则抢走。
        if (not s.dialogue_owner and size in ("大特写", "特写")
                and s.story_function in ("揭示", "铺垫")):
            actors = []                       # 无台词信息特写 → 插入镜/无人脸
        elif s.story_function in _TWO_PERSON_FUNCS and len(scene_chars) >= 2:
            # 对峙/对白镜 → **正反打单人镜**（避 Kling 双人近景失控:接吻/漂移/塑料）。
            # 只有**远景/全景建立镜**才双人同框（远距离无亲密风险）；近景一律单人正反打。
            if size in ("远景", "全景"):
                actors = scene_chars[:2]      # 远景建立两人关系(安全)
            elif s.dialogue_owner:
                actors = [s.dialogue_owner]   # 说话人单人镜(正打)
            else:
                other = next((c for c in scene_chars if c != protagonist), None)
                actors = [other or protagonist]   # 反应/无台词 → 对手单人镜(反打)
        elif s.dialogue_owner:
            actors = [s.dialogue_owner]
        elif protagonist:
            actors = [protagonist]
        else:
            actors = []
        for ab in s.action_beats or []:
            ab["actors"] = list(actors)
        # 无台词镜若也无 action_beats，挂一个隐性在场标记供编译器读取
        s.action_beats = s.action_beats or [
            {"body_action": "", "face_action": "", "micro_beats": [],
             "actors": list(actors)}]

    def review(self, plan: DirectorMasterPlan) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for sc in plan.scene_layout:
            if not (sc.get("blocking") or "").strip():
                issues.append(_i(sc.get("scene_id", ""), "场景缺走位调度"))
        # 有道具的镜必须写清接触方式（手/桌/门/手机）
        for s in plan.shots:
            if s.prop_usage.get("prop_id") and not s.prop_usage.get("contact"):
                issues.append(_i(s.shot_id, "道具镜缺接触方式（手/桌/门/手机）"))
        return issues


def _i(sid, msg):
    return {"agent": "staging", "category": "staging", "shot_id": sid, "message": msg}


__all__ = ["StagingBlockingAgent"]
