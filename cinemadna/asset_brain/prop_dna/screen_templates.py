"""屏幕内容道具 HTML 模板 (Phase 2b) —— 手机界面 / 付款记录 / 合同 / 录音界面

短剧最高频的四类屏幕道具，各一套 HTML/CSS 模板，由道具需求 + 状态填充：

  - phone_chat  手机聊天/短信/通知（催债短信、对话截图…）
  - payment     付款/转账/缴费记录（转账成功、账单、逾期…）
  - contract    合同/协议/借条（条款、甲乙方、签名日期…）
  - recording   录音界面（波形、计时、录音按钮…）

内容优先取 requirement["screen_content"]（精确控制），否则由 name/description/
story_function + state 推导可信默认值。全部为**虚构**内容、不含真实品牌标识
（避免版权/肖像风险），文字为矢量渲染 → 清晰可读、零乱码。
"""

from __future__ import annotations

import html
from typing import Any

# 分类关键词（按优先级：先 payment/recording/contract，再兜底 phone_chat）
_SCREEN_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("payment", ("付款", "缴费", "转账", "账单", "收款", "交易", "支付", "汇款",
                 "payment", "bill", "invoice", "transfer")),
    ("recording", ("录音", "voice", "audio", "record", "语音")),
    ("contract", ("合同", "协议", "借条", "欠条", "契约", "contract", "agreement")),
    # 通知/推送/来电/未接 → 专用锁屏推送通知界面（区别于聊天线程）
    ("notification", ("通知", "推送", "提醒", "未接", "来电", "弹窗", "锁屏",
                      "notification", "push", "alert", "banner")),
    ("phone_chat", ("手机", "短信", "消息", "聊天", "微信", "对话",
                    "phone", "screen", "屏幕", "message", "chat", "sms")),
]


def classify_screen_prop(requirement: dict[str, Any]) -> str | None:
    """判断道具是否屏幕内容类；是则返回模板种类，否则 None（走物理道具 FLUX）。"""
    pid = str(requirement.get("prop_id", ""))
    name = str(requirement.get("name", ""))
    desc = str(requirement.get("description", ""))
    fn = str(requirement.get("story_function", ""))
    hay = f"{pid} {name} {desc} {fn}".lower()
    for kind, kws in _SCREEN_KEYWORDS:
        if any(k in hay for k in kws):
            return kind
    return None


def _esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


_BASE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:768px;height:1024px}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;
  -webkit-font-smoothing:antialiased;overflow:hidden}
.statusbar{height:40px;display:flex;align-items:center;justify-content:space-between;
  padding:0 26px;font-size:24px;font-weight:600}
