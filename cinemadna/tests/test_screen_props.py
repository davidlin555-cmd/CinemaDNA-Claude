"""屏幕内容道具 (Phase 2b) 测试 —— 分类 / HTML 模板 / Chrome 截图 / PropDNA 路由

全程用**注入的假 Chrome runner**（把 argv 里的 --screenshot 路径写成合法 PNG），
不启动真实浏览器、不联网、不花钱。验证屏幕道具走模板截图（文字来自 HTML，
可读、零乱码），物理道具仍走 FLUX，两条路对下游字段一致。
"""

from __future__ import annotations

from pathlib import Path

from asset_brain.common.bundle import Bundle
from asset_brain.common.quad import Quad
from asset_brain.facade import OUTCOME_BACKFLOWED, AssetBrainFacade
from asset_brain.prop_dna.screen_templates import (
    classify_screen_prop,
    render_screen_html,
)
from assets_real.screen_render import ChromeScreenshotBackend

# 一张够大的合法 PNG（>500 字节）
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1500


def fake_runner(argv):
    """假 Chrome：把 --screenshot=<path> 写成合法 PNG，模拟成功截图。"""
    shot = next(a.split("=", 1)[1] for a in argv if a.startswith("--screenshot="))
    Path(shot).write_bytes(_PNG)


def screen_backend():
    return ChromeScreenshotBackend(chrome_path="fake-chrome", runner=fake_runner)


def ctx(task="task_propdna_001"):
    return Quad(story_id="drama_0005", bundle_id="bundle_20260724_001",
                task_id=task, asset_hash=None)


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------


class TestClassify:
    def test_phone_chat(self):
        assert classify_screen_prop({"name": "催债短信"}) == "phone_chat"

    def test_payment(self):
        assert classify_screen_prop({"name": "手机转账记录"}) == "payment"

    def test_contract(self):
        assert classify_screen_prop({"name": "借款协议书"}) == "contract"

    def test_recording(self):
        assert classify_screen_prop({"name": "通话录音"}) == "recording"

    def test_notification(self):
        # 通知/推送/未接来电 → 专用锁屏推送界面（不再落到聊天线程）
        assert classify_screen_prop({"name": "催债通知消息"}) == "notification"
        assert classify_screen_prop({"name": "未接来电推送"}) == "notification"

    def test_notification_template_renders_text(self):
        html = render_screen_html("notification", {"name": "催债通知"}, "默认")
        assert "转账提醒" in html and "未接来电" in html   # 关键信息可辨认
        assert "&lt;" not in html.replace("&lt;script", "")  # 无注入

    def test_physical_prop_is_none(self):
        assert classify_screen_prop({"name": "医院缴费单", "description": "纸质单据"}) \
            is None or classify_screen_prop({"name": "一把水果刀"}) is None

    def test_payment_beats_phone(self):
        """'手机付款界面' 同时含手机+付款 → 判 payment（更具体优先）。"""
        assert classify_screen_prop({"name": "手机付款界面"}) == "payment"


# ---------------------------------------------------------------------------
# HTML 模板：文字来自 HTML（可读、可断言），且被转义
# ---------------------------------------------------------------------------


class TestTemplates:
    def test_payment_content_and_state(self):
        req = {"name": "转账", "screen_content": {"amount": "58,000.00"}}
        html_ok = render_screen_html("payment", req, "成功")
        html_late = render_screen_html("payment", req, "逾期")
        assert "58,000.00" in html_ok
        assert "支付成功" in html_ok
        assert "已逾期" in html_late          # 状态改变屏内文案

    def test_contract_signed_state(self):
        req = {"name": "借款协议书"}
        signed = render_screen_html("contract", req, "已签字")
        blank = render_screen_html("contract", req, "空白")
        assert "合同专用章" in signed          # 签字态盖章
        assert "合同专用章" not in blank

    def test_screen_content_override_escaped(self):
        req = {"name": "短信", "screen_content": {
            "messages": [{"from": "them", "text": "<script>x</script>", "time": "1"}]}}
        html = render_screen_html("phone_chat", req, "未读")
        assert "<script>x</script>" not in html      # 被 HTML 转义
        assert "&lt;script&gt;" in html

    def test_recording_states(self):
        req = {"name": "通话录音"}
        assert "录音中" in render_screen_html("recording", req, "录音中")
        assert "已保存" in render_screen_html("recording", req, "已保存")


