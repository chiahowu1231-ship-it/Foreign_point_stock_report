# src/mailer.py
# v10 — HTML 格式 Email（標題/內文有清楚的視覺層級）
import os
import json
import glob
import re
import smtplib
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")


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
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _fb(n):
    try:
        v = int(n)
        if abs(v) >= 1e8:
            return f"{v/1e8:+.1f}億"
        elif abs(v) >= 1e4:
            return f"{v/1e4:+.0f}萬"
        return f"{v:+,}"
    except Exception:
        return str(n)


def _color_val(val_str):
    try:
        v = float(str(val_str).replace(",", "").replace("%", "").replace("億", "").replace("萬", "").replace("+", ""))
        if "+" in str(val_str) or v > 0:
            return "#C0392B"
        elif v < 0:
            return "#27AE60"
    except Exception:
        pass
    return "#555"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  AI 分析文字 → HTML（核心格式化）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SECTION_STYLES = {
    "A": {"bg": "#EBF5FB", "border": "#2E86C1", "icon": "&#x1F4CA;"},
    "B": {"bg": "#FEF9E7", "border": "#D4AC0D", "icon": "&#x1F3E6;"},
    "C": {"bg": "#EAFAF1", "border": "#27AE60", "icon": "&#x1F3AF;"},
    "D": {"bg": "#FDEDEC", "border": "#E74C3C", "icon": "&#x26A0;&#xFE0F;"},
    "E": {"bg": "#F4ECF7", "border": "#8E44AD", "icon": "&#x1F4A1;"},
    "F": {"bg": "#EBF5FB", "border": "#1A5276", "icon": "&#x1F50D;"},
}


def _format_ai_html(ai_text: str) -> str:
    if not ai_text or not ai_text.strip():
        return '<p style="color:#888;">本次尚未取得 AI 分析。</p>'

    if "失敗" in ai_text[:80] or "error" in ai_text[:80].lower():
        return (
            f'<div style="background:#FDF2F2;border-left:4px solid #E74C3C;'
            f'padding:12px 16px;border-radius:4px;color:#922;font-size:13px;">'
            f'{_esc(ai_text[:600])}</div>'
        )

    lines = ai_text.strip().split("\n")
    html = []
    in_section = False
    current_section = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Section header: A) ... / B) ...
        hm = re.match(r'^([A-F])\s*[)）]\s*(.*)', stripped)
        if hm:
            if in_section:
                html.append('</div></div>')
            letter = hm.group(1)
            title = hm.group(2).strip().rstrip("：:")
            st = SECTION_STYLES.get(letter, {"bg": "#F5F5F5", "border": "#999", "icon": ""})
            html.append(
                f'<div style="margin:16px 0 0;">'
                f'<div style="background:{st["bg"]};border-left:4px solid {st["border"]};'
                f'padding:10px 16px;border-radius:0 6px 6px 0;">'
                f'<span style="font-size:15px;font-weight:700;color:{st["border"]};">'
                f'{st["icon"]} {letter}) {_esc(title)}</span></div>'
                f'<div style="padding:8px 16px 12px 20px;font-size:14px;line-height:1.9;color:#333;">'
            )
            in_section = True
            current_section = letter
            continue

        if not in_section:
            html.append(f'<p style="font-size:13px;color:#666;margin:4px 0;">{_esc(stripped)}</p>')
            continue

        # Numbered item: 1) 2) 3) — 依區段用不同樣式
        nm = re.match(r'^(\d+)\s*[)）]\s*(.*)', stripped)
        if nm:
            num, text = nm.group(1), nm.group(2)

            # ── B 區：外資券商名稱 → 金色卡片（與 C 區的綠色股票卡區分） ──
            if current_section == "B":
                html.append(
                    f'<div style="margin:12px 0 4px;padding:10px 14px;'
                    f'background:#FEF9E7;border-left:4px solid #D4AC0D;border-radius:4px;">'
                    f'<span style="display:inline-block;min-width:22px;height:22px;'
                    f'background:#D4AC0D;color:#fff;border-radius:4px;text-align:center;'
                    f'line-height:22px;font-size:12px;font-weight:700;margin-right:10px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="font-weight:700;font-size:14px;color:#7D6608;vertical-align:middle;">'
                    f'{_style_keywords(_esc(text))}</span></div>'
                )
                continue

            # ── C 區：偵測股票代碼 → 綠色卡片 ──
            stock_m = re.match(r'^(\d{4})\s+(.+)', text)
            if stock_m:
                sid, rest = stock_m.groups()
                html.append(
                    f'<div style="margin:10px 0 4px;padding:8px 12px;'
                    f'background:#E8F8F5;border-left:3px solid #1ABC9C;border-radius:4px;">'
                    f'<span style="display:inline-block;min-width:20px;height:20px;'
                    f'background:#1ABC9C;color:#fff;border-radius:50%;text-align:center;'
                    f'line-height:20px;font-size:11px;font-weight:700;margin-right:8px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="font-weight:700;font-size:14px;vertical-align:middle;">'
                    f'{sid} {_esc(rest)}</span></div>'
                )
            else:
                # ── A/D/E 區：一般藍色圓圈 ──
                html.append(
                    f'<div style="margin:6px 0;padding-left:4px;">'
                    f'<span style="display:inline-block;min-width:20px;height:20px;'
                    f'background:#2E86C1;color:#fff;border-radius:50%;text-align:center;'
                    f'line-height:20px;font-size:11px;font-weight:700;margin-right:8px;'
                    f'vertical-align:middle;">{num}</span>'
                    f'<span style="vertical-align:middle;">{_style_keywords(_esc(text))}</span></div>'
                )
            continue

        # Sub-item: - / • / *
        sm = re.match(r'^[-•\*]\s*(.*)', stripped)
        if sm:
            html.append(
                f'<div style="margin:3px 0 3px 36px;padding-left:10px;'
                f'border-left:2px solid #DDD;font-size:13px;color:#444;">'
                f'{_style_keywords(_esc(sm.group(1)))}</div>'
            )
            continue

        # 一般文字
        html.append(
            f'<div style="margin:3px 0 3px 32px;color:#444;font-size:13px;">'
            f'{_style_keywords(_esc(stripped))}</div>'
        )

    if in_section:
        html.append('</div></div>')

    return "\n".join(html)


