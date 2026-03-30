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
#  共用 HTML 元件
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 區塊標題樣式
SECTION_COLORS = {
    "blue":   {"bg": "#1A5276", "light": "#EBF5FB"},
    "navy":   {"bg": "#1A2A3A", "light": "#E8ECF0"},
    "gold":   {"bg": "#9A7D0A", "light": "#FEF9E7"},
    "green":  {"bg": "#1E8449", "light": "#EAFAF1"},
    "red":    {"bg": "#922B21", "light": "#FDEDEC"},
    "purple": {"bg": "#6C3483", "light": "#F4ECF7"},
    "teal":   {"bg": "#148F77", "light": "#E8F8F5"},
    "gray":   {"bg": "#515A5A", "light": "#F2F3F4"},
}


def _sec_hdr(icon: str, title: str, color_key: str = "blue") -> str:
    """全寬區塊標題 bar"""
    c = SECTION_COLORS.get(color_key, SECTION_COLORS["blue"])
    return (
        f'<div style="margin:20px 0 10px;padding:10px 16px;'
        f'background:{c["bg"]};border-radius:5px 5px 0 0;">'
        f'<span style="font-size:14px;font-weight:700;color:#fff;letter-spacing:.5px;">'
        f'{icon}&nbsp; {_esc(title)}</span></div>'
    )


def _table_wrap(inner: str, border_color: str = "#DEE2E6") -> str:
    return (
        f'<table style="width:100%;border-collapse:collapse;font-size:12.5px;'
        f'border:1px solid {border_color};border-radius:0 0 5px 5px;'
        f'overflow:hidden;margin-bottom:4px;">{inner}</table>'
    )


def _th(*cols, bg: str = "#2C3E50", color: str = "#ECF0F1") -> str:
    cells = "".join(
        f'<th style="padding:6px 10px;text-align:{align};background:{bg};'
        f'color:{color};font-size:12px;font-weight:600;white-space:nowrap;">{label}</th>'
        for label, align in cols
    )
    return f"<tr>{cells}</tr>"


def _td_row(*cells, bg: str = "#FFF") -> str:
    parts = []
    for item in cells:
        if isinstance(item, tuple):
            text, extra_style = item
        else:
            text, extra_style = item, ""
        parts.append(
            f'<td style="padding:5px 10px;border-top:1px solid #EAECEE;'
            f'{extra_style}">{text}</td>'
        )
    return f'<tr style="background:{bg};">{"".join(parts)}</tr>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  大盤資料 HTML 渲染（修復缺失的三個區塊）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_taiex(taiex: list) -> str:
    """大盤指數 + 成交量表格（原本未在 Email 中渲染，現在補上）"""
    if not taiex:
        return ""
    rows_html = _th(
        ("日期", "left"), ("收盤", "right"), ("漲跌", "right"), ("成交金額", "right"),
    )
    for i, d in enumerate(taiex[:6]):
        chg = d.get("change", 0)
        sign = "+" if chg > 0 else ""
        chg_str = f"{sign}{chg:,.2f}" if isinstance(chg, float) else f"{sign}{chg}"
        chg_c = _color(chg)
        close = d.get("close", 0)
        amt = d.get("amount_billion", 0)
        bg = "#F8F9FA" if i % 2 else "#FFF"
        rows_html += _td_row(
            _esc(d.get("date", "")),
            (f'<span style="font-weight:600;">{close:,.2f}</span>' if isinstance(close, float) else _esc(str(close)), "text-align:right;"),
            (f'<span style="color:{chg_c};font-weight:600;">{_esc(chg_str)}</span>', "text-align:right;"),
            (f'{int(amt):,} 億', "text-align:right;color:#555;"),
            bg=bg,
        )
    return _sec_hdr("📊", "大盤指數 ＋ 成交量（近日）", "navy") + _table_wrap(rows_html)


