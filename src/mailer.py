# src/mailer.py
# v11 — 全 HTML 專業版 Email（修復 taiex/margin/tdcc 缺失渲染，統一視覺風格）
# ─────────────────────────────────────────────────────────────────────────────
# 修正清單：
#   1. 新增 大盤指數(TAIEX) HTML 表格渲染
#   2. 新增 融資融券 HTML 表格渲染
#   3. 新增 千張大戶(TDCC) HTML 卡片渲染
#   4. 修正期貨表格（補上 header row）
#   5. AI 分析：支援 **bold** markdown、E) 一句話摘要獨立卡片
#   6. 統一配色系統、版面結構更清晰

import os
import json
import glob
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  通用工具函式
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def load_summary():
    path = os.path.join("output", "summary.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "generated_at": datetime.now(TZ).isoformat(),
        "timezone": "Asia/Taipei",
        "days": int(os.getenv("DAYS", "5")),
        "success": False,
        "errors": ["summary.json 不存在"],
        "total_rows": 0, "brokers_total": 0, "brokers_ok": 0, "brokers_fail": 0,
        "top_preview": [], "ai_analysis": "",
    }


def pick_latest(pattern: str):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _fi(n):
    """格式化整數（千分位）"""
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _fb(n):
    """格式化帶正負號、億/萬換算的買賣超數字"""
    try:
        v = int(n)
        if abs(v) >= 1_000_000_000:
            return f"{v / 1e9:+.2f}B"
        elif abs(v) >= 1_0000_0000:
            return f"{v / 1e8:+.1f}億"
        elif abs(v) >= 1_0000:
            return f"{v / 1e4:+.0f}萬"
        return f"{v:+,}"
    except Exception:
        return str(n)


def _fbi(n):
    """格式化不帶億換算的整數（口數/張數，千分位）"""
    try:
        v = int(n)
        if v > 0:
            return f"+{v:,}"
        return f"{v:,}"
    except Exception:
        return str(n)


def _color(val, zero_color="#555"):
    """根據數值正負回傳台股慣例顏色（正=紅, 負=綠）"""
    try:
        raw = str(val).replace(",", "").replace("%", "").replace("億", "").replace("萬", "").replace("+", "").strip()
        v = float(raw)
        if v > 0:
            return "#C0392B"  # 台股紅
        elif v < 0:
            return "#27AE60"  # 台股綠
    except Exception:
        pass
    return zero_color


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _md_inline(text: str) -> str:
    """將 **bold** 和 *italic* markdown 轉為 HTML（用於 AI 輸出後處理）"""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*([^*]+?)\*', r'<em>\1</em>', text)
    return text


def _style_keywords(text: str) -> str:
    """高亮操作關鍵字（在 _esc 後調用）"""
    kws = {
        "進場": "#2E86C1", "停損": "#E74C3C", "了結": "#8E44AD",
        "突破": "#2980B9", "回測": "#E67E22", "不跌破": "#27AE60",
        "放量": "#C0392B", "縮量": "#27AE60", "偏多": "#C0392B",
        "偏空": "#27AE60", "觀察": "#7F8C8D", "風險": "#E74C3C",
        "連買": "#C0392B", "連賣": "#27AE60", "中性": "#7F8C8D",
        "高度集中": "#8E44AD", "強勢佈局": "#C0392B", "逢低承接": "#2980B9",
        "共識度高": "#C0392B", "被套": "#E74C3C",
    }
    for kw, c in kws.items():
        text = text.replace(kw, f'<b style="color:{c};">{kw}</b>')
    return text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  共用 HTML 元件（專業版）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTION_COLORS = {
    "blue":   {"bg": "#1A5276", "hdr": "#1F618D", "light": "#EBF5FB", "border": "#AED6F1"},
    "navy":   {"bg": "#1A2A3A", "hdr": "#2C3E50", "light": "#EAF0F6", "border": "#AEB6BF"},
    "gold":   {"bg": "#7D6608", "hdr": "#9A7D0A", "light": "#FEF9E7", "border": "#F9E79F"},
    "green":  {"bg": "#1A6B3A", "hdr": "#1E8449", "light": "#EAFAF1", "border": "#A9DFBF"},
    "red":    {"bg": "#7B241C", "hdr": "#922B21", "light": "#FDEDEC", "border": "#F5B7B1"},
    "purple": {"bg": "#5B2C6F", "hdr": "#6C3483", "light": "#F4ECF7", "border": "#D7BDE2"},
    "teal":   {"bg": "#0E6655", "hdr": "#148F77", "light": "#E8F8F5", "border": "#A2D9CE"},
    "gray":   {"bg": "#424949", "hdr": "#515A5A", "light": "#F2F3F4", "border": "#BFC9CA"},
}


def _fmt_date(d: str) -> str:
    """20260330 → 2026/03/30"""
    d = str(d).strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}/{d[4:6]}/{d[6:]}"
    return d


def _arrow(v) -> str:
    """回傳趨勢箭頭 HTML（▲紅 / ▼綠）"""
    try:
        n = float(str(v).replace(",", ""))
        if n > 0:
            return '<span style="color:#C0392B;font-size:10px;"> ▲</span>'
        elif n < 0:
            return '<span style="color:#27AE60;font-size:10px;"> ▼</span>'
    except Exception:
        pass
    return ""


def _badge(text: str, bg: str, color: str = "#fff", px: int = 7) -> str:
    return (
        f'<span style="display:inline-block;padding:1px {px}px;background:{bg};'
        f'color:{color};border-radius:3px;font-size:10.5px;font-weight:700;'
        f'vertical-align:middle;white-space:nowrap;">{text}</span>'
    )


