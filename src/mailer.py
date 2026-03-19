# src/mailer.py
import os
import json
import glob
import smtplib
import textwrap
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
        "errors": ["summary.json 不存在（run_report 可能未成功產生 summary）"],
        "total_rows": 0,
        "brokers_total": 0,
        "brokers_ok": 0,
        "brokers_fail": 0,
        "top_preview": [],
        "ai_analysis": "",
    }


def pick_latest(pattern: str):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _fmt_int(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _fmt_billion(n):
    """將元轉為億元顯示"""
    try:
        v = int(n)
        if abs(v) >= 1e8:
            return f"{v/1e8:+.1f}億"
        elif abs(v) >= 1e4:
            return f"{v/1e4:+.0f}萬"
        return f"{v:+,}"
    except Exception:
        return str(n)


def wrap_text(s: str, width: int = 72) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    paras = [p.strip() for p in s.split("\n") if p.strip()]
    return "\n\n".join(textwrap.fill(p, width=width) for p in paras)


def build_body(summary: dict):
    ok = summary.get("success", False)
    status_text = "成功" if ok else "失敗/部分失敗"

    lines = []
    lines.append("您好，")
    lines.append("")
    lines.append("【外資分點狙擊分析】報表已產生。")
    lines.append(f"狀態：{status_text}")
    lines.append(f"產生時間：{summary.get('generated_at')}（{summary.get('timezone')}）")
    lines.append(f"查詢天數：{summary.get('days')} 日")
    lines.append(f"資料筆數：{summary.get('total_rows', 0)}")
    lines.append(
        f"分點狀態：OK {summary.get('brokers_ok', 0)} / FAIL {summary.get('brokers_fail', 0)}（總計 {summary.get('brokers_total', 0)}）"
    )

    server_url = os.getenv("GITHUB_SERVER_URL")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    if server_url and repo and run_id:
        lines.append("")
        lines.append(f"本次 Workflow 連結：{server_url}/{repo}/actions/runs/{run_id}")

    # Top 3
    top_preview = summary.get("top_preview") or []
    lines.append("")
    lines.append("【每家外資買超 Top 3】（依外資總淨超排序）")

    if not top_preview:
        lines.append("本次無可用 Top 資料（可能當天無資料或尚未產生 top_preview）。")
    else:
        for block in top_preview:
            broker = str(block.get("broker", ""))
            total_net = block.get("total_net", 0)
            rows = block.get("rows") or []

            lines.append("")
            lines.append(f"■ {broker}")
            lines.append(f"  總淨超：{_fmt_int(total_net)} 張")

            for idx, r in enumerate(rows[:3], start=1):
                sid = r.get("sid", "")
                name = r.get("name", "")
                net = r.get("net", 0)
                avg = r.get("avg", "")
                price = r.get("price", "")
                bias = r.get("bias", "")
                lines.append(
                    f"  {idx}) {sid} {name}｜淨超 {_fmt_int(net)} 張｜均價 {avg}｜現價 {price}｜乖離 {bias}"
                )

    # AI Analysis
    ai_text = summary.get("ai_analysis", "")
    ai_provider = summary.get("ai_provider", "")
    ai_model = summary.get("ai_model", "")

    # ===== 大盤籌碼摘要（v9 新增）=====
    market = summary.get("market_data") or {}
    has_market = False

    # 三大法人
    inst = market.get("institutional") or []
    if inst:
        has_market = True
        lines.append("")
        lines.append("【三大法人買賣超】（近日，億元）")
        for d in inst[:6]:
            fg = d["foreign"]["net"]
            tr = d["trust"]["net"]
            dl = d["dealer"]["net"]
            total = d.get("total_net", fg + tr + dl)
            lines.append(
                f"  {d['date']}｜外資 {_fmt_billion(fg)}｜投信 {_fmt_billion(tr)}｜自營 {_fmt_billion(dl)}｜合計 {_fmt_billion(total)}"
            )

    # 大盤量能
    taiex = market.get("taiex") or []
    if taiex:
        has_market = True
        lines.append("")
        lines.append("【大盤指數＋成交金額】")
        for d in taiex[:6]:
            amt = d.get("amount_billion", 0)
            close_val = d.get("close", 0)
            chg = d.get("change", 0)
            sign = "+" if chg > 0 else ""
            idx_str = f"收盤 {close_val}" if close_val else ""
            chg_str = f"漲跌 {sign}{chg}" if chg else ""
            lines.append(f"  {d['date']}｜{idx_str}｜{chg_str}｜成交 {amt:.0f}億")

        # 量能比較
        if len(taiex) >= 2:
            today_amt = taiex[0].get("amount_billion", 0)
            prev_amts = [d.get("amount_billion", 0) for d in taiex[1:6]]
            avg5 = sum(prev_amts) / max(len(prev_amts), 1) if prev_amts else 0
            if avg5 > 0:
                ratio = today_amt / avg5
                if ratio > 1.2:
                    lines.append(f"  ★ 今日 {today_amt:.0f}億 vs 5日均 {avg5:.0f}億 → 放量 {ratio:.1f}倍")
                elif ratio < 0.8:
                    lines.append(f"  ★ 今日 {today_amt:.0f}億 vs 5日均 {avg5:.0f}億 → 縮量 {ratio:.1f}倍")
                else:
                    lines.append(f"  ★ 今日 {today_amt:.0f}億 vs 5日均 {avg5:.0f}億 → 持平")

    # 融資融券
    margin = market.get("margin") or []
    if margin:
        has_market = True
        lines.append("")
        lines.append("【融資融券變化】")
        for d in margin[:3]:
            mc = d.get("margin_change", 0)
            mb = d.get("margin_balance", 0)
            sc = d.get("short_change", 0)
            sb = d.get("short_balance", 0)
            lines.append(
                f"  {d['date']}｜融資增減 {_fmt_int(mc)}張 (餘額{_fmt_int(mb)})｜融券增減 {_fmt_int(sc)}張 (餘額{_fmt_int(sb)})"
            )

    # 期貨籌碼
    futures = market.get("futures") or []
    if futures:
        has_market = True
        lines.append("")
        lines.append("【期貨三大法人台指期淨部位（口）】")
        for d in futures[:3]:
            fg = d.get("foreign_net_oi", 0)
            tr = d.get("trust_net_oi", 0)
            dl = d.get("dealer_net_oi", 0)
            lines.append(
                f"  {d['date']}｜外資 {_fmt_int(fg)}｜投信 {_fmt_int(tr)}｜自營 {_fmt_int(dl)}"
            )

    # 千張大戶
    tdcc = market.get("tdcc") or []
    if tdcc:
        has_market = True
        lines.append("")
        lines.append("【千張大戶持股比例（觀察個股）】")
        for d in tdcc:
            sid = d.get("stock_id", "")
            pct = d.get("pct_1000_plus", 0)
            cnt = d.get("holders_1000_plus", 0)
            lines.append(f"  {sid}｜千張以上 {cnt} 人｜持股 {pct:.1f}%")

    if not has_market:
        lines.append("")
        lines.append("【大盤籌碼】本次未取得（可能為非交易日或 API 異常）")

    # ===== AI 分析 =====
    lines.append("")
    lines.append("【AI 分析】")

    if ai_text:
        head = "來源："
        if ai_provider:
            head += ai_provider
        if ai_model:
            head += f" {ai_model}"
        if head != "來源：":
            lines.append(head.strip())
        lines.append("")
        lines.append(wrap_text(ai_text, width=72))
    else:
        lines.append("本次尚未取得 AI 分析（可能未設定 GEMINI_API_KEY 或分析步驟未執行）。")

    if summary.get("errors"):
        lines.append("")
        lines.append("抓取錯誤摘要（前 10 筆）：")
        for e in summary["errors"][:10]:
            lines.append(f"- {e}")

    lines.append("")
    lines.append("（此信由 GitHub Actions 自動寄出）")
    return "\n".join(lines)


def _safe_int_env(name: str, default: int) -> int:
    v = (os.environ.get(name, "") or "").strip()
    if not v:
        return default
    try:
        return int(v)
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
        raise RuntimeError("SMTP_USER/SMTP_PASS 未設定（請在 GitHub Secrets 設定）")
    if not mail_to:
        raise RuntimeError("MAIL_TO 未設定（請在 GitHub Secrets 設定）")

    summary = load_summary()

    ymd = datetime.now(TZ).strftime("%Y-%m-%d")
    subject = os.environ.get("MAIL_SUBJECT", f"【外資分點狙擊分析】{ymd}（TW 14:30）")

    xlsx = pick_latest(os.path.join("output", "IKE_Report_*.xlsx"))
    pdf = pick_latest(os.path.join("output", "IKE_Report_*.pdf"))

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = mail_to
    if mail_bcc:
        msg["Bcc"] = mail_bcc
    msg["Subject"] = subject

    msg.set_content(build_body(summary))

    for fpath, mime in [
        (xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (pdf, "application/pdf"),
    ]:
        if fpath and os.path.exists(fpath):
            with open(fpath, "rb") as f:
                data = f.read()
            maintype, subtype = mime.split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(fpath))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print("[OK] Email sent.")


if __name__ == "__main__":
    main()
