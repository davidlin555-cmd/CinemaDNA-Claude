"""根治三问题的回归测试：字幕显示名 / 镜内状态变化 / 场景内首帧（0 成本）。"""

from __future__ import annotations

from pathlib import Path

from asset_brain.common.bundle import Bundle
from audio.service import build_dialogue_vtt
from narrative.density import DensityQAService, VERDICT_PASS
from render.backend import MediaSink, RenderRequest
from render.budget import BudgetGate, confirm_yes
from render.first_frame import (
    FirstFrameBackend,
    SceneGroundedImageToVideoBackend,
    build_first_frame_prompt,
    action_to_en,
)


# ---------------------------------------------------------------------------
# 1. 字幕不再泄露 char_id（也不带说话人前缀）
# ---------------------------------------------------------------------------


class TestSubtitleNoCharId:
    def test_vtt_has_no_char_id_prefix(self):
        contracts = [{
            "shot_id": "S1", "order": 1,
            "dialogue": [{"character_id": "char_linwan", "line": "你完了"}],
            "voice_contract": {"bindings": [{"line": "你完了", "duration_sec": 2.0}]}}]
        vtt, cues = build_dialogue_vtt(contracts)
        assert "char_linwan" not in vtt           # 绝不泄露 char_id
        assert "你完了" in vtt
        assert cues[0]["speaker"] == "char_linwan"  # cue 内部仍留 id 供音频绑定


class TestSubtitleAnchoredToShotStart:
    """字幕/音频锚到镜头在视频里的起点 —— 治"字幕跑在画面前面、中段就放完"。

    旧版只累加台词时长(忽略静默镜与镜头>台词的余量) → 开场静默镜就打出后面的台词、
    片尾无字幕(见样片实测)。视频时轴=Σ镜头 duration_sec,字幕/音频必须同锚此时基。
    """
    def _c(self, i, dur, line="", ldur=2.0):
        c = {"shot_id": f"S{i}", "order": i, "duration_sec": dur, "dialogue": []}
        if line:
            c["dialogue"] = [{"character_id": "char_a", "line": line}]
            c["voice_contract"] = {"bindings": [{"line": line, "duration_sec": ldur}]}
        return c

    def test_silent_shots_advance_timeline(self):
        # 3 个静默镜(共 6.5s)后才有台词 → 该台词的 cue 起点 = 6.5s，不是 0
        contracts = [self._c(1, 2.6), self._c(2, 1.5), self._c(3, 2.4),
                     self._c(4, 3.0, line="签还是不签", ldur=2.2)]
        _, cues = build_dialogue_vtt(contracts)
        assert len(cues) == 1
        assert cues[0]["start"] == 6.5            # = 2.6+1.5+2.4，不再从 0 开始
        assert cues[0]["end"] == 8.7              # 6.5 + 2.2

    def test_last_line_lands_at_end_not_middle(self):
        # 台词短于镜头时长时，后续镜头仍按镜头时长推进 → 最后一句落在片尾附近
        contracts = [self._c(1, 3.0, line="第一句", ldur=1.5),
                     self._c(2, 3.0),                       # 静默
                     self._c(3, 3.0, line="最后一句", ldur=1.5)]
        _, cues = build_dialogue_vtt(contracts)
        total = 9.0
        assert cues[-1]["text"] == "最后一句"
        assert cues[-1]["start"] == 6.0           # 第三镜起点，接近片尾(9s)，非中段
        assert cues[-1]["start"] > total * 0.6


# ---------------------------------------------------------------------------
# 2. 镜内状态变化计入内容密度
# ---------------------------------------------------------------------------