def _sec_hdr(icon: str, title: str, color_key: str = "blue", subtitle: str = "") -> str:
    c = SECTION_COLORS.get(color_key, SECTION_COLORS["blue"])
    sub_html = (
        f'<span style="font-size:11px;color:rgba(255,255,255,.7);'
        f'margin-left:10px;font-weight:400;">{_esc(subtitle)}</span>'
        if subtitle else ""
    )
    return (
        f'<div style="margin:22px 0 0;padding:10px 16px;'
        f'background:linear-gradient(90deg,{c["bg"]},{c["hdr"]});'
        f'border-radius:5px 5px 0 0;border-left:4px solid rgba(255,255,255,.35);">'
        f'<span style="font-size:13.5px;font-weight:700;color:#fff;letter-spacing:.5px;">'
        f'{icon}&nbsp; {_esc(title)}</span>{sub_html}</div>'
    )


def _table_open(col_styles: list = None) -> str:
    """開啟專業表格，可選 colgroup 寬度。col_styles = [("60px",""), ("auto",""), ...]"""
    cols = ""
    if col_styles:
        cols = "<colgroup>" + "".join(
            f'<col style="width:{w};{s}">' for w, s in col_styles
        ) + "</colgroup>"
    return (
        f'<table style="width:100%;border-collapse:collapse;font-size:12.5px;'
        f'border:1px solid #D5D8DC;border-top:none;margin-bottom:0;">{cols}'
    )


TABLE_CLOSE = "</table>"


def _th_row(*cols, bg: str = "#2C3E50", color: str = "#ECF0F1") -> str:
    """表頭列 — cols = (label, align) tuples"""
    cells = "".join(
        f'<th style="padding:7px 11px;text-align:{align};background:{bg};'
        f'color:{color};font-size:11.5px;font-weight:600;'
        f'white-space:nowrap;border-right:1px solid rgba(255,255,255,.12);">'
        f'{label}</th>'
        for label, align in cols
    )
    return f"<tr>{cells}</tr>"


def _td(text, align="left", bold=False, color="", extra="", border=True) -> str:
    b  = "font-weight:700;" if bold else ""
    c  = f"color:{color};" if color else ""
    br = "border-right:1px solid #EAECEE;" if border else ""
    bt = "border-top:1px solid #EAECEE;"
    return (
        f'<td style="padding:6px 11px;text-align:{align};{bt}{br}{b}{c}{extra}">'
        f'{text}</td>'
    )


def _tr(*cells, bg="#FFF", today=False) -> str:
    if today:
        bg = "#FFFDE7"  # 今日列淡黃高亮
    return f'<tr style="background:{bg};">{"".join(cells)}</tr>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  大盤資料 HTML 渲染（專業版）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_taiex(taiex: list) -> str:
    if not taiex:
        return ""
    # 計算5日均量（用第2~6筆）供量能比較
    amts = [d.get("amount_billion", 0) for d in taiex]
    avg5 = sum(amts[1:6]) / max(len(amts[1:6]), 1) if len(amts) > 1 else 0

    hdr = _sec_hdr("📊", "大盤指數 ＋ 成交量", "navy", "近 6 日")
    tbl = _table_open([("100px",""), ("90px",""), ("90px",""), ("100px",""), ("90px","")])
    tbl += _th_row(
        ("日期", "left"), ("收盤指數", "right"), ("漲跌點", "right"),
        ("成交金額", "right"), ("量比(5日均)", "right"),
        bg="#2C3E50",
    )
    for i, d in enumerate(taiex[:6]):
        chg   = d.get("change", 0)
        close = d.get("close", 0)
        amt   = d.get("amount_billion", 0)
        chg_c = _color(chg)
        sign  = "+" if chg > 0 else ""
        chg_s = f"{sign}{chg:,.2f}" if isinstance(chg, float) else f"{sign}{chg}"

        # 量比
        ratio     = amt / avg5 if avg5 > 0 else 0
        ratio_s   = f"{ratio:.2f}x" if avg5 > 0 else "—"
        ratio_c   = "#C0392B" if ratio > 1.2 else "#27AE60" if ratio < 0.8 else "#555"
        ratio_txt = (
            f'<span style="color:{ratio_c};font-weight:{"700" if i==0 else "400"};">'
            f'{ratio_s}</span>'
            + (_badge("放量", "#E74C3C") if ratio > 1.2 and i == 0 else
               _badge("縮量", "#27AE60") if ratio < 0.8 and i == 0 else "")
        )

        # 今日標籤
        date_html = _fmt_date(d.get("date", ""))
        if i == 0:
            date_html += "&nbsp;" + _badge("最新", "#1A5276")

        close_s = f"{close:,.2f}" if isinstance(close, float) else str(close)
        bg      = "#FFFDE7" if i == 0 else ("#F8F9FA" if i % 2 else "#FFF")

        tbl += _tr(
            _td(date_html, bold=(i == 0)),
            _td(f'<span style="font-weight:{"700" if i==0 else "600"};color:#333;">{close_s}</span>', "right"),
            _td(f'<span style="color:{chg_c};font-weight:{"700" if i==0 else "500"};">{chg_s}</span>{_arrow(chg)}', "right"),
            _td(f'<span style="{"font-weight:700;" if i==0 else ""}">{int(amt):,} 億</span>', "right"),
            _td(ratio_txt, "right"),
            bg=bg,
        )
    tbl += TABLE_CLOSE
    return hdr + tbl