def _style_keywords(text: str) -> str:
    kws = {
        "進場": "#2E86C1", "停損": "#E74C3C", "了結": "#8E44AD",
        "突破": "#2980B9", "回測": "#E67E22", "不跌破": "#27AE60",
        "放量": "#C0392B", "縮量": "#27AE60", "偏多": "#C0392B",
        "偏空": "#27AE60", "觀察": "#7F8C8D", "風險": "#E74C3C",
    }
    for kw, c in kws.items():
        text = text.replace(kw, f'<b style="color:{c};">{kw}</b>')
    return text


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  HTML Email 全文
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _sec(title, color):
    return (
        f'<div style="margin:18px 0 8px;padding:8px 14px;'
        f'background:{color};border-radius:4px;">'
        f'<span style="font-size:14px;font-weight:700;color:#fff;">{title}</span></div>'
    )


def build_html(summary: dict) -> str:
    ok = summary.get("success", False)
    sc = "#27AE60" if ok else "#E74C3C"
    st = "&#x2705; 成功" if ok else "&#x26A0;&#xFE0F; 失敗/部分失敗"

    p = []

    # Header
    p.append(
        f'<div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:20px 24px;border-radius:8px 8px 0 0;">'
        f'<h1 style="margin:0;font-size:20px;color:#fff;font-weight:700;">&#x1F4C8; 外資分點狙擊分析</h1>'
        f'<p style="margin:6px 0 0;font-size:13px;color:#aaa;">'
        f'{summary.get("generated_at","")} &#xFF5C; 近{summary.get("days",5)}日 &#xFF5C; '
        f'<span style="color:{sc};font-weight:600;">{st}</span> &#xFF5C; '
        f'{summary.get("total_rows",0)} 筆 &#xFF5C; '
        f'OK {summary.get("brokers_ok",0)} / FAIL {summary.get("brokers_fail",0)}</p></div>'
    )

    srv = os.getenv("GITHUB_SERVER_URL")
    repo = os.getenv("GITHUB_REPOSITORY")
    rid = os.getenv("GITHUB_RUN_ID")
    if srv and repo and rid:
        link = f"{srv}/{repo}/actions/runs/{rid}"
        p.append(f'<div style="padding:6px 24px;background:#F0F0F0;font-size:12px;">'
                 f'<a href="{link}" style="color:#2E86C1;">Workflow 連結</a></div>')

    p.append('<div style="padding:16px 24px;">')

    # 外資 Top 3
    top_preview = summary.get("top_preview") or []
    if top_preview:
        p.append(_sec("&#x1F3DB; 每家外資買超 Top 3", "#2C3E50"))
        for block in top_preview:
            broker = block.get("broker", "")
            total_net = block.get("total_net", 0)
            rows = block.get("rows") or []
            nc = "#C0392B" if total_net > 0 else "#27AE60"
            p.append(
                f'<div style="margin:8px 0 2px;font-weight:700;font-size:14px;">'
                f'&#x25A0; {_esc(broker)} '
                f'<span style="color:{nc};font-size:13px;">總淨超 {_fi(total_net)} 張</span></div>'
            )
            if rows:
                p.append('<table style="width:100%;border-collapse:collapse;font-size:12px;margin:0 0 6px 8px;">')
                for i, r in enumerate(rows[:3], 1):
                    bg = "#F9F9F9" if i % 2 == 0 else "#FFF"
                    nv = r.get("net", 0)
                    rc = "#C0392B" if nv > 0 else "#27AE60"
                    p.append(
                        f'<tr style="background:{bg};">'
                        f'<td style="padding:3px 6px;width:24px;color:#888;">{i})</td>'
                        f'<td style="padding:3px 6px;">{r.get("sid","")} {_esc(r.get("name",""))}</td>'
                        f'<td style="padding:3px 6px;color:{rc};font-weight:600;text-align:right;">淨超 {_fi(nv)}</td>'
                        f'<td style="padding:3px 6px;text-align:right;">均價 {r.get("avg","")}</td>'
                        f'<td style="padding:3px 6px;text-align:right;">現價 {r.get("price","")}</td>'
                        f'<td style="padding:3px 6px;text-align:right;">{r.get("bias","")}</td></tr>'
                    )
                p.append('</table>')

    # 大盤籌碼
    market = summary.get("market_data") or {}
    inst = market.get("institutional") or []
    if inst:
        p.append(_sec("&#x1F4CA; 三大法人買賣超", "#2E86C1"))
        p.append('<table style="width:100%;border-collapse:collapse;font-size:12px;">'
                 '<tr style="background:#EBF5FB;font-weight:600;">'
                 '<td style="padding:4px 8px;">日期</td>'
                 '<td style="padding:4px 8px;text-align:right;">外資</td>'
                 '<td style="padding:4px 8px;text-align:right;">投信</td>'
                 '<td style="padding:4px 8px;text-align:right;">自營</td>'
                 '<td style="padding:4px 8px;text-align:right;">合計</td></tr>')
        for d in inst[:6]:
            fg, tr, dl = d["foreign"]["net"], d["trust"]["net"], d["dealer"]["net"]
            total = d.get("total_net", fg + tr + dl)
            p.append(
                f'<tr>'
                f'<td style="padding:3px 8px;">{d["date"]}</td>'
                f'<td style="padding:3px 8px;text-align:right;color:{_color_val(_fb(fg))};">{_fb(fg)}</td>'
                f'<td style="padding:3px 8px;text-align:right;color:{_color_val(_fb(tr))};">{_fb(tr)}</td>'
                f'<td style="padding:3px 8px;text-align:right;color:{_color_val(_fb(dl))};">{_fb(dl)}</td>'
                f'<td style="padding:3px 8px;text-align:right;font-weight:600;color:{_color_val(_fb(total))};">{_fb(total)}</td></tr>'
            )
        p.append('</table>')

    futures = market.get("futures") or []
    if futures:
        p.append(_sec("&#x1F4C9; 期貨三大法人台指期淨部位（口）", "#884EA0"))
        p.append('<table style="width:100%;border-collapse:collapse;font-size:12px;">')
        for d in futures[:3]:
            fg = d.get("foreign_net_oi", 0)
            tr = d.get("trust_net_oi", 0)
            dl = d.get("dealer_net_oi", 0)
            p.append(
                f'<tr><td style="padding:3px 8px;">{d["date"]}</td>'
                f'<td style="padding:3px 8px;text-align:right;">外資 {_fi(fg)}</td>'
                f'<td style="padding:3px 8px;text-align:right;">投信 {_fi(tr)}</td>'
                f'<td style="padding:3px 8px;text-align:right;">自營 {_fi(dl)}</td></tr>'
            )
        p.append('</table>')

    # AI 分析
    ai_text = summary.get("ai_analysis", "")
    ai_model = summary.get("ai_model", "")
    ai_provider = summary.get("ai_provider", "")

    p.append('<div style="margin-top:24px;border-top:3px solid #2C3E50;padding-top:16px;">')
    p.append('<h2 style="margin:0 0 4px;font-size:18px;color:#2C3E50;">&#x1F916; AI 籌碼分析</h2>')
    if ai_provider or ai_model:
        p.append(f'<p style="margin:0 0 12px;font-size:11px;color:#999;">模型：{_esc(ai_provider)} {_esc(ai_model)}</p>')
    p.append(_format_ai_html(ai_text))
    p.append('</div>')

    # 錯誤
    errors = summary.get("errors") or []
    if errors:
        p.append('<div style="margin-top:16px;padding:10px 16px;background:#FDF2F2;border-radius:4px;font-size:12px;color:#922;">')
        p.append('<b>錯誤摘要：</b><br>')
        for e in errors[:10]:
            p.append(f'&#x2022; {_esc(e)}<br>')
        p.append('</div>')

    p.append('</div>')

    # Disclaimer + Footer
    p.append(
        '<div style="padding:14px 24px;background:#FFF9E6;border-top:1px solid #F0E0A0;'
        'font-size:11px;color:#8B7500;line-height:1.6;">'
        '<b>[免責聲明]</b> '
        '帶進帶出是違法的行為，此資料皆為網上根據盤後資訊大數據所取得訊息，'
        '非作為或被視為買進或售出標的的邀請或意象，'
        '請自行依據取得資訊評估風險與獲利，有賺有賠請斟酌。'
        '</div>'
    )
    p.append(
        '<div style="padding:10px 24px;background:#F5F5F5;border-radius:0 0 8px 8px;'
        'font-size:11px;color:#999;text-align:center;">'
        '此信由 GitHub Actions 自動寄出</div>'
    )

    body = "\n".join(p)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
        '<body style="margin:0;padding:20px;background:#EAEAEA;'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;\">"
        '<div style="max-width:680px;margin:0 auto;background:#FFF;border-radius:8px;'
        'overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.1);">'
        f'{body}</div></body></html>'
    )