def _render_institutional(inst: list) -> str:
    """三大法人買賣超表格"""
    if not inst:
        return ""
    rows_html = _th(
        ("日期", "left"),
        ("外資", "right"), ("投信", "right"), ("自營商", "right"), ("合計", "right"),
    )
    for i, d in enumerate(inst[:6]):
        fg  = d["foreign"]["net"]
        tr  = d["trust"]["net"]
        dl  = d["dealer"]["net"]
        tot = d.get("total_net", fg + tr + dl)
        bg  = "#F8F9FA" if i % 2 else "#FFF"

        def _cell(v):
            s   = _fb(v)
            col = _color(v)
            bold = "font-weight:600;" if i == 0 else ""
            return (f'<span style="color:{col};{bold}">{_esc(s)}</span>', "text-align:right;")

        rows_html += _td_row(_esc(d["date"]), _cell(fg), _cell(tr), _cell(dl),
                             (f'<span style="color:{_color(tot)};font-weight:700;">{_esc(_fb(tot))}</span>',
                              "text-align:right;font-weight:700;"),
                             bg=bg)

    return _sec_hdr("🏦", "三大法人買賣超（近日，元）", "blue") + _table_wrap(rows_html)


def _render_margin(margin: list) -> str:
    """融資融券表格（原本未在 Email 中渲染，現在補上）"""
    if not margin:
        return ""
    rows_html = _th(
        ("日期", "left"),
        ("融資增減", "right"), ("融資餘額", "right"),
        ("融券增減", "right"), ("融券餘額", "right"),
    )
    for i, d in enumerate(margin[:6]):
        mc  = d.get("margin_change", 0)
        mb  = d.get("margin_balance", 0)
        sc  = d.get("short_change", 0)
        sb  = d.get("short_balance", 0)
        bg  = "#F8F9FA" if i % 2 else "#FFF"

        # 融資增加（散戶追多） = 偏多情緒 = 紅色；減少 = 綠色
        mc_c = "#C0392B" if mc > 0 else "#27AE60" if mc < 0 else "#555"
        # 融券增加（放空增加）= 偏空 = 綠色；減少 = 紅色（軋空）
        sc_c = "#27AE60" if sc > 0 else "#C0392B" if sc < 0 else "#555"

        rows_html += _td_row(
            _esc(d.get("date", "")),
            (f'<span style="color:{mc_c};font-weight:600;">{_fbi(mc)}</span>', "text-align:right;"),
            (_fi(mb), "text-align:right;color:#555;"),
            (f'<span style="color:{sc_c};font-weight:600;">{_fbi(sc)}</span>', "text-align:right;"),
            (_fi(sb), "text-align:right;color:#555;"),
            bg=bg,
        )
    return _sec_hdr("💳", "融資融券（近日，張）", "gray") + _table_wrap(rows_html)


def _render_futures(futures: list) -> str:
    """期貨三大法人台指期淨部位（修正：補上 header row）"""
    if not futures:
        return ""
    rows_html = _th(
        ("日期", "left"),
        ("外資淨部位(口)", "right"),
        ("投信淨部位(口)", "right"),
        ("自營淨部位(口)", "right"),
    )
    for i, d in enumerate(futures[:6]):
        fg = d.get("foreign_net_oi", 0)
        tr = d.get("trust_net_oi", 0)
        dl = d.get("dealer_net_oi", 0)
        bg = "#F8F9FA" if i % 2 else "#FFF"

        def _fcell(v):
            col   = _color(v)
            bold  = "font-weight:700;" if i == 0 else ""
            return (f'<span style="color:{col};{bold}">{_fbi(v)}</span>', "text-align:right;")

        rows_html += _td_row(_esc(d.get("date", "")), _fcell(fg), _fcell(tr), _fcell(dl), bg=bg)

    return _sec_hdr("📉", "期貨三大法人台指期淨部位（近日，口）", "purple") + _table_wrap(rows_html)