def _render_institutional(inst: list) -> str:
    if not inst:
        return ""
    hdr = _sec_hdr("🏦", "三大法人買賣超（元）", "blue", "近 6 日")
    tbl = _table_open([("100px",""), ("110px",""), ("110px",""), ("110px",""), ("110px","")])
    tbl += _th_row(
        ("日期", "left"), ("外資買賣超", "right"), ("投信買賣超", "right"),
        ("自營買賣超", "right"), ("三大合計", "right"),
        bg="#1F618D",
    )
    for i, d in enumerate(inst[:6]):
        fg  = d["foreign"]["net"]
        tr  = d["trust"]["net"]
        dl  = d["dealer"]["net"]
        tot = d.get("total_net", fg + tr + dl)
        bg  = "#FFFDE7" if i == 0 else ("#F8F9FA" if i % 2 else "#FFF")

        def _mc(v, is_today):
            col  = _color(v)
            bld  = "font-weight:700;" if is_today else "font-weight:500;"
            return _td(
                f'<span style="color:{col};{bld}">{_fb(v)}</span>{_arrow(v) if is_today else ""}',
                "right"
            )

        date_html = _fmt_date(d["date"]) + ("&nbsp;" + _badge("最新", "#1A5276") if i == 0 else "")
        tbl += _tr(
            _td(date_html, bold=(i == 0)),
            _mc(fg, i == 0), _mc(tr, i == 0), _mc(dl, i == 0),
            _td(
                f'<span style="color:{_color(tot)};font-weight:{"800" if i==0 else "600"};">'
                f'{_fb(tot)}</span>{_arrow(tot) if i==0 else ""}',
                "right", bold=(i == 0)
            ),
            bg=bg,
        )
    tbl += TABLE_CLOSE
    return hdr + tbl


def _render_margin(margin: list) -> str:
    if not margin:
        return ""
    hdr = _sec_hdr("💳", "融資融券（張）", "gray", "近 6 日｜散戶籌碼動向")
    tbl = _table_open([
        ("100px",""), ("90px",""), ("110px",""),
        ("90px",""), ("110px",""), ("80px",""),
    ])
    tbl += _th_row(
        ("日期", "left"),
        ("融資增減", "right"), ("融資餘額", "right"),
        ("融券增減", "right"), ("融券餘額", "right"),
        ("券資比", "right"),
        bg="#515A5A",
    )
    for i, d in enumerate(margin[:6]):
        mc = d.get("margin_change", 0)
        mb = d.get("margin_balance", 0)
        sc = d.get("short_change", 0)
        sb = d.get("short_balance", 0)
        # 券資比（融券餘額 / 融資餘額）
        sr_ratio = (sb / mb * 100) if mb > 0 else 0
        sr_c     = "#C0392B" if sr_ratio > 15 else "#27AE60" if sr_ratio < 5 else "#555"

        mc_c = "#C0392B" if mc > 0 else "#27AE60" if mc < 0 else "#555"
        sc_c = "#27AE60" if sc > 0 else "#C0392B" if sc < 0 else "#555"
        bg   = "#FFFDE7" if i == 0 else ("#F8F9FA" if i % 2 else "#FFF")

        is_t = (i == 0)
        date_html = _fmt_date(d.get("date","")) + ("&nbsp;" + _badge("最新","#424949") if is_t else "")

        tbl += _tr(
            _td(date_html, bold=is_t),
            _td(f'<span style="color:{mc_c};font-weight:{"700" if is_t else "500"};">'
                f'{_fbi(mc)}</span>{_arrow(mc) if is_t else ""}', "right"),
            _td(f'<span style="{"font-weight:600;" if is_t else "color:#555;"}">{_fi(mb)}</span>', "right"),
            _td(f'<span style="color:{sc_c};font-weight:{"700" if is_t else "500"};">'
                f'{_fbi(sc)}</span>{_arrow(sc) if is_t else ""}', "right"),
            _td(f'<span style="{"font-weight:600;" if is_t else "color:#555;"}">{_fi(sb)}</span>', "right"),
            _td(f'<span style="color:{sr_c};font-weight:{"700" if is_t else "400"};">'
                f'{sr_ratio:.1f}%</span>', "right"),
            bg=bg,
        )
    # 說明列
    tbl += (
        '<tr><td colspan="6" style="padding:5px 11px;font-size:11px;color:#888;'
        'background:#F8F9FA;border-top:1px solid #EAECEE;">'
        '券資比說明：&lt;5% 偏多（軋空潛力高）｜5~15% 中性｜&gt;15% 偏空（放空壓力大）'
        '</td></tr>'
    )
    tbl += TABLE_CLOSE
    return hdr + tbl


def _render_futures(futures: list) -> str:
    if not futures:
        return ""
    hdr = _sec_hdr("📉", "期貨三大法人台指期淨部位（口）", "purple", "近 6 日｜未平倉淨口數")
    tbl = _table_open([("100px",""), ("130px",""), ("130px",""), ("120px","")])
    tbl += _th_row(
        ("日期", "left"),
        ("外資淨口數", "right"), ("投信淨口數", "right"), ("自營淨口數", "right"),
        bg="#6C3483",
    )
    for i, d in enumerate(futures[:6]):
        fg = d.get("foreign_net_oi", 0)
        tr = d.get("trust_net_oi", 0)
        dl = d.get("dealer_net_oi", 0)
        bg = "#FFFDE7" if i == 0 else ("#F8F9FA" if i % 2 else "#FFF")
        is_t = (i == 0)

        def _fc(v):
            col = _color(v)
            bld = "font-weight:700;" if is_t else "font-weight:500;"
            return _td(
                f'<span style="color:{col};{bld}">{_fbi(v)}</span>'
                + (_arrow(v) if is_t else ""), "right"
            )

        date_html = _fmt_date(d.get("date","")) + ("&nbsp;" + _badge("最新","#6C3483") if is_t else "")
        tbl += _tr(_td(date_html, bold=is_t), _fc(fg), _fc(tr), _fc(dl), bg=bg)

    # 說明列
    tbl += (
        '<tr><td colspan="4" style="padding:5px 11px;font-size:11px;color:#888;'
        'background:#F8F9FA;border-top:1px solid #EAECEE;">'
        '正值=淨多單（看漲）｜負值=淨空單（看跌）｜自營商主要以選擇權避險，期貨部位通常接近 0'
        '</td></tr>'
    )
    tbl += TABLE_CLOSE
    return hdr + tbl


