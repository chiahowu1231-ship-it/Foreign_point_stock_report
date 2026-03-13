import os
import re
import json
import time
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

from config import LEGEND_BROKERS

TZ = ZoneInfo('Asia/Taipei')

DEFAULT_DAYS = 5  # 固定 5 日區間（仍允許用環境變數 DAYS 覆蓋）
HEAD_BROKER = '9600'
BASE_URL = 'https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm'

UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)


def ensure_output_dir():
    os.makedirs('output', exist_ok=True)


def now_taipei():
    return datetime.now(TZ)


def safe_int(s: str) -> int:
    if s is None:
        return 0
    s = s.strip().replace(',', '')
    if s == '' or s == '--':
        return 0
    try:
        return int(s)
    except ValueError:
        m = re.search(r'-?\d+', s)
        return int(m.group()) if m else 0


def fetch_html(url: str, timeout: int = 15, retries: int = 5, base_sleep: float = 1.0) -> str:
    """指數退避重試 + jitter。

    sleep = base_sleep * (2**attempt) + random(0, 0.5)
    """
    headers = {
        'User-Agent': UA,
        'Referer': 'https://fubon-ebrokerdj.fbs.com.tw/'
    }
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.encoding = 'big5'
            if r.status_code == 200 and r.text:
                return r.text
            last_err = RuntimeError(f'HTTP {r.status_code}')
        except Exception as e:
            last_err = e

        sleep_sec = base_sleep * (2 ** attempt) + random.uniform(0, 0.5)
        time.sleep(sleep_sec)

    raise last_err


def parse_table(html: str) -> dict:
    """解析嘉實頁面表格。

    return: { stock_id: {'name':..., 'buy':..., 'sell':..., 'net': int } }
    """
    soup = BeautifulSoup(html, 'html.parser')
    res = {}
    for tr in soup.find_all('tr'):
        script = tr.find('script')
        if not script:
            continue
        script_text = script.get_text()
        if 'GenLink2stk' not in script_text:
            continue

        m = re.search(r"GenLink2stk\('AS(\w+)','(.+?)'\)", script_text)
        if not m:
            continue

        tds = tr.find_all('td', class_=['t3n1', 't3n0'])
        if len(tds) < 3:
            continue

        sid = m.group(1)
        name = m.group(2)
        buy_txt = tds[0].get_text(strip=True)
        sell_txt = tds[1].get_text(strip=True)
        net_txt = tds[2].get_text(strip=True)

        res[sid] = {
            'name': name,
            'buy': buy_txt,
            'sell': sell_txt,
            'net': safe_int(net_txt)
        }
    return res


def get_stock_price(sid: str) -> float:
    """嘗試 .TW / .TWO，取最近 1 日 Close。"""
    for suffix in ['.TW', '.TWO']:
        try:
            data = yf.Ticker(f'{sid}{suffix}').history(period='1d')
            if not data.empty:
                return float(data['Close'].iloc[-1])
        except Exception:
            continue
    return 0.0


def build_report(days: int):
    start_time = now_taipei()

    rows = []
    failures = []
    errors = []

    broker_ok = 0
    broker_fail = 0

    for broker_id, broker_name in LEGEND_BROKERS.items():
        url_qty = f"{BASE_URL}?a={HEAD_BROKER}&b={broker_id}&c=E&d={days}"
        url_amt = f"{BASE_URL}?a={HEAD_BROKER}&b={broker_id}&c=B&d={days}"

        try:
            html_qty = fetch_html(url_qty)
            html_amt = fetch_html(url_amt)
            qty_map = parse_table(html_qty)
            amt_map = parse_table(html_amt)

            for sid, info in qty_map.items():
                if sid not in amt_map:
                    continue

                net_qty = info['net']
                net_amt = amt_map[sid]['net']  # 仟元
                avg_cost = round((net_amt / net_qty), 2) if net_qty > 0 else 0.0

                price = get_stock_price(sid)
                bias = round(((price - avg_cost) / avg_cost) * 100, 2) if (price > 0 and avg_cost > 0) else 0.0

                rows.append({
                    '日期': start_time.strftime('%Y%m%d'),
                    '代碼': sid,
                    '名稱': info['name'],
                    '大戶': broker_name,
                    '買進': info['buy'],
                    '賣出': info['sell'],
                    '淨超': net_qty,
                    '區間均價': avg_cost,
                    '現價': round(price, 2),
                    '乖離率': f"{bias}%"
                })

            broker_ok += 1
            time.sleep(1.2)  # 防鎖間隔

        except Exception as e:
            broker_fail += 1
            msg = f"{broker_name}({broker_id}) 失敗：{str(e)}"
            errors.append(msg)
            failures.append({
                '日期': start_time.strftime('%Y%m%d'),
                '分點代號': broker_id,
                '分點名稱': broker_name,
                '錯誤訊息': str(e),
                '網址(張數E)': url_qty,
                '網址(金額B)': url_amt
            })

    df = pd.DataFrame(rows, columns=[
        '日期', '代碼', '名稱', '大戶', '買進', '賣出', '淨超', '區間均價', '現價', '乖離率'
    ])

    fail_df = pd.DataFrame(failures, columns=[
        '日期', '分點代號', '分點名稱', '錯誤訊息', '網址(張數E)', '網址(金額B)'
    ])

    summary = {
        'generated_at': start_time.isoformat(),
        'timezone': 'Asia/Taipei',
        'days': days,
        'total_rows': int(len(df)),
        'brokers_total': int(len(LEGEND_BROKERS)),
        'brokers_ok': broker_ok,
        'brokers_fail': broker_fail,
        'success': (broker_fail == 0),
        'errors': errors[:50]
    }

    return df, fail_df, summary


