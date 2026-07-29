"""场景内首帧生成 —— 根治"证件照感/融景弱" + "全片同一张脸/零景别变化"。

两个根因，两段式根治：
  1) 证件照感：image2video 只拿一张白底证件照去动 → 两段式：先按镜出**场景内首帧**
     （人物在真实场景里、穿对服装、正在做这一镜的动作、对的景别），再 i2v 动这张首帧。
  2) 全片同一张脸/零景别（复盘发现的致命问题）：旧版首帧缓存键**只按角色**（cids[0]），
     同角色 N 镜复用同一张首帧 → i2v 永远动同一张头像 → 景别/机位/场景/动作/对手全锁死。
     根治：首帧改为**按镜构图**——缓存键含镜头身份，不同镜出不同景别/动作/对手/道具，
     身份由**每角色固定种子 + 一致外观描述（+金图包）**锚定，而不是靠复用同一张图。

英文-only：绝不把中文台词/成句/动作喂给 FLUX（否则画面渲出乱码"文字"）。中文动作节拍
先翻成英文视觉短语再进提示词。
"""

from __future__ import annotations

import base64
import hashlib
import inspect
from pathlib import Path
from typing import Any

from .backend import FatalRenderError, MediaSink, RenderRequest
from .screen_aware_backend import ScreenAwareImageToVideoBackend


#: 景别（中文）→ 英文构图（喂 FLUX；不同景别出明显不同的构图，制造视觉变化）
_SIZE_EN = {
    "全景": "wide establishing shot, full environment visible, subject small in frame",
    "远景": "wide establishing shot, full environment visible, subject small in frame",
    "中景": "medium shot, waist-up framing",
    "中近景": "medium close-up, chest-up framing",
    "近景": "close-up shot, head and shoulders",
    "特写": "close-up shot, head and shoulders",
    "大特写": "extreme close-up, tight on the face and eyes",
}
_SIZE_DEFAULT = "medium shot, waist-up framing"

#: 中文动作节拍 → 英文视觉短语（让首帧里的人**正在做动作**，不是站着说话）
_ACTION_EN: tuple[tuple[str, str], ...] = (
    ("掏出", "pulling a crumpled paper document out and holding it up as evidence"),
    ("证据", "holding up a crumpled paper document as evidence"),
    ("单据", "clutching a crumpled bill in a tight fist"),
    ("欠条", "holding up a crumpled IOU paper note"),
    ("撕", "tearing a paper document apart"),
    ("砸", "slamming a fist down hard"),
    ("拳", "clenching a fist tightly, knuckles white"),
    ("掩面", "covering the face with one hand, head bowed"),
    ("后退", "stepping back defensively, recoiling"),
    ("重心后移", "shifting weight backward, flinching away"),
    ("逼近", "leaning in aggressively, invading personal space"),
    ("前逼", "leaning in aggressively, invading personal space"),
    ("打断", "raising a hand sharply to cut someone off"),
    ("指向", "pointing sharply and accusingly"),
    ("转身", "turning away sharply"),
    ("起身", "rising slowly and deliberately to stand"),
    ("手指收紧", "fingers tightening, body tense"),
    ("手机", "holding up a phone showing a screen"),
)


def action_to_en(*texts: str) -> str:
    """把中文动作节拍翻成英文视觉短语（首个命中）。无命中 → 空串。"""
    blob = " ".join(t for t in texts if t)
    for zh, en in _ACTION_EN:
        if zh in blob:
            return en
    return ""


def stable_seed(text: str) -> int:
    """由文本派生稳定种子（同一角色永远同一 seed → 跨镜同一张脸）。"""
    return int(hashlib.sha1((text or "x").encode("utf-8")).hexdigest()[:7], 16)