class TestInShotStateDensity:
    def _shot(self, i, bt, shift=""):
        return {"shot_id": f"S{i}", "order": i, "shot_type": "MEDIUM",
                "duration_sec": 3.0, "beat_type": bt, "internal_shift": shift,
                "narrative": {}, "dialogue": []}

    def test_internal_shift_counts_as_content_unit(self):
        # 6 镜，但每镜都有镜内状态变化 → 12 内容单元，满足 30s 的密度
        types = ["setup", "interruption", "obstruction", "counterattack",
                 "reversal", "cliffhanger"]
        cs = [self._shot(i, t, shift="读账单→手机响→僵住")
              for i, t in enumerate(types, 1)]
        r = DensityQAService().review(cs, story_id="s")
        assert r.metrics["internal_shifts"] == 6
        assert r.metrics["content_units"] == 12
        assert not any(i["category"] == "shot_count" for i in r.issues)  # 密度达标


# ---------------------------------------------------------------------------
# 3. 场景内首帧驱动 image2video（根治证件照感）
# ---------------------------------------------------------------------------


class TestSceneGroundedFirstFrame:
    def test_prompt_embeds_character_and_action(self):
        req = RenderRequest(
            shot_id="S1", task_id="t", story_id="s", bundle_id="b",
            contract_hash="h", model="kling-v1", tier="std", duration_sec=3.0,
            seed=1, prompt="林晚撕掉协议。夜，出租屋。中近景。",
            camera={"shot_size": "中近景", "angle": "平视"})
        p = build_first_frame_prompt(req, "a Chinese nurse in white uniform")
        # 首帧只放外观+场景+景别+电影感，**不喂中文台词/动作**（防 FLUX 渲乱码文字）
        assert "a Chinese nurse in white uniform" in p and "cinematic" in p
        assert "撕掉协议" not in p and "no text" in p     # 无中文成句 + 反文字

    def test_first_frame_used_as_i2v_reference(self, tmp_path):
        gate = BudgetGate(max_units=10.0, confirm=confirm_yes,
                          allowed_durations_sec=tuple(range(2, 13)),
                          cost_table={("kling-v1", "std", d): 2.0 for d in range(2, 13)})

        class FakeImg:  # 假文生图后端：写一张假首帧
            name = "fake-flux"
            def generate(self, *, prompt, dest, item_id, width, height):
                Path(dest).write_bytes(b"\x89PNG\r\n" + b"0" * 900)
                return dest

        def fake_http(method, url, headers, body):
            if method == "POST":
                # 关键：提交体里带的是**首帧**（有内容），不是空
                assert body.get("image"), "i2v 参考必须是首帧"
                return 200, {"code": 0, "data": {"task_id": "j1"}}
            return 200, {"code": 0, "data": {"task_status": "succeed",
                         "task_result": {"videos": [{"url": "http://x/v.mp4"}]}}}

        from render.backend import BackendCapabilities
        be = SceneGroundedImageToVideoBackend(
            first_frame_backend=FirstFrameBackend(FakeImg()),
            character_desc_by_id={"linwan": "护士林晚，白大褂"},
            budget=gate, bundle_root=tmp_path, api_key="k", base="http://x",
            http=fake_http)
        be.capabilities = BackendCapabilities(          # 节拍密度镜 2-4s
            min_duration_sec=2.0, max_duration_sec=12.5, supports_reference_images=True,
            produces_media=True, is_async=True, accepts_any_resolution=True,
            duration_tolerance_sec=9.0)
        be.download = lambda url, dest: Path(dest).write_bytes(b"\x00" * 5000)

        bundle = Bundle(tmp_path, "b")
        req = RenderRequest(
            shot_id="S1", task_id="t1", story_id="s", bundle_id="b",
            contract_hash="h", model="kling-v1", tier="std", duration_sec=3.0,
            seed=1, prompt="林晚撕掉协议",
            camera={"shot_size": "中近景"},
            reference_assets={"character_images": {"linwan": "02_cast/faces/x.png"},
                              "props": {}})
        rendered = be.render(req, sink=MediaSink(bundle))
        assert rendered["scene_grounded_first_frame"] is True
        # 首帧真被生成落盘（**按镜**缓存 → 不同镜不同构图；身份靠固定种子锁脸）
        assert bundle.path_for("08_submissions/first_frames/shot__S1.png").is_file()


