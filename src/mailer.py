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