def build_first_frame_prompt(
    request: RenderRequest, character_desc: str, *,
    action_en: str = "", others_desc: list[str] | None = None,
    prop_desc: str | None = None, phase: str = "",
) -> str:
    """场景内首帧提示词 —— **纯英文视觉描述 + 按镜构图 + 强力反文字**。

    - 道具插入镜（prop_desc）：拍道具特写，画面里**没有人脸**。
    - 双人对峙镜（others_desc）：两人同框对峙 → 反打感 + 对手真进画面。
    - 单人动作镜：人物按景别 + **正在做 action_en 的动作**（不是站着说话）。
    - phase：起帧+尾帧驱动动作。"start"=中性预备态（动作未起）；"end"=动作完成态峰值。
      Kling 在两帧间插值 → 动作真发生 + 表情弧线实现。
    """
    cam = request.camera or {}
    env = ("a realistic dim Chinese county-town street at night, "
           "wet asphalt, moody neon and lantern glow far in the background")
    anti_text = (
        "absolutely no text, no letters, no chinese characters, no words, "
        "no watermark, no captions, no subtitles, no signboard text, "
        "no shop signs, no lantern text, no banners, no logo, "
        "no writing anywhere in the frame")
    base = ("cinematic film still, photorealistic, natural skin texture, "
            "moody cinematic lighting, shallow depth of field, film grain, "
            "vertical 9:16 composition, SFW, fully clothed")

    # ① 道具插入镜：特写道具，无人脸（制造插入镜节奏 + 展示关键证据）
    if prop_desc:
        return (
            f"extreme close-up product shot of {prop_desc}, "
            f"held in a hand, {env}, {base}, {anti_text}")[:1500]

    size = _SIZE_EN.get(cam.get("shot_size", ""), _SIZE_DEFAULT)
    if phase == "start":
        act = ", standing ready in a calm neutral pose, before the action begins"
    elif phase == "end" and action_en:
        act = f", {action_en}, action fully completed at the peak of the moment"
    else:
        act = f", {action_en}" if action_en else ""

    # ② 双人对峙镜：对手真进画面（治"对手全程缺席"）
    others = [d for d in (others_desc or []) if d]
    if others:
        opp = others[0]
        return (
            f"{base}, {size}, two people confronting each other, "
            f"on the left {character_desc}{act}, "
            f"on the right {opp}, tense face-off, {env}, {anti_text}")[:1500]

    # ③ 单人动作镜：按景别 + 正在做动作
    return (
        f"{base}, {size}, {character_desc}{act}, grounded in {env}, "
        f"{anti_text}")[:1500]


class FirstFrameBackend:
    """按镜生成场景内首帧（文生图）。复用任意 ImageBackend（含预算闸）。"""

    def __init__(self, image_backend: Any) -> None:
        self.image_backend = image_backend
        # 后端是否支持 seed（老签名不支持 → 不传，避免双调用/双记账）
        try:
            self._supports_seed = "seed" in inspect.signature(
                image_backend.generate).parameters
        except (ValueError, TypeError, AttributeError):
            self._supports_seed = False

    def generate(self, request: RenderRequest, *, character_desc: str,
                 dest: Path, item_id: str, seed: int | None = None,
                 action_en: str = "", others_desc: list[str] | None = None,
                 prop_desc: str | None = None) -> Path:
        prompt = build_first_frame_prompt(
            request, character_desc, action_en=action_en,
            others_desc=others_desc, prop_desc=prop_desc)
        kw: dict[str, Any] = dict(prompt=prompt, dest=dest, item_id=item_id,
                                  width=768, height=1360)
        if self._supports_seed and seed is not None:
            kw["seed"] = seed           # 固定种子锁脸
        self.image_backend.generate(**kw)
        if not dest.is_file() or dest.stat().st_size < 500:
            raise FatalRenderError(f"{request.shot_id}: 场景内首帧生成失败")
        return dest


