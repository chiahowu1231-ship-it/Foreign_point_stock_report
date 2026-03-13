import os
import json
import glob
import smtplib
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')


def load_summary():
    path = os.path.join('output', 'summary.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {
        'generated_at': datetime.now(TZ).isoformat(),
        'timezone': 'Asia/Taipei',
        'days': int(os.getenv('DAYS', '5')),
        'success': False,
        'errors': ['summary.json 不存在（run_report 可能未成功產生 summary）'],
        'total_rows': 0,
        'brokers_total': 0,
        'brokers_ok': 0,
        'brokers_fail': 0,
    }


def pick_latest(pattern: str):
    files = sorted(glob.glob(pattern))
    return files[-1] if files else None


def build_body(summary: dict):
    ok = summary.get('success', False)
    status_text = '成功' if ok else '失敗/部分失敗'

    lines = []
    lines.append('您好，')
    lines.append('')
    lines.append('Point Stock Daily Report 已產生。')
    lines.append(f"狀態：{status_text}")
    lines.append(f"產生時間：{summary.get('generated_at')}（{summary.get('timezone')}）")
    lines.append(f"查詢天數：{summary.get('days')} 日")
    lines.append(f"資料筆數：{summary.get('total_rows', 0)}")
    lines.append(
        f"分點狀態：OK {summary.get('brokers_ok', 0)} / FAIL {summary.get('brokers_fail', 0)}（總計 {summary.get('brokers_total', 0)}）"
    )

    server_url = os.getenv('GITHUB_SERVER_URL')
    repo = os.getenv('GITHUB_REPOSITORY')
    run_id = os.getenv('GITHUB_RUN_ID')
    if server_url and repo and run_id:
        lines.append('')
        lines.append(f"本次 Workflow 連結：{server_url}/{repo}/actions/runs/{run_id}")

    if summary.get('errors'):
        lines.append('')
        lines.append('抓取錯誤摘要（前 10 筆）：')
        for e in summary['errors'][:10]:
            lines.append(f"- {e}")

    lines.append('')
    lines.append('（此信由 GitHub Actions 自動寄出）')
    return '\n'.join(lines)


def main():
    smtp_host = os.environ['SMTP_HOST']
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ['SMTP_USER']
    smtp_pass = os.environ['SMTP_PASS']

    mail_from = os.environ.get('MAIL_FROM', smtp_user)
    mail_to = os.environ['MAIL_TO']
    mail_bcc = os.environ.get('MAIL_BCC', '')

    summary = load_summary()

    ymd = datetime.now(TZ).strftime('%Y%m%d')
    subject = os.environ.get('MAIL_SUBJECT', f'Point Stock Daily Report - {ymd}')

    xlsx = pick_latest(os.path.join('output', 'IKE_Report_*.xlsx'))
    pdf = pick_latest(os.path.join('output', 'IKE_Report_*.pdf'))

    msg = EmailMessage()
    msg['From'] = mail_from
    msg['To'] = mail_to
    if mail_bcc.strip():
        msg['Bcc'] = mail_bcc
    msg['Subject'] = subject

    msg.set_content(build_body(summary))

    attachments = [
        (xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        (pdf, 'application/pdf')
    ]
    for fpath, mime in attachments:
        if fpath and os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                data = f.read()
            maintype, subtype = mime.split('/', 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=os.path.basename(fpath))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print('[OK] Email sent.')


if __name__ == '__main__':
    main()