def _render_tdcc(tdcc: list) -> str:
    if not tdcc:
        return ""
    hdr = _sec_hdr("🏆", "千張大戶持股比例", "teal", "集保中心資料｜法人籌碼集中度")
    tbl = _table_open([("80px",""), ("90px",""), ("80px",""), ("90px",""), ("80px","")])
    tbl += _th_row(
        ("股票代號", "left"), ("千張以上人數", "right"), ("持股比例", "right"),
        ("400~999張人數", "right"), ("400~999張佔比", "right"),
        bg="#148F77",
    )
    for i, d in enumerate(tdcc):
        bg    = "#F8F9FA" if i % 2 else "#FFF"
        pct   = d.get("pct_1000_plus", 0)
        cnt   = d.get("holders_1000_plus", 0)
        p400  = d.get("pct_400_999", 0)
        c400  = d.get("holders_400_999", 0)
        # 持股>60% = 籌碼高度集中 = 紅色；<40% = 分散 = 綠色
        pct_c = "#C0392B" if pct >= 60 else "#27AE60" if pct < 40 else "#E67E22"
        # 視覺比例 bar（max假設 80%）
        bar_w = min(int(pct / 80 * 60), 60)
        bar   = (
            f'<span style="color:{pct_c};font-weight:700;">{pct:.1f}%</span>'
            f'&nbsp;<span style="display:inline-block;width:{bar_w}px;height:8px;'
            f'background:{pct_c};border-radius:2px;vertical-align:middle;opacity:.7;"></span>'
        )
        tbl += _tr(
            _td(f'<span style="font-weight:700;color:#1A5276;">{_esc(d.get("stock_id",""))}</span>'),
            _td(_fi(cnt), "right"),
            _td(bar, "right"),
            _td(_fi(c400) if c400 else "—", "right", color="#555"),
            _td(f'{p400:.1f}%' if p400 else "—", "right", color="#888"),
            bg=bg,
        )
    # 說明列
    tbl += (
        '<tr><td colspan="5" style="padding:5px 11px;font-size:11px;color:#888;'
        'background:#F8F9FA;border-top:1px solid #EAECEE;">'
        '千張大戶持股 &gt;60% = 籌碼高度集中（支撐強）｜&lt;40% = 籌碼分散（浮額多）'
        '</td></tr>'
    )
    tbl += TABLE_CLOSE
    return hdr + tbl


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  外資分點渲染（專業版）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_broker_block(block: dict, rank: int) -> str:
    broker    = _esc(block.get("broker", ""))
    total_net = block.get("total_net", 0)
    rows      = block.get("rows") or []
    nc        = _color(total_net, "#555")
    medal     = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"#{rank}")

    # 外資券商 header 列
    net_badge_bg = "#C0392B" if total_net > 0 else "#27AE60" if total_net < 0 else "#888"
    header = (
        f'<div style="margin:12px 0 0;padding:9px 14px;'
        f'background:linear-gradient(90deg,#2C3E50,#34495E);'
        f'border-radius:5px 5px 0 0;'
        f'display:flex;align-items:center;justify-content:space-between;">'
        f'<span style="font-size:14px;font-weight:700;color:#ECF0F1;">'
        f'{medal}&nbsp; {broker}</span>'
        f'<span style="font-size:12px;">'
        f'<span style="color:#AAB7B8;">總淨超&nbsp;</span>'
        f'<span style="color:{nc};font-weight:800;font-size:14px;">{_fi(total_net)}</span>'
        f'<span style="color:#AAB7B8;font-size:11px;">&nbsp;張</span>'
        f'</span></div>'
    )

    if not rows:
        return header + '<div style="padding:8px 14px;background:#F8F9FA;border:1px solid #D5D8DC;border-top:none;border-radius:0 0 5px 5px;font-size:12px;color:#888;">本期無明細資料</div>'

    tbl = _table_open([("30px",""), ("auto",""), ("80px",""), ("70px",""), ("70px",""), ("70px","")])
    tbl += _th_row(
        ("#", "center"), ("代號　股票名稱", "left"), ("淨超(張)", "right"),
        ("區間均價", "right"), ("現　　價", "right"), ("乖離率", "right"),
        bg="#1A252F",
    )
    for j, r in enumerate(rows[:5], 1):
        nv       = r.get("net", 0)
        rc       = _color(nv)
        bias_raw = str(r.get("bias", "")).replace("%", "").strip()
        try:
            bv   = float(bias_raw)
            # 乖離率色階：>5% 深紅、1~5% 淺紅、-1~1% 灰、-1~-5% 淺綠、<-5% 深綠
            if bv > 5:
                bias_c, bias_bg = "#922B21", "#FADBD8"
            elif bv > 1:
                bias_c, bias_bg = "#C0392B", "#FDF2F2"
            elif bv < -5:
                bias_c, bias_bg = "#1A5632", "#D5F5E3"
            elif bv < -1:
                bias_c, bias_bg = "#27AE60", "#EAFAF1"
            else:
                bias_c, bias_bg = "#555", "#F8F9FA"
            bias_html = (
                f'<span style="display:inline-block;padding:1px 6px;'
                f'background:{bias_bg};color:{bias_c};border-radius:3px;'
                f'font-size:11.5px;font-weight:700;">{r.get("bias","")}</span>'
            )
        except Exception:
            bias_html = _esc(str(r.get("bias", "")))

        bg = "#FDFEFE" if j % 2 else "#F2F3F4"
        tbl += _tr(
            _td(f'<span style="color:#888;font-size:11px;">{j}</span>', "center"),
            _td(f'<span style="font-weight:700;color:#1A5276;">{_esc(r.get("sid",""))}</span>'
                f'&nbsp;<span style="font-weight:600;color:#2C3E50;">{_esc(r.get("name",""))}</span>'),
            _td(f'<span style="color:{rc};font-weight:700;">{_fi(nv)}</span>', "right"),
            _td(f'<span style="color:#7F8C8D;">{_esc(str(r.get("avg","")))}</span>', "right"),
            _td(f'<span style="font-weight:700;color:#333;">{_esc(str(r.get("price","")))}</span>', "right"),
            _td(bias_html, "right"),
            bg=bg,
        )
    tbl += TABLE_CLOSE
    return header + tbl


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AI 分析 HTML 格式化（v11：支援 **bold**、E) 獨立卡片）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTION_STYLES = {
    # header_bg = 全寬 banner 背景色, content_bg = 內容區淡底色, accent = 左邊線/數字圓圈色
    "A": {"header_bg": "#1A6FA8", "content_bg": "#EBF5FB", "accent": "#2E86C1",
          "icon": "📊", "label": "大盤籌碼環境研判"},
    "B": {"header_bg": "#9A6F00", "content_bg": "#FEF9E7", "accent": "#D4AC0D",
          "icon": "🏦", "label": "外資力量深度剖析"},
    "C": {"header_bg": "#1A7A40", "content_bg": "#EAFAF1", "accent": "#27AE60",
          "icon": "🎯", "label": "明日觀察清單"},
    "D": {"header_bg": "#A93226", "content_bg": "#FDEDEC", "accent": "#E74C3C",
          "icon": "⚠️", "label": "風控與資金配置"},
    "E": {"header_bg": "#6C3483", "content_bg": "#F4ECF7", "accent": "#8E44AD",
          "icon": "💡", "label": "一句話摘要"},
    "F": {"header_bg": "#1A5276", "content_bg": "#EAF2FB", "accent": "#2471A3",
          "icon": "🔍", "label": "外資交叉比對亮點"},
}