class SceneGroundedImageToVideoBackend(ScreenAwareImageToVideoBackend):
    """场景内首帧 + image2video + 屏内合成 —— **每镜按构图锚定场景**，不再头像动画。"""

    name = "scene-grounded-image2video"

    def __init__(self, *, first_frame_backend: FirstFrameBackend,
                 character_desc_by_id: dict[str, str] | None = None,
                 gold_packs: dict[str, Any] | None = None,
                 require_gold: bool = False,
                 foundry: Any = None,
                 default_character_desc: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.ff = first_frame_backend
        self.char_desc = character_desc_by_id or {}
        #: 角色金图包 {character_id: CharacterGoldPack}；有则主角镜用金图锚帧
        self.gold_packs = gold_packs or {}
        #: 硬规则：主角必须有金图包才准渲（无金图拒渲）
        self.require_gold = require_gold
        #: 合成身份铸造：有则主角单人镜用 PuLID 锚脸锁脸（跨镜同脸+无塑料感）
        self.foundry = foundry
        # 兜底外观也用**具体**服装(不留 "story-appropriate wardrobe" 模糊口子 → 治服装漂移)
        from identity.appearance import DEFAULT_APPEARANCE
        self.default_desc = default_character_desc or DEFAULT_APPEARANCE
        #: shot_id → (参考类型, 参考键)，供渲染后盖可验证身份指纹
        self._ref_used: dict[str, tuple[str, str]] = {}

    # -- 在场角色 / 动作 / 道具（供按镜构图） ---------------------------

    def _in_frame_cids(self, request: RenderRequest) -> list[str]:
        return list((request.reference_assets or {}).get("character_images") or {})

    def _character_desc(self, request: RenderRequest) -> str:
        for cid in self._in_frame_cids(request):
            if cid in self.char_desc:
                return self.char_desc[cid]
        return self.default_desc

    def _others_desc(self, request: RenderRequest) -> list[str]:
        """本镜除主角外的其他在场角色描述（双人对峙镜用 → 对手进画面）。"""
        cids = self._in_frame_cids(request)
        return [self.char_desc[c] for c in cids[1:] if c in self.char_desc]

    def _action_en(self, request: RenderRequest) -> str:
        """从表演节拍里提炼英文动作短语（让首帧人物正在做动作）。"""
        beats = (request.performance or {}).get("acting_beats") or []
        bodies = [str(b.get("body", "")) for b in beats]
        cues = [str(b.get("cue", "")) for b in beats]
        return action_to_en(*bodies, *cues)

    def _prop_insert_desc(self, request: RenderRequest) -> str | None:
        """判定是否道具插入镜；是则返回英文道具描述（拍道具特写，无人脸）。"""
        cam = request.camera or {}
        props = list((request.reference_assets or {}).get("props") or {})
        cids = self._in_frame_cids(request)
        stype = cam.get("shot_type", "")
        size = cam.get("shot_size", "")
        # 插入镜：显式 INSERT，或"大特写/特写道具镜且无对白无主角在场"
        is_insert = stype in ("INSERT", "插入") or (
            size in ("大特写", "特写") and props and not cids
            and not (request.dialogue or []))
        if not is_insert or not props:
            return None
        p = str(props[0])
        if any(k in p for k in ("欠条", "单据", "缴费", "账单", "借条")):
            return "a crumpled handwritten IOU paper note with a red fingerprint stamp"
        if any(k in p for k in ("手机", "屏", "短信", "录音")):
            return "a smartphone screen glowing in the dark"
        return "a key story prop"

    # -- 首帧缓存键：**按镜构图**（治全片同一张脸/零景别） ---------------

    def _first_frame_key(self, request: RenderRequest) -> str:
        """按镜构图键。道具插入镜按道具建帧；其余每镜一张场景内首帧
        （身份由种子+描述锚定，不再靠复用同一张图 → 景别/动作/对手随镜变化）。"""
        if self._prop_insert_desc(request):
            return f"prop__{request.shot_id}"
        return f"shot__{request.shot_id}"

    def _reference_image_b64(self, request: RenderRequest) -> str:
        """i2v 参考图优先级：**金图包锚帧**（跨镜稳定硬路径）> 按镜场景内首帧。
        无金图包且 require_gold=True → 拒渲主角（硬规则）。"""
        if self._bundle_root is None:
            return super()._reference_image_b64(request)
        root = Path(self._bundle_root) / request.bundle_id
        cids = self._in_frame_cids(request)
        primary = cids[0] if cids else None

        # ① 金图包锚帧：**仅当金图是参考条件级同脸**（Redux/Elements）才用——它只有
        # 固定几个角度、同构图，若拿来当每镜锚帧会重演"零景别变化"。纯文生图金图
        # (angles_are_reference_consistent=False) 连同脸都不保证，一律走②按镜首帧。
        pack = self.gold_packs.get(primary) if primary else None
        if pack is not None and getattr(pack, "angles_are_reference_consistent", False):
            from identity.gold_pack import require_gold_pack
            require_gold_pack(pack, character_id=primary)
            stype = (request.camera or {}).get("shot_type", "")
            pressure = float((request.emotion or {}).get("intensity", 0) or 0) >= 0.75
            rel = pack.angle_for(stype, pressure=pressure)
            gp = root / rel if rel else None
            if gp and gp.is_file():
                self._ref_used[request.shot_id] = ("gold_multiangle", str(rel))
                return base64.b64encode(gp.read_bytes()).decode()
        elif pack is None and self.require_gold and primary is not None:
            from identity.gold_pack import GoldPackError
            raise GoldPackError(f"角色 {primary} 无金图包，禁止渲染主角镜")

        # ①.5 合成身份铸造：主角**单人镜**用 PuLID 锚脸锁脸（跨镜同脸+无塑料感）。
        # 双人镜暂不用（单参考 PuLID 会把对手也画成主角脸）→ 走②按镜首帧。
        others = self._others_desc(request)
        if self._foundry_shot(request):
            key = self._first_frame_key(request)
            dest = root / "08_submissions" / "first_frames" / f"pulid_{key}.png"
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.is_file():
                prompt = build_first_frame_prompt(
                    request, self._character_desc(request),
                    action_en=self._action_en(request),
                    prop_desc=self._prop_insert_desc(request), phase="start")
                self.foundry.first_frame(primary, prompt=prompt, dest=dest,
                                         item_id=f"ff_{key}")
            self._ref_used[request.shot_id] = ("pulid_anchor", key)
            return base64.b64encode(dest.read_bytes()).decode()

        # ② 按镜场景内首帧：景别/动作/对手/道具随镜变化，身份靠固定种子锁脸
        key = self._first_frame_key(request)
        dest = root / "08_submissions" / "first_frames" / f"{key}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            self.ff.generate(
                request, character_desc=self._character_desc(request),
                dest=dest, item_id=f"ff_{key}",
                seed=stable_seed(primary or request.shot_id),  # 每角色固定种子
                action_en=self._action_en(request),
                others_desc=self._others_desc(request),
                prop_desc=self._prop_insert_desc(request))
        self._ref_used[request.shot_id] = ("scene_first_frame", key)
        return base64.b64encode(dest.read_bytes()).decode()

    def _foundry_shot(self, request: RenderRequest) -> bool:
        """是否走 foundry PuLID 锁脸（有 foundry + 主角单人镜 + 有锚脸）。"""
        cids = self._in_frame_cids(request)
        primary = cids[0] if cids else None
        return bool(self.foundry is not None and primary is not None
                    and not self._others_desc(request)
                    and self.foundry.has_anchor(primary))

    def _tail_image_b64(self, request: RenderRequest) -> str | None:
        """起帧+尾帧驱动：foundry **所有主角单人镜**都生成 PuLID 锁定尾帧（同一张脸）→
        Kling 在起/末两个锁定帧间插值 → **脸被两端夹住不漂走** + 动作真发生。

        实测([[kling-motion-drift-tail]]): 只给首帧,Kling 动画中脸会逐渐 morph 成另一个人
        (SH001 沿时间轴 0.39→0.94);给了尾帧的镜漂移只 0.175-0.287。故不再限动作镜——
        有动作 → 尾帧=动作完成态;无动作(对白/反应/建立) → 尾帧=同脸 settled 态,仍锁身份。
        """
        if self._bundle_root is None or not self._foundry_shot(request):
            return None
        action = self._action_en(request)     # 有则动作完成态尾帧;无则同脸锁定尾帧
        root = Path(self._bundle_root) / request.bundle_id
        primary = self._in_frame_cids(request)[0]
        key = self._first_frame_key(request)
        dest = root / "08_submissions" / "first_frames" / f"pulid_tail_{key}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            prompt = build_first_frame_prompt(
                request, self._character_desc(request), action_en=action, phase="end")
            self.foundry.first_frame(primary, prompt=prompt, dest=dest,
                                     item_id=f"tail_{key}")
        return base64.b64encode(dest.read_bytes()).decode()

    def render(self, request: RenderRequest, *, sink: MediaSink) -> dict[str, Any]:
        rendered = super().render(request, sink=sink)
        rendered["scene_grounded_first_frame"] = True
        # ②可验证身份指纹：这一镜用了哪张参考锚 + 种子 + 在场角色
        from .reference_lock import identity_fingerprint, has_core_character
        kind, ref_key = self._ref_used.get(request.shot_id, ("", ""))
        if has_core_character(request):
            rendered["identity_reference"] = identity_fingerprint(
                request, reference_kind=kind or "scene_first_frame", ref_key=ref_key)
        return rendered


__all__ = ["FirstFrameBackend", "SceneGroundedImageToVideoBackend",
           "build_first_frame_prompt", "action_to_en", "stable_seed"]