# ---------------------------------------------------------------------------
# Chrome 截图后端（注入假 runner）
# ---------------------------------------------------------------------------


class TestChromeBackend:
    def test_renders_png_to_chinese_path(self, tmp_path):
        be = screen_backend()
        assert be.available() is True
        dest = tmp_path / "04_props" / "p1" / "已读.png"   # 中文文件名
        r = be.render(html="<b>hi</b>", dest=dest, kind="phone_chat")
        assert dest.exists() and dest.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        assert r.bytes > 500 and r.is_real_media is True
        # 中转临时文件应被清理
        assert not list(tmp_path.glob("**/_screensrc_*.html"))
        assert not list(tmp_path.glob("**/_screenshot_*.png"))

    def test_unavailable_backend_reports(self):
        be = screen_backend()
        be.chrome_path = None                # 模拟本机无 Chrome
        assert be.available() is False


# ---------------------------------------------------------------------------
# PropDNA 路由：屏幕道具走模板，物理道具走 FLUX
# ---------------------------------------------------------------------------


class FakeImageHttp:
    import base64 as _b64
    JPG = _b64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 1500).decode()

    def __call__(self, method, url, headers, body):
        return 200, {"data": [{"b64_json": self.JPG}]}


def flux_backend():
    from assets_real.image_backend import TogetherImageBackend
    from render.budget import BudgetGate, confirm_yes
    return TogetherImageBackend(
        budget=BudgetGate(max_units=5.0, confirm=confirm_yes),
        api_key="k", http=FakeImageHttp())


PHONE_PROP = {"prop_id": "prop_debt_sms", "name": "催债短信",
              "states_needed": ["未读", "已读"], "continuity_critical": True}


class TestPropRouting:
    def test_screen_prop_uses_template(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        fac = AssetBrainFacade(bundle=bundle, screen_backend=screen_backend())
        res = fac.request_prop(PHONE_PROP, ctx(),
                               state_timeline=[{"shot_id": "s1", "state": "未读"}])
        assert res["outcome"] == OUTCOME_BACKFLOWED
        rec = fac.store.prop_library["prop_debt_sms"]
        assert rec["is_real_image"] is True
        assert rec["prop_render_method"] == "template_screenshot"
        assert rec["screen_kind"] == "phone_chat"
        for state, rel in (rec["prop_images_by_state"] or {}).items():
            assert bundle.path_for(rel).exists()

    def test_screen_prop_without_chrome_marks_missing(self, tmp_path):
        """是屏幕道具但没 Chrome 后端 → 不用 FLUX 出乱码，如实标缺失。"""
        bundle = Bundle(tmp_path, "b1").ensure()
        fac = AssetBrainFacade(bundle=bundle, image_backend=flux_backend())
        fac.request_prop(PHONE_PROP, ctx(),
                         state_timeline=[{"shot_id": "s1", "state": "未读"}])
        rec = fac.store.prop_library["prop_debt_sms"]
        assert rec["is_real_image"] is False
        assert rec.get("real_prop_errors")

    def test_physical_prop_uses_flux(self, tmp_path):
        bundle = Bundle(tmp_path, "b1").ensure()
        fac = AssetBrainFacade(bundle=bundle, image_backend=flux_backend(),
                               screen_backend=screen_backend())
        phys = {"prop_id": "prop_knife", "name": "水果刀",
                "states_needed": ["完整"], "continuity_critical": False}
        fac.request_prop(phys, ctx(),
                         state_timeline=[{"shot_id": "s1", "state": "完整"}])
        rec = fac.store.prop_library["prop_knife"]
        assert rec["screen_kind"] is None
        assert rec["prop_render_method"] == "flux_text2image"
        assert rec["is_real_image"] is True
        # FLUX 返回 JPEG → 后缀纠正为 .jpg
        assert any(v.endswith(".jpg") for v in rec["prop_images_by_state"].values())