def _format_ai_html(ai_text: str) -> str:
    if not ai_text or not ai_text.strip():
        return '<p style="color:#888;font-size:13px;">本次尚未取得 AI 分析。</p>'

    # 錯誤訊息顯示
    if "失敗" in ai_text[:80] or "error" in ai_text[:80].lower():
        return (
            f'<div style="background:#FDF2F2;border-left:4px solid #E74C3C;'
            f'padding:12px 16px;border-radius:4px;color:#922;font-size:13px;">'
            f'{_esc(ai_text[:800])}</div>'
        )

    lines      = ai_text.strip().split("\n")
    html       = []
    in_section = False
    cur_letter = None
    e_summary  = ""

    def _close_section():
        nonlocal in_section
        if in_section:
            html.append('</div></div>')  # close content + wrapper
            in_section = False

    def _pi(raw: str) -> str:
        return _style_keywords(_md_inline(_esc(raw)))

    def _num_badge(num: str, color: str, shape: str = "circle") -> str:
        radius = "50%" if shape == "circle" else "4px"
        return (
            f'<span style="display:inline-block;min-width:22px;height:22px;'
            f'background:{color};color:#fff;border-radius:{radius};text-align:center;'
            f'line-height:22px;font-size:12px;font-weight:700;margin-right:9px;'
            f'vertical-align:middle;">{num}</span>'
        )

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # ══════════════════════════════════════════
        #  Section header  A) … F)
        # ══════════════════════════════════════════
        hm = re.match(r'^([A-F])\s*[)）:：]\s*(.*)', stripped)
        if hm:
            _close_section()
            letter = hm.group(1)
            ai_title = hm.group(2).strip().rstrip("：:").strip()
            st = SECTION_STYLES.get(letter, {
                "header_bg": "#555", "content_bg": "#F5F5F5",
                "accent": "#999", "icon": "▪", "label": ""
            })
            display_title = ai_title if ai_title else st["label"]

            # ── E) 一句話摘要：獨立漸層卡片，不走通用模板 ──
            if letter == "E":
                e_summary = display_title
                html.append(
                    f'<div style="margin:26px 0 4px;">'
                    # 全寬 banner
                    f'<div style="background:linear-gradient(90deg,{st["header_bg"]},#9B59B6);'
                    f'padding:11px 18px;border-radius:6px 6px 0 0;">'
                    f'<span style="font-size:15px;font-weight:800;color:#fff;letter-spacing:.5px;">'
                    f'{st["icon"]}&nbsp; E）{st["label"]}</span></div>'
                    # 摘要卡片內容
                    f'<div style="background:{st["content_bg"]};border:1px solid #D2B4DE;'
                    f'border-top:none;border-radius:0 0 6px 6px;padding:14px 20px;">'
                )
                if display_title:
                    html.append(
                        f'<p style="margin:0;font-size:16px;font-weight:700;color:#4A235A;'
                        f'line-height:1.7;">{_pi(display_title)}</p>'
                    )
                html.append('</div></div>')
                in_section = True
                cur_letter = "E"
                continue

            # ── 通用 Section banner ──
            html.append(
                # 外層 wrapper
                f'<div style="margin:26px 0 0;border-radius:6px;overflow:hidden;'
                f'box-shadow:0 2px 6px rgba(0,0,0,.10);">'
                # ★ 全寬深色 banner 標題
                f'<div style="background:{st["header_bg"]};padding:12px 18px;">'
                f'<span style="font-size:16px;font-weight:800;color:#fff;letter-spacing:.5px;">'
                f'{st["icon"]}&nbsp; {letter}）{_esc(display_title)}</span></div>'
                # 內容區
                f'<div style="background:{st["content_bg"]};padding:12px 18px 16px;'
                f'font-size:13.5px;line-height:1.9;color:#333;">'
            )
            in_section = True
            cur_letter = letter
            continue

        # E) 後面的純文字行（摘要在下一行的情況）
        if cur_letter == "E":
            if not e_summary:
                e_summary = stripped
                html.append(f'<p style="margin:0;font-size:16px;font-weight:700;color:#4A235A;">{_pi(stripped)}</p>')
            continue

        if not in_section:
            html.append(f'<p style="font-size:13px;color:#666;margin:4px 0;">{_pi(stripped)}</p>')
            continue

        st = SECTION_STYLES.get(cur_letter, {"accent": "#555", "content_bg": "#FFF"})
        accent = st["accent"]

        # ══════════════════════════════════════════
        #  編號項目  1) 2) 3)
        # ══════════════════════════════════════════
        nm = re.match(r'^(\d+)\s*[)）.]\s*(.*)', stripped)
        if nm:
            num, text = nm.group(1), nm.group(2)

            if cur_letter == "B":
                # 金色券商名稱卡片
                html.append(
                    f'<div style="margin:14px 0 4px;padding:10px 14px;'
                    f'background:#FFFBEE;border:1px solid #E8C840;border-radius:5px;">'
                    + _num_badge(num, "#D4AC0D", "square")
                    + f'<span style="font-weight:700;font-size:14px;color:#7D6608;'
                    f'vertical-align:middle;">{_pi(text)}</span></div>'
                )
            elif cur_letter == "C":
                stock_m = re.match(r'^(\d{4,5})\s+(.+)', text)
                if stock_m:
                    sid, rest = stock_m.groups()
                    html.append(
                        f'<div style="margin:12px 0 4px;padding:9px 14px;'
                        f'background:#E8F8F5;border-left:4px solid #1ABC9C;border-radius:0 5px 5px 0;">'
                        + _num_badge(num, "#1ABC9C")
                        + f'<span style="font-weight:800;font-size:14px;color:#0E6655;'
                        f'vertical-align:middle;">{_esc(sid)}&nbsp;</span>'
                        f'<span style="font-weight:600;font-size:14px;vertical-align:middle;">'
                        f'{_pi(rest)}</span></div>'
                    )
                else:
                    html.append(
                        f'<div style="margin:10px 0 2px;padding:8px 14px;'
                        f'background:#F0FBF8;border-left:3px solid #27AE60;border-radius:0 4px 4px 0;">'
                        + _num_badge(num, "#27AE60")
                        + f'<span style="font-size:13.5px;vertical-align:middle;">{_pi(text)}</span></div>'
                    )
            elif cur_letter == "D":
                html.append(
                    f'<div style="margin:8px 0;padding:9px 14px;'
                    f'background:#FFF5F5;border-left:3px solid #E74C3C;border-radius:0 4px 4px 0;">'
                    + _num_badge(num, "#E74C3C")
                    + f'<span style="font-size:13.5px;vertical-align:middle;">{_pi(text)}</span></div>'
                )
            elif cur_letter == "F":
                html.append(
                    f'<div style="margin:10px 0 4px;padding:9px 14px;'
                    f'background:#EAF2FB;border-left:3px solid #2471A3;border-radius:0 4px 4px 0;">'
                    + _num_badge(num, "#2471A3", "square")
                    + f'<span style="font-size:13.5px;vertical-align:middle;">{_pi(text)}</span></div>'
                )
            else:
                # A 區（及預設）
                html.append(
                    f'<div style="margin:8px 0;padding:7px 12px;'
                    f'background:#F4F8FD;border-left:3px solid {accent};border-radius:0 4px 4px 0;">'
                    + _num_badge(num, accent)
                    + f'<span style="font-size:13.5px;vertical-align:middle;">{_pi(text)}</span></div>'
                )
            continue

        # ══════════════════════════════════════════
        #  子項目  - / • / *
        # ══════════════════════════════════════════
        sm = re.match(r'^[-•\*＊]\s*(.*)', stripped)
        if sm:
            html.append(
                f'<div style="margin:3px 0 3px 42px;padding:3px 10px;'
                f'border-left:2px solid #C8D6E5;font-size:13px;color:#555;">'
                f'{_pi(sm.group(1))}</div>'
            )
            continue

        # ── 一般內文 ──
        html.append(
            f'<div style="margin:4px 0 4px 6px;color:#444;font-size:13.5px;line-height:1.85;">'
            f'{_pi(stripped)}</div>'
        )

    _close_section()
    return "\n".join(html)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HTML Email 主體建構
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_html(summary: dict) -> str:
    ok = summary.get("success", False)
    sc = "#27AE60" if ok else "#E74C3C"
    st = "✅ 成功" if ok else "⚠️ 失敗/部分失敗"

    p = []

    # ── Header Banner ─────────────────────────────────
    gen_at   = summary.get("generated_at", "")
    days     = summary.get("days", 5)
    tot_rows = summary.get("total_rows", 0)
    brk_ok   = summary.get("brokers_ok", 0)
    brk_fail = summary.get("brokers_fail", 0)

    p.append(
        f'<div style="background:linear-gradient(135deg,#0F2744,#1A3A5C,#1F6FAB);'
        f'padding:22px 26px 18px;border-radius:8px 8px 0 0;">'
        f'<h1 style="margin:0 0 6px;font-size:22px;color:#fff;font-weight:800;'
        f'letter-spacing:.5px;">📈 外資分點狙擊分析</h1>'
        f'<p style="margin:0;font-size:12.5px;color:#B0C8E8;line-height:1.8;">'
        f'產生時間：{_esc(gen_at)}&nbsp;｜&nbsp;近 {days} 日&nbsp;｜&nbsp;'
        f'資料筆數：{tot_rows:,}&nbsp;｜&nbsp;'
        f'<span style="color:{sc};font-weight:700;">{st}</span>&nbsp;｜&nbsp;'
        f'OK <span style="color:#58D68D;">{brk_ok}</span> / '
        f'FAIL <span style="color:#EC7063;">{brk_fail}</span>'
        f'</p></div>'
    )

    # ── GitHub Actions 連結 ───────────────────────────
    srv  = os.getenv("GITHUB_SERVER_URL")
    repo = os.getenv("GITHUB_REPOSITORY")
    rid  = os.getenv("GITHUB_RUN_ID")
    if srv and repo and rid:
        link = f"{srv}/{repo}/actions/runs/{rid}"
        p.append(
            f'<div style="padding:7px 20px;background:#E8ECF0;border-bottom:1px solid #D0D8E4;">'
            f'<a href="{link}" style="font-size:12px;color:#2E86C1;text-decoration:none;">'
            f'🔗 GitHub Actions Workflow 執行記錄</a></div>'
        )

    p.append('<div style="padding:16px 20px 20px;">')

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  一、大盤環境速覽（TAIEX / 三大法人 / 融資融券 / 期貨）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    market = summary.get("market_data") or {}

    taiex   = market.get("taiex") or []
    inst    = market.get("institutional") or []
    margin  = market.get("margin") or []
    futures = market.get("futures") or []
    tdcc    = market.get("tdcc") or []

    has_market = any([taiex, inst, margin, futures])
    if has_market:
        p.append(
            '<div style="margin:8px 0 4px;padding:6px 14px;'
            'background:#1A2A3A;border-radius:4px;">'
            '<span style="font-size:13px;font-weight:700;color:#ECF0F1;'
            'letter-spacing:1px;">一、大盤環境速覽</span></div>'
        )

    if taiex:
        p.append(_render_taiex(taiex))

    if inst:
        p.append(_render_institutional(inst))

    # 融資融券已移除（數據抓取不穩定，移除顯示）

    if futures:
        p.append(_render_futures(futures))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  二、外資分點明細 Top N
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    top_preview = summary.get("top_preview") or []
    if top_preview:
        p.append(
            '<div style="margin:20px 0 4px;padding:6px 14px;'
            'background:#1A2A3A;border-radius:4px;">'
            '<span style="font-size:13px;font-weight:700;color:#ECF0F1;'
            'letter-spacing:1px;">二、外資分點明細（淨超 Top '
            f'{len(top_preview)} 家）</span></div>'
        )
        for rank, block in enumerate(top_preview, 1):
            p.append(_render_broker_block(block, rank))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  三、千張大戶持股（原本未渲染）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if tdcc:
        p.append(
            '<div style="margin:20px 0 4px;padding:6px 14px;'
            'background:#1A2A3A;border-radius:4px;">'
            '<span style="font-size:13px;font-weight:700;color:#ECF0F1;'
            'letter-spacing:1px;">三、千張大戶持股比例</span></div>'
        )
        p.append(_render_tdcc(tdcc))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  四、AI 深度分析
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ai_text     = summary.get("ai_analysis", "")
    ai_model    = summary.get("ai_model", "")
    ai_provider = summary.get("ai_provider", "")
    ai_version  = summary.get("ai_analyzer_version", "")

    p.append(
        '<div style="margin:24px 0 4px;padding:6px 14px;'
        'background:#1A2A3A;border-radius:4px;">'
        '<span style="font-size:13px;font-weight:700;color:#ECF0F1;'
        'letter-spacing:1px;">四、🤖 AI 深度分析</span></div>'
    )
    # 模型資訊 pill
    if ai_provider or ai_model:
        parts_info = []
        if ai_provider:
            parts_info.append(_esc(ai_provider))
        if ai_model:
            parts_info.append(_esc(ai_model))
        if ai_version:
            parts_info.append(_esc(ai_version))
        p.append(
            f'<div style="margin:6px 0 12px;display:flex;flex-wrap:wrap;gap:6px;">'
            + "".join(
                f'<span style="display:inline-block;padding:2px 10px;background:#EBF5FB;'
                f'border:1px solid #AED6F1;border-radius:12px;font-size:11px;color:#2E86C1;">'
                f'{part}</span>'
                for part in parts_info
            )
            + "</div>"
        )

    p.append(_format_ai_html(ai_text))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  錯誤摘要
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    errors = summary.get("errors") or []
    market_errors = market.get("fetch_errors") or []
    all_errors = errors + market_errors

    if all_errors:
        p.append(
            '<div style="margin-top:18px;padding:10px 16px;'
            'background:#FDF2F2;border:1px solid #FADBD8;'
            'border-radius:4px;font-size:12px;color:#922;">'
            '<b>⚠️ 錯誤摘要：</b><br>'
            + "".join(f'&bull; {_esc(e)}<br>' for e in all_errors[:15])
            + '</div>'
        )

    p.append('</div>')  # close padding div

    # ── 免責聲明 ──────────────────────────────────────
    p.append(
        '<div style="padding:14px 22px;background:#FFF9E6;'
        'border-top:2px solid #F0E0A0;font-size:11.5px;color:#8B7500;line-height:1.7;">'
        '<b>[免責聲明]</b> '
        '帶進帶出是違法的行為，此資料皆為網上根據盤後資訊大數據所取得訊息，'
        '非作為或被視為買進或售出標的的邀請或意象，'
        '請自行依據取得資訊評估風險與獲利，<b>有賺有賠請斟酌</b>。'
        '</div>'
    )

    # ── Footer ────────────────────────────────────────
    ymd_now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    p.append(
        f'<div style="padding:10px 22px;background:#1A2A3A;'
        f'border-radius:0 0 8px 8px;font-size:11px;color:#9DB5CC;text-align:center;">'
        f'此信由 GitHub Actions 自動寄出 ｜ {ymd_now} (TW) ｜ 外資分點狙擊分析系統</div>'
    )

    body = "\n".join(p)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>'
        'table{border-spacing:0!important;}'
        'td,th{border-spacing:0!important;}'
        'a{color:#2E86C1;}'
        '@media(max-width:600px){'
        '.wrap{padding:10px!important;}'
        'table{font-size:11px!important;}'
        '}'
        '</style></head>'
        '<body style="margin:0;padding:16px;background:#DDE4EC;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;\">"
        '<div class="wrap" style="max-width:700px;margin:0 auto;background:#FFF;'
        'border-radius:8px;overflow:hidden;box-shadow:0 3px 12px rgba(0,0,0,0.15);">'
        f'{body}</div></body></html>'
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Plain Text 備用版
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_plain(summary: dict) -> str:
    ok = summary.get("success", False)
    market = summary.get("market_data") or {}

    lines = [
        "【外資分點狙擊分析】",
        f"狀態：{'成功' if ok else '失敗/部分失敗'}",
        f"產生時間：{summary.get('generated_at','')}",
        f"資料筆數：{summary.get('total_rows',0)}",
        "",
    ]

    # 大盤概況
    inst = market.get("institutional") or []
    if inst:
        today = inst[0]
        lines.append("【三大法人今日買賣超】")
        lines.append(f"  外資：{_fb(today['foreign']['net'])}")
        lines.append(f"  投信：{_fb(today['trust']['net'])}")
        lines.append(f"  自營：{_fb(today['dealer']['net'])}")
        lines.append(f"  合計：{_fb(today.get('total_net',0))}")
        lines.append("")

    taiex = market.get("taiex") or []
    if taiex:
        t = taiex[0]
        chg = t.get("change", 0)
        sign = "+" if chg > 0 else ""
        lines.append(f"【大盤指數】 收盤={t.get('close',0)}  漲跌={sign}{chg}  成交={t.get('amount_billion',0):.0f}億")
        lines.append("")

    # AI 分析
    ai_text = summary.get("ai_analysis", "")
    if ai_text:
        lines.append("【AI 深度分析】")
        lines.append(ai_text[:4000])
        lines.append("")

    lines.append(
        "[免責聲明] 帶進帶出是違法的行為，此資料皆為網上根據盤後資訊大數據所取得訊息，"
        "非作為或被視為買進或售出標的的邀請或意象，請自行依據取得資訊評估風險與獲利，有賺有賠請斟酌。"
    )
    lines.append("")
    lines.append("（此信由 GitHub Actions 自動寄出）")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SMTP 發送
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _safe_int_env(name, default):
    v = (os.environ.get(name, "") or "").strip()
    try:
        return int(v) if v else default
    except Exception:
        return default


