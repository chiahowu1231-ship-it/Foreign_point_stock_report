
# src/run_report.py
# 純外資版（a 代號各異 / b 固定 9900）
# - DEBUG_HTML=1 會輸出 output/debug/*.html（E/B 各一份）方便比對瀏覽器 vs requests
# - 強化 parse_table：不只 script，也會從 href/onclick/tr html 中抓 GenLink2stk
# - 避免假成功：若解析空 or E/B 無交集 → 該券商算 FAIL；若總筆數=0 → summary success=False
# - PDF：優先使用 fonts/NotoSansTC-Regular.ttf 嵌入字型（避免亂碼）
#   若字型缺失：仍會產出「提示用 PDF」避免 artifacts 找不到

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

# 你的 config.py 必須是：LEGEND_BROKERS = { "1470_F": ("1470","label"), ... }
from config import LEGEND_BROKERS

TZ = ZoneInfo("Asia/Taipei")
DEFAULT_DAYS = 5
BASE_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zg/zgb/zgb0.djhtm"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def ensure_output_dir():
    os.makedirs("output", exist_ok=True)


def now_taipei():
    return datetime.now(TZ)


def safe_int(s: str) -> int:
    if s is None:
        return 0
    s = s.strip().replace(",", "")
    if s in ("", "--"):
        return 0
    try:
        return int(s)
    except ValueError:
        m = re.search(r"-?\d+", s)
        return int(m.group()) if m else 0


def dump_debug_html(tag: str, html: str):
    """DEBUG_HTML=1 時，把抓回來的 HTML 存到 output/debug/"""
    if os.getenv("DEBUG_HTML", "0") != "1":
        return
    os.makedirs("output/debug", exist_ok=True)
    path = os.path.join("output", "debug", f"{tag}.html")
    with open(path, "w", encoding="utf-8", errors="ignore") as f:
        f.write(html)