class TestPerShotComposition:
    """治"全片同一张脸/零景别"：首帧按镜构图（景别/动作/对手/道具随镜变化）。"""

    def _req(self, shot_id, **cam):
        return RenderRequest(
            shot_id=shot_id, task_id="t", story_id="s", bundle_id="b",
            contract_hash="h", model="kling-v1", tier="std", duration_sec=3.0,
            seed=1, prompt="x", camera=cam,
            reference_assets={"character_images": {"linwan": "x.png"}, "props": {}})

    def test_different_shots_get_different_first_frame_keys(self):
        gate = BudgetGate(max_units=10.0, confirm=confirm_yes,
                          allowed_durations_sec=tuple(range(2, 13)),
                          cost_table={("kling-v1", "std", d): 2.0 for d in range(2, 13)})
        be = SceneGroundedImageToVideoBackend(
            first_frame_backend=FirstFrameBackend(object()),
            budget=gate, bundle_root=".", api_key="k", base="http://x")
        k1 = be._first_frame_key(self._req("EP1_SC1_SH1", shot_size="特写"))
        k2 = be._first_frame_key(self._req("EP1_SC1_SH2", shot_size="全景"))
        assert k1 != k2                       # 不同镜 → 不同首帧（构图会变）

    def test_shot_size_changes_composition(self):
        wide = build_first_frame_prompt(self._req("s", shot_size="全景"),
                                        "a nurse")
        cu = build_first_frame_prompt(self._req("s", shot_size="大特写"), "a nurse")
        assert "wide establishing" in wide and "extreme close-up" in cu

    def test_antagonist_enters_frame_on_two_person_shot(self):
        p = build_first_frame_prompt(
            self._req("s", shot_size="中景"), "a nurse",
            others_desc=["a debt collector"])
        assert "confronting" in p and "a debt collector" in p

    def test_action_makes_person_do_something(self):
        assert "holding it up" in action_to_en("掏出证据")
        p = build_first_frame_prompt(self._req("s"), "a nurse",
                                     action_en=action_to_en("掏出证据"))
        assert "holding it up" in p or "holding up" in p

    def test_prop_insert_has_no_face(self):
        p = build_first_frame_prompt(self._req("s"), "a nurse",
                                     prop_desc="a crumpled IOU note")
        assert "close-up product shot" in p and "a crumpled IOU note" in p
        assert "a nurse" not in p              # 插入镜没有人脸

    def test_stable_seed_same_char_same_seed(self):
        from render.first_frame import stable_seed
        assert stable_seed("linwan") == stable_seed("linwan")
        assert stable_seed("linwan") != stable_seed("boss")


class TestBeatShotTypeMouthSafety:
    def test_lined_beat_is_speaking_shot_not_silent(self):
        """有台词的节拍(含 reaction/info)必须落到能张嘴的景别，杜绝 SILENT_HAS_AUDIO。"""
        from director.strategy import _plan_shots_from_beats
        scene = {"scene_id": "S1", "characters": ["a"], "props": [],
                 "prop_states": {}, "beat_type": "CONFLICT", "mood": "压力",
                 "narrative": {}}
        beats = [
            {"type": "reaction", "seconds": 2, "description": "冷笑回击",
             "character_id": "a", "line": "你完了", "emotion": "冷"},
            {"type": "reaction", "seconds": 2, "description": "别过头",
             "character_id": None, "line": "", "emotion": "冷"},
        ]
        shots = _plan_shots_from_beats(scene, beats, seed="s", shot_index_start=1)
        assert shots[0]["shot_type"] == "CLOSEUP" and shots[0]["dialogue"]
        assert shots[1]["shot_type"] == "REACTION" and not shots[1]["dialogue"]
        # 全片：任何带台词的镜都不是无声景别
        for s in shots:
            if s["dialogue"]:
                assert s["shot_type"] not in ("REACTION", "INSERT", "ESTABLISHING")