def export_excel(df: pd.DataFrame, fail_df: pd.DataFrame, xlsx_path: str):
    from openpyxl.styles import PatternFill, Font
    from openpyxl.formatting.rule import FormulaRule

    with pd.ExcelWriter(xlsx_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Report')
        ws = writer.sheets['Report']

        # 欄寬
        col_widths = {
            'A': 10, 'B': 8, 'C': 14, 'D': 26,
            'E': 10, 'F': 10, 'G': 10, 'H': 12,
            'I': 10, 'J': 10
        }
        for col, w in col_widths.items():
            ws.column_dimensions[col].width = w

        # 乖離率(J欄)條件式上色：淺綠 / 藍 / 深紅
        last_row = ws.max_row
        if last_row >= 2:
            rng = f"J2:J{last_row}"

            fill_green = PatternFill(start_color='C6EFCE', end_color='C6EFCE', fill_type='solid')  # 淺綠
            fill_blue = PatternFill(start_color='BDD7EE', end_color='BDD7EE', fill_type='solid')    # 藍
            fill_red = PatternFill(start_color='8B0000', end_color='8B0000', fill_type='solid')     # 深紅
            font_white = Font(color='FFFFFF', bold=True)

            rule_red = FormulaRule(
                formula=[r'=VALUE(SUBSTITUTE($J2,"%",""))>10'],
                fill=fill_red,
                font=font_white,
                stopIfTrue=True
            )
            rule_blue = FormulaRule(
                formula=[r'=AND(VALUE(SUBSTITUTE($J2,"%",""))>3,VALUE(SUBSTITUTE($J2,"%",""))<=10)'],
                fill=fill_blue,
                stopIfTrue=True
            )
            rule_green = FormulaRule(
                formula=[r'=VALUE(SUBSTITUTE($J2,"%",""))<=3'],
                fill=fill_green,
                stopIfTrue=True
            )

            ws.conditional_formatting.add(rng, rule_red)
            ws.conditional_formatting.add(rng, rule_blue)
            ws.conditional_formatting.add(rng, rule_green)

        # Failures sheet
        if fail_df is not None and not fail_df.empty:
            fail_df.to_excel(writer, index=False, sheet_name='Failures')
            ws2 = writer.sheets['Failures']
            ws2.column_dimensions['A'].width = 10
            ws2.column_dimensions['B'].width = 10
            ws2.column_dimensions['C'].width = 35
            ws2.column_dimensions['D'].width = 60
            ws2.column_dimensions['E'].width = 55
            ws2.column_dimensions['F'].width = 55


def export_pdf(df: pd.DataFrame, pdf_path: str, summary: dict):
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont

    # 繁中 CID 字型
    pdfmetrics.registerFont(UnicodeCIDFont('MSung-Light'))

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18
    )

    styles = getSampleStyleSheet()
    title_style = styles['Title']
    title_style.fontName = 'MSung-Light'

    normal_style = styles['Normal']
    normal_style.fontName = 'MSung-Light'

    title = Paragraph('Point Stock Daily Report', title_style)
    meta = Paragraph(
        f"產生時間：{summary['generated_at']}（{summary['timezone']}）\u3000"
        f"查詢區間：{summary['days']} 日\u3000"
        f"結果：{'成功' if summary['success'] else '部分失敗'}\u3000"
        f"筆數：{summary['total_rows']}",
        normal_style
    )

    elements = [title, Spacer(1, 8), meta, Spacer(1, 12)]

    header = list(df.columns)
    data = [header] + df.astype(str).values.tolist()

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'MSung-Light'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EAEAEA')),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F7F7F7')]),
    ]))

    elements.append(table)

    if summary.get('errors'):
        elements.append(Spacer(1, 12))
        elements.append(Paragraph('抓取失敗清單（前 50 筆）：', normal_style))
        for e in summary['errors']:
            elements.append(Paragraph(f'- {e}', normal_style))

    doc.build(elements)


def main():
    ensure_output_dir()
    days = int(os.getenv('DAYS', str(DEFAULT_DAYS)))

    df, fail_df, summary = build_report(days)

    ymd = now_taipei().strftime('%Y%m%d')
    xlsx_path = os.path.join('output', f'IKE_Report_{ymd}.xlsx')
    pdf_path = os.path.join('output', f'IKE_Report_{ymd}.pdf')
    summary_path = os.path.join('output', 'summary.json')

    export_excel(df, fail_df, xlsx_path)
    export_pdf(df, pdf_path, summary)

    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f'[OK] Excel: {xlsx_path}')
    print(f'[OK] PDF  : {pdf_path}')
    print(f'[OK] Summary: {summary_path}')

    if not summary['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
