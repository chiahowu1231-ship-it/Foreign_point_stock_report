
# src/mailer.py
import os
import json
import glob
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
        "errors": ["summary.json 不存在（run_report 可能未成功產生 summary）"],
        "total_rows": 0,
        "brokers_total": 0,
        "brokers_ok": 0,
        "brokers_fail": 0,
    }


def pick_latest(pattern: str):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def _fmt_int(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def build_body(summary: dict):
    """
    純文字排版（iPhone/Apple Mail 友善）：
    - 不用表格、不用對齊空白
    - 每家外資間空一行
    - 每檔用 1) 2) 3) 條列
    """
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

    # ===== ✅ 每家外資買超 Top 3（iPhone 友善純文字）=====
    top_preview = summary.get("top_preview") or []
    top_n_in_mail = 3  # 你指定：信件正文只顯示 Top 3

    lines.append("")
    lines.append(f"【每家外資買超 Top {top_n_in_mail}】（依外資總淨超排序）")

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

            # 只取 Top 3
            for idx, r in enumerate(rows[:top_n_in_mail], start=1):
                sid = r.get("sid", "")
                name = r.get("name", "")
                net = r.get("net", 0)
                avg = r.get("avg", "")
                price = r.get("price", "")
                bias = r.get("bias", "")

                # 單行條列：避免 iPhone 寬度換行後看起來擠在一起
                lines.append(
                    f"  {idx}) {sid} {name}｜淨超 {_fmt_int(net)} 張｜均價 {avg}｜現價 {price}｜乖離 {bias}"
                )

    # ===== 錯誤摘要 =====
    if summary.get("errors"):
        lines.append("")
        lines.append("抓取錯誤摘要（前 10 筆）：")
        for e in summary["errors"][:10]:
            lines.append(f"- {e}")

    lines.append("")
    lines.append("（此信由 GitHub Actions 自動寄出）")
    return "\n".join(lines)


def main():
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    mail_from = os.environ.get("MAIL_FROM", smtp_user)
    mail_to = os.environ["MAIL_TO"]
    mail_bcc = os.environ.get("MAIL_BCC", "")

    summary = load_summary()

    # 你 workflow 會注入 MAIL_SUBJECT；若沒有就用預設
    ymd = datetime.now(TZ).strftime("%Y-%m-%d")
    subject = os.environ.get("MAIL_SUBJECT", f"【外資分點狙擊分析】{ymd}（TW 14:30）")

    xlsx = pick_latest(os.path.join("output", "IKE_Report_*.xlsx"))
    pdf = pick_latest(os.path.join("output", "IKE_Report_*.pdf"))

    msg = EmailMessage()
    msg["From"] = mail_from
    msg["To"] = mail_to
    if mail_bcc.strip():
        msg["Bcc"] = mail_bcc
    msg["Subject"] = subject

    msg.set_content(build_body(summary))

    attachments = [
        (xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        (pdf, "application/pdf"),
    ]
    for fpath, mime in attachments:
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