def fetch_html(url: str, timeout: int = 20, retries: int = 5, base_sleep: float = 1.0) -> str:
    """指數退避重試 + jitter（修正：確實會重試到 retries 次）"""
    headers = {
        "User-Agent": UA,
        "Referer": "https://fubon-ebrokerdj.fbs.com.tw/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            r.encoding = "big5"
            if r.status_code == 200 and r.text:
                return r.text
            last_err = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:
            last_err = e

        sleep_sec = base_sleep * (2 ** attempt) + random.uniform(0, 0.5)
        time.sleep(sleep_sec)

    raise last_err


def parse_table(html: str) -> dict:
    """
    強化解析：
    - 從 script / a[href|onclick] / tr html 找 GenLink2stk('ASxxxx','name')
    - 支援單/雙引號與空白換行
    - 欄位優先找 class t3n0/t3n1，不足則 fallback 所有 td
    """
    soup = BeautifulSoup(html, "html.parser")
    res = {}

    # 同時支援：GenLink2stk('AS2330','台積電') 及 GenLink2stk(\"AS2330\",\"台積電\")
    pat = re.compile(
        r"GenLink2stk\(\s*'AS(\w+)'\s*,\s*'(.+?)'\s*\)"
        r"|GenLink2stk\(\s*\"AS(\w+)\"\s*,\s*\"(.+?)\"\s*\)"
    )

    for tr in soup.find_all("tr"):
        found = None

        # ① script
        for sc in tr.find_all("script"):
            txt = sc.get_text() or ""
            m = pat.search(txt)
            if m:
                sid = m.group(1) or m.group(3)
                name = m.group(2) or m.group(4)
                found = (sid, name)
                break

        # ② href / onclick
        if not found:
            for a in tr.find_all("a"):
                blob = (a.get("href", "") or "") + " " + (a.get("onclick", "") or "")
                m = pat.search(blob)
                if m:
                    sid = m.group(1) or m.group(3)
                    name = m.group(2) or m.group(4)
                    found = (sid, name)
                    break

        # ③ tr html fallback
        if not found:
            m = pat.search(str(tr))
            if m:
                sid = m.group(1) or m.group(3)
                name = m.group(2) or m.group(4)
                found = (sid, name)

        if not found:
            continue

        sid, name = found

        tds = tr.find_all("td", class_=["t3n1", "t3n0"])
        if len(tds) < 3:
            tds = tr.find_all("td")
        if len(tds) < 3:
            continue

        res[sid] = {
            "name": name,
            "buy": tds[0].get_text(strip=True),
            "sell": tds[1].get_text(strip=True),
            "net": safe_int(tds[2].get_text(strip=True)),
        }

    return res


def get_stock_price(sid: str) -> float:
    """嘗試 .TW / .TWO，取最近 1 日 Close。"""
    for suffix in [".TW", ".TWO"]:
        try:
            data = yf.Ticker(f"{sid}{suffix}").history(period="1d")
            if not data.empty:
                return float(data["Close"].iloc[-1])
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

    for broker_key, (a_code, broker_label) in LEGEND_BROKERS.items():
        a_code = str(a_code)
        b_code = "9900"  # ✅ 純外資：固定 9900

        url_qty = f"{BASE_URL}?a={a_code}&b={b_code}&c=E&d={days}"
        url_amt = f"{BASE_URL}?a={a_code}&b={b_code}&c=B&d={days}"

        try:
            html_qty = fetch_html(url_qty)
            html_amt = fetch_html(url_amt)

            dump_debug_html(f"{a_code}_{b_code}_E", html_qty)
            dump_debug_html(f"{a_code}_{b_code}_B", html_amt)

            qty_map = parse_table(html_qty)
            amt_map = parse_table(html_amt)

            # ✅ 若解析結果皆空，視為失敗（避免假成功）
            if not qty_map and not amt_map:
                raise RuntimeError("解析結果為空（瀏覽器有資料但程式解析不到：HTML 結構不同或被擋）")

            common = set(qty_map.keys()) & set(amt_map.keys())
            if not common:
                raise RuntimeError("E/B 解析後無交集（欄位定位或解析規則可能需調整）")

            for sid in common:
                info = qty_map[sid]
                net_qty = info["net"]
                net_amt = amt_map[sid]["net"]  # 仟元
                avg_cost = round((net_amt / net_qty), 2) if net_qty > 0 else 0.0

                price = get_stock_price(sid)
                bias = (
                    round(((price - avg_cost) / avg_cost) * 100, 2)
                    if (price > 0 and avg_cost > 0)
                    else 0.0
                )

                rows.append({
                    "日期": start_time.strftime("%Y%m%d"),
                    "代碼": sid,
                    "名稱": info["name"],
                    "大戶": broker_label,
                    "買進": info["buy"],
                    "賣出": info["sell"],
                    "淨超": net_qty,
                    "區間均價": avg_cost,
                    "現價": round(price, 2),
                    "乖離率": f"{bias}%",
                })

            broker_ok += 1
            time.sleep(1.2)

        except Exception as e:
            broker_fail += 1
            msg = f"{broker_label}({broker_key}) 失敗：{str(e)}"
            errors.append(msg)
            failures.append({
                "日期": start_time.strftime("%Y%m%d"),
                "券商key": str(broker_key),
                "總公司(a)": a_code,
                "分點(b)": b_code,
                "券商名稱": broker_label,
                "錯誤訊息": str(e),
                "網址(張數E)": url_qty,
                "網址(金額B)": url_amt,
                "debug_E": f"{a_code}_{b_code}_E.html",
                "debug_B": f"{a_code}_{b_code}_B.html",
            })

    df = pd.DataFrame(rows, columns=[
        "日期", "代碼", "名稱", "大戶", "買進", "賣出", "淨超", "區間均價", "現價", "乖離率"
    ])

    fail_df = pd.DataFrame(failures, columns=[
        "日期", "券商key", "總公司(a)", "分點(b)", "券商名稱", "錯誤訊息",
        "網址(張數E)", "網址(金額B)", "debug_E", "debug_B"
    ])

    summary = {
        "generated_at": start_time.isoformat(),
        "timezone": "Asia/Taipei",
        "days": days,
        "total_rows": int(len(df)),
        "brokers_total": int(len(LEGEND_BROKERS)),
        "brokers_ok": broker_ok,
        "brokers_fail": broker_fail,
        "success": (broker_fail == 0),
        "errors": errors[:50],
    }

    # ✅ 總筆數為 0 → 直接視為失敗（避免「成功但0筆」）
    if summary["total_rows"] == 0:
        summary["success"] = False
        summary["errors"] = (summary.get("errors") or []) + [
            "總資料筆數為 0（解析全部為空 / 抓回非資料頁 / 解析規則需調整）"
        ]

    return df, fail_df, summary


def export_excel(df: pd.DataFrame, fail_df: pd.DataFrame, xlsx_path: str):
    from openpyxl.styles import PatternFill, Font
    from openpyxl.formatting.rule import FormulaRule

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Report")
        ws = writer.sheets["Report"]

        col_widths = {
            "A": 10, "B": 8, "C": 14, "D": 40,
            "E": 10, "F": 10, "G": 10, "H": 12,
            "I": 10, "J": 10
        }
        for col, w in col_widths.items():
            ws.column_dimensions[col].width = w

        # 乖離率(J欄)條件式上色：淺綠 / 藍 / 深紅
        last_row = ws.max_row
        if last_row >= 2:
            rng = f"J2:J{last_row}"

            fill_green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
            fill_blue  = PatternFill(start_color="BDD7EE", end_color="BDD7EE", fill_type="solid")
            fill_red   = PatternFill(start_color="8B0000", end_color="8B0000", fill_type="solid")
            font_white = Font(color="FFFFFF", bold=True)

            rule_red = FormulaRule(
                formula=[r'=VALUE(SUBSTITUTE($J2,"%",""))>10'],
                fill=fill_red, font=font_white, stopIfTrue=True
            )
            rule_blue = FormulaRule(
                formula=[r'=AND(VALUE(SUBSTITUTE($J2,"%",""))>3,VALUE(SUBSTITUTE($J2,"%",""))<=10)'],
                fill=fill_blue, stopIfTrue=True
            )
            rule_green = FormulaRule(
                formula=[r'=VALUE(SUBSTITUTE($J2,"%",""))<=3'],
                fill=fill_green, stopIfTrue=True
            )

            ws.conditional_formatting.add(rng, rule_red)
            ws.conditional_formatting.add(rng, rule_blue)
            ws.conditional_formatting.add(rng, rule_green)

        if fail_df is not None and not fail_df.empty:
            fail_df.to_excel(writer, index=False, sheet_name="Failures")
            ws2 = writer.sheets["Failures"]
            ws2.column_dimensions["A"].width = 10
            ws2.column_dimensions["B"].width = 12
            ws2.column_dimensions["C"].width = 10
            ws2.column_dimensions["D"].width = 10
            ws2.column_dimensions["E"].width = 35
            ws2.column_dimensions["F"].width = 60
            ws2.column_dimensions["G"].width = 55
            ws2.column_dimensions["H"].width = 55
            ws2.column_dimensions["I"].width = 22
            ws2.column_dimensions["J"].width = 22


def export_pdf(df: pd.DataFrame, pdf_path: str, summary: dict):
    """
    優先用嵌入字型（fonts/NotoSansTC-Regular.ttf）避免亂碼；
    若字型缺失，仍產出一份「提示 PDF」避免 artifacts 找不到。
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = os.path.join("fonts", "NotoSansTC-Regular.ttf")
    font_name = "NotoSansTC"

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=landscape(A4),
        leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18
    )

    styles = getSampleStyleSheet()
    normal = styles["Normal"]
    title_style = styles["Title"]

    if os.path.exists(font_path):
        pdfmetrics.registerFont(TTFont(font_name, font_path))
        normal.fontName = font_name
        title_style.fontName = font_name
    else:
        # fallback：至少讓 PDF 能生成
        normal.fontName = "Helvetica"
        title_style.fontName = "Helvetica"

    title = Paragraph("【外資分點狙擊分析】報表", title_style)
    meta = Paragraph(
        f"Generated: {summary['generated_at']} ({summary['timezone']}) &nbsp; "
        f"Days: {summary['days']} &nbsp; "
        f"Status: {'OK' if summary['success'] else 'PARTIAL/FAIL'} &nbsp; "
        f"Rows: {summary['total_rows']}",
        normal
    )

    elements = [title, Spacer(1, 8), meta, Spacer(1, 12)]

    if not os.path.exists(font_path):
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(
            "Font missing: fonts/NotoSansTC-Regular.ttf (PDF generated with Helvetica).",
            normal
        ))

    header = list(df.columns) if df is not None and not df.empty else ["日期","代碼","名稱","大戶","買進","賣出","淨超","區間均價","現價","乖離率"]
    data = [header] + (df.astype(str).values.tolist() if df is not None and not df.empty else [])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), normal.fontName),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAEAEA")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F7F7")]),
    ]))
    elements.append(table)

    if summary.get("errors"):
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("Errors (top 20):", normal))
        for e in summary["errors"][:20]:
            elements.append(Paragraph(f"- {e}", normal))

    doc.build(elements)


def main():
    ensure_output_dir()
    days = int(os.getenv("DAYS", str(DEFAULT_DAYS)))

    df, fail_df, summary = build_report(days)

    ymd = now_taipei().strftime("%Y%m%d")
    xlsx_path = os.path.join("output", f"IKE_Report_{ymd}.xlsx")
    pdf_path = os.path.join("output", f"IKE_Report_{ymd}.pdf")
    summary_path = os.path.join("output", "summary.json")

    export_excel(df, fail_df, xlsx_path)
    export_pdf(df, pdf_path, summary)

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"[OK] Excel: {xlsx_path}")
    print(f"[OK] PDF  : {pdf_path}")
    print(f"[OK] Summary: {summary_path}")

    # 若整體不成功（包含 total_rows==0 的情況），讓 step fail
    if not summary["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