def main():
    smtp_host = (os.environ.get("SMTP_HOST", "smtp.gmail.com") or "").strip() or "smtp.gmail.com"
    smtp_port = _safe_int_env("SMTP_PORT", 587)
    smtp_user = (os.environ.get("SMTP_USER") or "").strip()
    smtp_pass = (os.environ.get("SMTP_PASS") or "").strip()
    mail_from = (os.environ.get("MAIL_FROM") or smtp_user).strip()
    mail_to   = (os.environ.get("MAIL_TO") or "").strip()
    mail_bcc  = (os.environ.get("MAIL_BCC") or "").strip()

    if not smtp_user or not smtp_pass:
        raise RuntimeError("SMTP_USER/SMTP_PASS 未設定")
    if not mail_to:
        raise RuntimeError("MAIL_TO 未設定")

    summary = load_summary()
    ymd     = datetime.now(TZ).strftime("%Y-%m-%d")
    subject = os.environ.get("MAIL_SUBJECT", f"【外資分點狙擊分析】{ymd}（TW 18:00）")

    xlsx = pick_latest(os.path.join("output", "IKE_Report_*.xlsx"))
    pdf  = pick_latest(os.path.join("output", "IKE_Report_*.pdf"))

    msg             = EmailMessage()
    msg["From"]     = mail_from
    msg["To"]       = mail_to
    if mail_bcc:
        msg["Bcc"]  = mail_bcc
    msg["Subject"]  = subject

    msg.set_content(build_plain(summary))
    msg.add_alternative(build_html(summary), subtype="html")

    for fpath, mime in [
        (xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (pdf,  "application/pdf"),
    ]:
        if fpath and os.path.exists(fpath):
            with open(fpath, "rb") as f:
                data = f.read()
            mt, st = mime.split("/", 1)
            msg.add_attachment(data, maintype=mt, subtype=st, filename=os.path.basename(fpath))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print("[OK] HTML Email sent.")


if __name__ == "__main__":
    main()