def _render_tdcc(tdcc: list) -> str:
    """千張大戶持股比例卡片（原本未在 Email 中渲染，現在補上）"""
    if not tdcc:
        return ""
    inner = _th(
        ("股票代號", "left"),
        ("千張以上人數", "right"),
        ("持股比例", "right"),
        ("400~999張持股", "right"),
    )
    for i, d in enumerate(tdcc):
        bg  = "#F8F9FA" if i % 2 else "#FFF"
        pct = d.get("pct_1000_plus", 0)
        cnt = d.get("holders_1000_plus", 0)
        pct400 = d.get("pct_400_999", 0)
        # 大戶持股>60% 視為籌碼集中，偏正面 → 紅色
        pct_c = "#C0392B" if pct >= 60 else "#27AE60" if pct < 40 else "#555"
        inner += _td_row(
            f'<span style="font-weight:700;">{_esc(d.get("stock_id",""))}</span>',
            (_fi(cnt), "text-align:right;"),
            (f'<span style="color:{pct_c};font-weight:700;">{pct:.1f}%</span>', "text-align:right;"),
            (f'{pct400:.1f}%' if pct400 else "—", "text-align:right;color:#888;"),
            bg=bg,
        )
    return _sec_hdr("🏆", "千張大戶持股比例（觀察清單個股）", "teal") + _table_wrap(inner)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  外資分點 Top 3 渲染（升級版）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _render_broker_block(block: dict, rank: int) -> str:
    broker    = _esc(block.get("broker", ""))
    total_net = block.get("total_net", 0)
    rows      = block.get("rows") or []
    nc        = _color(total_net, "#555")

    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "▪")

    header = (
        f'<div style="margin:10px 0 0;padding:8px 14px;'
        f'background:#F5F8FA;border-left:4px solid #2C3E50;border-radius:0 4px 4px 0;">'
        f'<span style="font-size:14px;font-weight:700;color:#2C3E50;">{medal} {broker}</span>'
        f'<span style="float:right;font-size:13px;color:{nc};font-weight:700;">'
        f'總淨超 {_fi(total_net)} 張</span><div style="clear:both;"></div></div>'
    )

    if not rows:
        return header

    rows_html = _th(
        ("#", "center"), ("代號 股名", "left"), ("淨超(張)", "right"),
        ("均價", "right"), ("現價", "right"), ("乖離", "right"),
        bg="#34495E",
    )
    for i, r in enumerate(rows[:5], 1):
        nv  = r.get("net", 0)
        rc  = _color(nv)
        bias_val = r.get("bias", "")
        bias_c   = _color(str(bias_val).replace("%",""))
        bg  = "#F8F9FA" if i % 2 else "#FFF"
        rows_html += _td_row(
            (f'<span style="color:#888;">{i}</span>', "text-align:center;width:28px;"),
            f'<span style="font-weight:600;">{_esc(r.get("sid",""))} {_esc(r.get("name",""))}</span>',
            (f'<span style="color:{rc};font-weight:700;">{_fi(nv)}</span>', "text-align:right;"),
            (f'{_esc(str(r.get("avg","")))}', "text-align:right;color:#555;"),
            (f'{_esc(str(r.get("price","")))}', "text-align:right;color:#333;font-weight:600;"),
            (f'<span style="color:{bias_c};">{_esc(str(bias_val))}</span>', "text-align:right;"),
            bg=bg,
        )

    return header + _table_wrap(rows_html, border_color="#DEE2E6")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AI 分析 HTML 格式化（v11：支援 **bold**、E) 獨立卡片）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTION_STYLES = {
    "A": {"bg": "#EBF5FB", "border": "#2E86C1", "icon": "📊", "label": "大盤籌碼環境研判"},
    "B": {"bg": "#FEF9E7", "border": "#D4AC0D", "icon": "🏦", "label": "外資力量深度剖析"},
    "C": {"bg": "#EAFAF1", "border": "#27AE60", "icon": "🎯", "label": "明日觀察清單"},
    "D": {"bg": "#FDEDEC", "border": "#E74C3C", "icon": "⚠️", "label": "風控與資金配置"},
    "E": {"bg": "#F4ECF7", "border": "#8E44AD", "icon": "💡", "label": "一句話摘要"},
    "F": {"bg": "#EBF5FB", "border": "#1A5276", "icon": "🔍", "label": "外資交叉比對亮點"},
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

    # 用於提取 E) 一句話摘要
    e_summary  = ""

    def _close_section():
        if in_section:
            html.append('</div></div>')  # close content div + section wrapper

    def _process_inline(raw: str) -> str:
        """_esc → **bold** → keyword highlight"""
        return _style_keywords(_md_inline(_esc(raw)))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # ── Section header: A) ... 到 F) ... ──
        hm = re.match(r'^([A-F])\s*[)）:：]\s*(.*)', stripped)
        if hm:
            _close_section()
            letter = hm.group(1)
            title_from_ai = hm.group(2).strip().rstrip("：:").strip()
            st = SECTION_STYLES.get(letter, {"bg": "#F5F5F5", "border": "#999", "icon": "▪", "label": ""})
            default_label = st["label"]
            # 優先使用 AI 輸出的標題（通常更完整）
            display_title = title_from_ai if title_from_ai else default_label

            # E) 一句話摘要：特殊卡片
            if letter == "E":
                html.append(
                    f'<div style="margin:20px 0 8px;padding:14px 20px;'
                    f'background:linear-gradient(135deg,#F4ECF7,#EBD5F8);'
                    f'border-left:5px solid #8E44AD;border-radius:4px;">'
                    f'<div style="font-size:12px;font-weight:700;color:#6C3483;'
                    f'margin-bottom:6px;">💡 一句話摘要</div>'
                )
                if display_title:
                    # 如果標題本身就是摘要內容
                    e_summary = display_title
                    html.append(
                        f'<div style="font-size:15px;font-weight:700;color:#4A235A;'
                        f'line-height:1.6;">{_process_inline(e_summary)}</div>'
                    )
                html.append('</div>')
                in_section = True
                cur_letter = "E"
                continue

            html.append(
                f'<div style="margin:18px 0 0;">'
                # Section header bar
                f'<div style="background:{st["bg"]};border-left:5px solid {st["border"]};'
                f'padding:10px 16px;border-radius:0 5px 0 0;">'
                f'<span style="font-size:14.5px;font-weight:700;color:{st["border"]};">'
                f'{st["icon"]} {letter}）{_esc(display_title)}</span></div>'
                # Content area
                f'<div style="padding:10px 16px 14px 20px;font-size:13.5px;'
                f'line-height:1.9;color:#333;background:{st["bg"]}22;">'
            )
            in_section = True
            cur_letter = letter
            continue

        # ── E) 後面的純文字行（當摘要本身在下一行時）──
        if cur_letter == "E" and not e_summary:
            e_summary = stripped
            # 找到 E) 的 div 並追加文字
            html.append(
                f'<div style="margin-top:4px;font-size:15px;font-weight:700;color:#4A235A;">'
                f'{_process_inline(stripped)}</div>'
            )
            continue

        if not in_section or cur_letter == "E":
            # 前言文字（A) 之前）
            html.append(
                f'<p style="font-size:13px;color:#666;margin:4px 0;">'
                f'{_process_inline(stripped)}</p>'
            )
            continue

        # ── 編號項目：1) 2) 3) ──
        nm = re.match(r'^(\d+)\s*[)）.]\s*(.*)', stripped)
        if nm:
            num, text = nm.group(1), nm.group(2)

            # B 區：外資券商名稱 → 金色卡片
            if cur_letter == "B":
                html.append(
                    f'<div style="margin:14px 0 4px;padding:10px 14px;'
                    f'background:#FEF9E7;border:1px solid #F0D060;border-radius:5px;">'
                    f'<span style="display:inline-block;min-width:22px;height:22px;'
                    f'background:#D4AC0D;color:#fff;border-radius:4px;text-align:center;'
                    f'line-height:22px;font-size:12px;font-weight:700;margin-right:10px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="font-weight:700;font-size:14px;color:#7D6608;vertical-align:middle;">'
                    f'{_process_inline(text)}</span></div>'
                )
                continue

            # C 區：偵測「NNNN 股名」股票標的 → 綠色卡片
            stock_m = re.match(r'^(\d{4,5})\s+(.+)', text)
            if cur_letter == "C" and stock_m:
                sid, rest = stock_m.groups()
                html.append(
                    f'<div style="margin:12px 0 4px;padding:9px 14px;'
                    f'background:#E8F8F5;border-left:4px solid #1ABC9C;border-radius:0 4px 4px 0;">'
                    f'<span style="display:inline-block;min-width:20px;height:20px;'
                    f'background:#1ABC9C;color:#fff;border-radius:50%;text-align:center;'
                    f'line-height:20px;font-size:11px;font-weight:700;margin-right:8px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="font-weight:700;font-size:14px;color:#1A6B5A;'
                    f'vertical-align:middle;">{_esc(sid)}'
                    f'</span><span style="font-weight:600;font-size:14px;vertical-align:middle;">'
                    f' {_process_inline(rest)}</span></div>'
                )
                continue

            # F 區：交叉比對亮點 → 藍色框
            if cur_letter == "F":
                html.append(
                    f'<div style="margin:10px 0 4px;padding:9px 14px;'
                    f'background:#EBF5FB;border-left:3px solid #2E86C1;border-radius:0 4px 4px 0;">'
                    f'<span style="display:inline-block;min-width:20px;height:20px;'
                    f'background:#2E86C1;color:#fff;border-radius:4px;text-align:center;'
                    f'line-height:20px;font-size:11px;font-weight:700;margin-right:8px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="font-size:13.5px;vertical-align:middle;">'
                    f'{_process_inline(text)}</span></div>'
                )
                continue

            # D 區：風控 → 橙紅色數字標記
            if cur_letter == "D":
                html.append(
                    f'<div style="margin:8px 0;padding:8px 14px;'
                    f'background:#FFF5F5;border-left:3px solid #E74C3C;border-radius:0 4px 4px 0;">'
                    f'<span style="display:inline-block;min-width:20px;height:20px;'
                    f'background:#E74C3C;color:#fff;border-radius:50%;text-align:center;'
                    f'line-height:20px;font-size:11px;font-weight:700;margin-right:8px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="font-size:13.5px;vertical-align:middle;">'
                    f'{_process_inline(text)}</span></div>'
                )
                continue

            # A 區（及預設）：藍色圓形數字
            html.append(
                f'<div style="margin:7px 0;padding-left:2px;">'
                f'<span style="display:inline-block;min-width:20px;height:20px;'
                f'background:#2E86C1;color:#fff;border-radius:50%;text-align:center;'
                f'line-height:20px;font-size:11px;font-weight:700;margin-right:8px;'
                f'vertical-align:middle;">{num}</span>'
                f'<span style="font-size:13.5px;vertical-align:middle;">'
                f'{_process_inline(text)}</span></div>'
            )
            continue

        # ── 子項目：- / • / * ──
        sm = re.match(r'^[-•\*＊]\s*(.*)', stripped)
        if sm:
            html.append(
                f'<div style="margin:3px 0 3px 36px;padding:3px 10px;'
                f'border-left:2px solid #C8D6E5;font-size:13px;color:#444;">'
                f'{_process_inline(sm.group(1))}</div>'
            )
            continue

        # ── 一般內文段落 ──
        html.append(
            f'<div style="margin:4px 0 4px 4px;color:#444;font-size:13.5px;line-height:1.8;">'
            f'{_process_inline(stripped)}</div>'
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

    if margin:
        p.append(_render_margin(margin))

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