def build_plain(summary: dict) -> str:
    ok = summary.get("success", False)
    lines = [
        "【外資分點狙擊分析】",
        f"狀態：{'成功' if ok else '失敗/部分失敗'}",
        f"產生時間：{summary.get('generated_at','')}",
        f"資料筆數：{summary.get('total_rows',0)}",
        "",
    ]
    ai_text = summary.get("ai_analysis", "")
    if ai_text:
        lines.append("【AI 分析】")
        lines.append(ai_text[:3000])
    lines.append("")
    lines.append("[免責聲明] 帶進帶出是違法的行為，此資料皆為網上根據盤後資訊大數據所取得訊息，"
                 "非作為或被視為買進或售出標的的邀請或意象，請自行依據取得資訊評估風險與獲利，有賺有賠請斟酌。")
    lines.append("")
    lines.append("（此信由 GitHub Actions 自動寄出）")
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
    mail_to = (os.environ.get("MAIL_TO") or "").strip()
    mail_bcc = (os.environ.get("MAIL_BCC") or "").strip()

    if not smtp_user or not smtp_pass:
        raise RuntimeError("SMTP_USER/SMTP_PASS 未設定")
    if not mail_to:
        raise RuntimeError("MAIL_TO 未設定")

    summary = load_summary()
    ymd = datetime.now(TZ).strftime("%Y-%m-%d")
    subject = os.environ.get("MAIL_SUBJECT", f"【外資分點狙擊分析】{ymd}（TW 18:00）")

    xlsx = pick_latest(os.path.join("output", "IKE_Report_*.xlsx"))
    pdf = pick_latest(os.path.join("output", "IKE_Report_*.pdf"))

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = mail_to
    if mail_bcc:
        msg["Bcc"] = mail_bcc
    msg["Subject"] = subject

    msg.set_content(build_plain(summary))
    msg.add_alternative(build_html(summary), subtype="html")

    for fpath, mime in [
        (xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (pdf, "application/pdf"),
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