.dots{letter-spacing:3px}
"""


def _page(css: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>"
            f"{_BASE_CSS}{css}</style></head><body>{body}</body></html>")


def _statusbar(clock: str, dark: bool = False) -> str:
    col = "#fff" if dark else "#111"
    return (f"<div class='statusbar' style='color:{col}'>"
            f"<span>{_esc(clock)}</span>"
            f"<span class='dots'>●●●●  5G  ▮▮▮▮</span></div>")


# ---------------------------------------------------------------------------
# 1. 手机聊天 / 短信 / 通知
# ---------------------------------------------------------------------------

def _phone_chat(req: dict[str, Any], state: str, sc: dict[str, Any]) -> str:
    contact = sc.get("contact") or req.get("name") or "陌生号码 138****6021"
    clock = sc.get("clock") or "23:41"
    read = "已读" in state or sc.get("read")
    msgs = sc.get("messages") or [
        {"from": "them", "text": "钱什么时候还？已经拖三个月了。", "time": "23:36"},
        {"from": "them", "text": "今晚十二点前不转，明天就去你单位。", "time": "23:38"},
        {"from": "me", "text": "再给我两天，工资一发就还。", "time": "23:40"},
        {"from": "them", "text": "别拿工资糊弄我。¥58,000，一分不能少。", "time": "23:41"},
    ]
    bubbles = []
    for m in msgs:
        me = m.get("from") == "me"
        side = "flex-end" if me else "flex-start"
        bg = "#95ec69" if me else "#fff"
        bubbles.append(
            f"<div style='display:flex;justify-content:{side};margin:14px 24px'>"
            f"<div style='max-width:70%;background:{bg};color:#111;font-size:30px;"
            f"line-height:1.4;padding:18px 22px;border-radius:16px;"
            f"box-shadow:0 1px 2px rgba(0,0,0,.08)'>{_esc(m.get('text'))}"
            f"<div style='font-size:18px;color:#8a8a8a;margin-top:8px;text-align:right'>"
            f"{_esc(m.get('time',''))}{' · 已读' if me and read else ''}</div></div></div>")
    css = "body{background:#ededed}.hdr{height:88px;background:#f7f7f7;display:flex;" \
          "align-items:center;justify-content:center;font-size:32px;font-weight:600;" \
          "color:#111;border-bottom:1px solid #dcdcdc}" \
          ".bar{position:absolute;bottom:0;width:768px;height:96px;background:#f7f7f7;" \
          "border-top:1px solid #dcdcdc;display:flex;align-items:center;padding:0 24px;" \
          "color:#9a9a9a;font-size:28px}"
    body = (_statusbar(clock) + f"<div class='hdr'>{_esc(contact)}</div>"
            + "".join(bubbles)
            + "<div class='bar'>输入消息…</div>")
    return _page(css, body)


# ---------------------------------------------------------------------------
# 2. 付款 / 转账 / 缴费记录
# ---------------------------------------------------------------------------

def _payment(req: dict[str, Any], state: str, sc: dict[str, Any]) -> str:
    amount = sc.get("amount") or "58,000.00"
    payee = sc.get("payee") or "王建国"      # 收款方是人名，别用道具名兜底
    clock = sc.get("clock") or "23:41"
    # 状态映射
    if any(k in state for k in ("逾期", "待", "未")):
        status, color, icon = "待支付 · 已逾期", "#e8452b", "!"
    elif any(k in state for k in ("失败", "驳回")):
        status, color, icon = "支付失败", "#e8452b", "×"
    else:
        status, color, icon = "支付成功", "#09a552", "✓"
    rows = sc.get("details") or [
        ("收款方", _esc(payee)),
        ("付款方式", "储蓄卡（尾号 6021）"),
        ("交易时间", "2026-07-24 23:41:12"),
        ("交易单号", "20260724234112008861"),
        ("备注", "医院缴费 / 借款偿还"),
    ]
    rows_html = "".join(
        f"<div style='display:flex;justify-content:space-between;padding:26px 0;"
        f"border-bottom:1px solid #f0f0f0;font-size:28px'>"
        f"<span style='color:#9a9a9a'>{_esc(k)}</span>"
        f"<span style='color:#333;max-width:60%;text-align:right'>{_esc(v)}</span></div>"
        for k, v in rows)
    css = "body{background:#f5f6f8}.top{height:120px;display:flex;align-items:center;" \
          "padding:0 30px;font-size:34px;font-weight:600;color:#111}" \
          ".card{margin:20px 28px;background:#fff;border-radius:24px;padding:50px 40px}"
    body = (_statusbar(clock) + "<div class='top'>‹ 账单详情</div>"
            + "<div class='card'>"
            + f"<div style='text-align:center;margin-bottom:20px'>"
            + f"<div style='width:104px;height:104px;border-radius:50%;background:{color};"
            + f"color:#fff;font-size:64px;line-height:104px;margin:0 auto 24px'>{icon}</div>"
            + f"<div style='font-size:34px;color:{color};font-weight:600'>{status}</div>"
            + f"<div style='font-size:72px;font-weight:700;color:#111;margin-top:24px'>"
            + f"¥{_esc(amount)}</div></div>"
            + f"<div style='margin-top:20px'>{rows_html}</div></div>")
    return _page(css, body)


# ---------------------------------------------------------------------------
# 3. 合同 / 协议 / 借条
# ---------------------------------------------------------------------------

def _contract(req: dict[str, Any], state: str, sc: dict[str, Any]) -> str:
    title = sc.get("title") or req.get("name") or "借款协议书"
    party_a = sc.get("party_a") or "甲方（出借人）：王建国"
    party_b = sc.get("party_b") or "乙方（借款人）：林 晚"
    clauses = sc.get("clauses") or [
        "一、乙方向甲方借款人民币伍万捌仟元整（¥58,000.00）。",
        "二、借款期限自 2026 年 4 月 24 日起至 2026 年 7 月 24 日止。",
        "三、月利率为百分之二（2%），到期一次性还本付息。",
        "四、乙方逾期未还，甲方有权要求乙方以工资收入优先偿还。",
        "五、本协议一式两份，甲乙双方各执一份，自签字之日起生效。",
    ]
    signed = "签" in state or "已" in state or sc.get("signed")
    sig_b = "林 晚" if signed else "＿＿＿＿＿"
    date = "2026 年 4 月 24 日" if signed else "＿＿ 年 ＿＿ 月 ＿＿ 日"
    stamp = ("<div style='position:absolute;right:70px;bottom:70px;width:150px;"
             "height:150px;border:5px solid #c62828;border-radius:50%;color:#c62828;"
             "display:flex;align-items:center;justify-content:center;text-align:center;"
             "font-size:23px;font-weight:700;transform:rotate(-14deg);opacity:.8'>"
             "合同专用章</div>") if signed else ""
    clause_html = "".join(
        f"<p style='font-size:27px;line-height:1.75;color:#222;margin:14px 0'>{_esc(c)}</p>"
        for c in clauses)
    css = "body{background:#e7e2d6}.paper{position:absolute;top:44px;left:40px;" \
          "width:688px;height:936px;background:#fffdf7;padding:56px 56px;" \
          "box-shadow:0 8px 30px rgba(0,0,0,.2)}" \
          ".sign{position:absolute;left:56px;right:56px;bottom:64px;font-size:27px;color:#222}"
    body = ("<div class='paper'>"
            + f"<h1 style='text-align:center;font-size:42px;color:#111;margin-bottom:30px;"
            + f"letter-spacing:4px'>{_esc(title)}</h1>"
            + f"<p style='font-size:27px;color:#222;margin:8px 0'>{_esc(party_a)}</p>"
            + f"<p style='font-size:27px;color:#222;margin:8px 0 20px'>{_esc(party_b)}</p>"
            + clause_html
            + f"<div class='sign'>"
            + f"<div style='display:flex;justify-content:space-between'>"
            + f"<span>甲方签字：王建国</span><span>乙方签字：{_esc(sig_b)}</span></div>"
            + f"<div style='margin-top:22px'>签订日期：{_esc(date)}</div>"
            + f"</div>{stamp}</div>")
    return _page(css, body)


# ---------------------------------------------------------------------------
# 4. 录音界面
# ---------------------------------------------------------------------------

def _recording(req: dict[str, Any], state: str, sc: dict[str, Any]) -> str:
    name = sc.get("title") or req.get("name") or "新录音 07"
    clock = sc.get("clock") or "23:41"
    if any(k in state for k in ("保存", "停", "完成", "已")):
        running, status, timer = False, "已保存", sc.get("timer") or "02:47"
    else:
        running, status, timer = True, "录音中", sc.get("timer") or "00:23"
    # 静态波形（一排高低不一的竖条，围绕中线上下对称）
    heights = [30, 64, 120, 48, 150, 90, 40, 180, 72, 130, 56, 160, 44, 110, 84,
               150, 36, 96, 140, 60, 120, 50, 170, 78, 132]
    wcol = "#ff4d4f" if running else "#5a6472"
    bars = "".join(
        f"<span style='display:inline-block;width:10px;height:{h}px;margin:0 4px;"
        f"background:{wcol};border-radius:6px;vertical-align:middle'></span>"
        for h in heights)
    btn = ("#ff4d4f", "■") if running else ("#3a7afe", "●")
    css = "body{background:#0e1116;color:#fff;position:relative}" \
          ".c{position:absolute;left:0;width:768px;text-align:center}"
    body = (_statusbar(clock, dark=True)
            + f"<div class='c' style='top:120px;font-size:38px;font-weight:600'>"
            + f"{_esc(name)}</div>"
            + f"<div class='c' style='top:186px;font-size:28px;"
            + f"color:{'#ff4d4f' if running else '#8a94a3'}'>● {status}</div>"
            + f"<div class='c' style='top:330px;font-size:120px;font-weight:300;"
            + f"font-variant-numeric:tabular-nums;letter-spacing:4px'>{_esc(timer)}</div>"
            + f"<div class='c' style='top:560px;height:190px;line-height:190px;"
            + f"white-space:nowrap;overflow:hidden'>{bars}</div>"
            + f"<div class='c' style='bottom:110px'>"
            + f"<div style='width:150px;height:150px;border-radius:50%;background:{btn[0]};"
            + f"color:#fff;font-size:60px;line-height:150px;margin:0 auto'>{btn[1]}</div>"
            + f"<div style='margin-top:24px;font-size:26px;color:#8a94a3'>"
            + f"轻触{'停止' if running else '开始'}录音</div></div>")
    return _page(css, body)


# ---------------------------------------------------------------------------
# 5. 锁屏推送通知界面（区别于聊天线程：横幅式推送 + 大时钟）
# ---------------------------------------------------------------------------

def _notification(req: dict[str, Any], state: str, sc: dict[str, Any]) -> str:
    clock = sc.get("clock") or "23:41"
    date = sc.get("date") or "7月24日 星期四"
    # 默认一屏催债主题推送；可由 screen_content["items"] 覆盖
    items = sc.get("items") or [
        {"app": "银行", "icon": "🏦", "ago": "现在",
         "title": "转账提醒", "body": "您尾号6021的储蓄卡支出 ¥58,000.00，余额 ¥312.7。"},
        {"app": "电话", "icon": "📞", "ago": "9分钟前",
         "title": "未接来电（3）", "body": "王建国  138****6021"},
        {"app": "信息", "icon": "💬", "ago": "12分钟前",
         "title": "王建国", "body": "今晚十二点前不到账，明天就去你们医院找你。"},
        {"app": "微信", "icon": "💚", "ago": "20分钟前",
         "title": "微信支付", "body": "转账 ¥58,000 已被对方接收。"},
    ]
    cards = []
    for it in items:
        cards.append(
            "<div style='margin:14px 24px;background:rgba(255,255,255,.14);"
            "backdrop-filter:blur(8px);border-radius:22px;padding:22px 24px'>"
            "<div style='display:flex;align-items:center;gap:12px;font-size:24px;"
            "color:#e8e8ea;margin-bottom:10px'>"
            f"<span style='font-size:30px'>{_esc(it.get('icon','🔔'))}</span>"
            f"<b style='font-weight:600'>{_esc(it.get('app',''))}</b>"
            f"<span style='margin-left:auto;color:#c7c7cc'>{_esc(it.get('ago',''))}</span>"
            "</div>"
            f"<div style='font-size:30px;color:#fff;font-weight:600'>{_esc(it.get('title',''))}</div>"
            f"<div style='font-size:27px;color:#e2e2e6;line-height:1.4;margin-top:6px'>"
            f"{_esc(it.get('body',''))}</div></div>")
    css = ("body{background:linear-gradient(160deg,#1a2233,#0b0e16);color:#fff;"
           "position:relative}")
    body = (_statusbar(clock, dark=True)
            + "<div style='text-align:center;margin-top:70px'>"
            + f"<div style='font-size:130px;font-weight:300;letter-spacing:2px'>{_esc(clock)}</div>"
            + f"<div style='font-size:30px;color:#c7c7cc;margin-top:4px'>{_esc(date)}</div></div>"
            + "<div style='margin-top:60px'>" + "".join(cards) + "</div>")
    return _page(css, body)


_RENDERERS = {
    "phone_chat": _phone_chat,
    "payment": _payment,
    "contract": _contract,
    "recording": _recording,
    "notification": _notification,
}


def render_screen_html(kind: str, requirement: dict[str, Any], state: str) -> str:
    """渲染指定种类的屏幕道具 HTML。screen_content 覆盖优先。"""
    fn = _RENDERERS.get(kind)
    if fn is None:
        raise KeyError(f"未知屏幕道具种类: {kind}")
    sc = dict(requirement.get("screen_content") or {})
    # 支持按状态给不同内容：screen_content["by_state"][state]
    by_state = sc.pop("by_state", None) or {}
    if state in by_state and isinstance(by_state[state], dict):
        sc.update(by_state[state])
    return fn(requirement, state, sc)


__all__ = ["classify_screen_prop", "render_screen_html"]
