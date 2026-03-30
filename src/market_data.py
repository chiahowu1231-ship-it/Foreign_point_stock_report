# src/market_data.py
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  大盤籌碼資料抓取模組
#  - 三大法人買賣超（外資、投信、自營商）
#  - 大盤指數 + 成交量（近 6 天含當日）
#  - 融資融券變化（全市場）
#  - 期貨籌碼（三大法人台指期 + 大額交易人）
#  - 千張大戶持股比例（針對 top 觀察清單個股）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import os
import re
import json
import time
import random
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

TZ = ZoneInfo("Asia/Taipei")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


def _safe_int(s) -> int:
    """安全轉 int：處理逗號、空值、--"""
    if s is None:
        return 0
    s = str(s).strip().replace(",", "").replace(" ", "")
    if s in ("", "--", "-", "N/A"):
        return 0
    try:
        return int(s)
    except ValueError:
        m = re.search(r"-?[\d]+", s)
        return int(m.group()) if m else 0


def _safe_float(s) -> float:
    if s is None:
        return 0.0
    s = str(s).strip().replace(",", "").replace(" ", "")
    if s in ("", "--", "-", "N/A"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?[\d.]+", s)
        return float(m.group()) if m else 0.0


def _get_json(url: str, params: dict = None, retries: int = 3, timeout: int = 15) -> Optional[dict]:
    """帶重試的 JSON GET（對 TWSE 回傳格式寬容）"""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                # TWSE 回傳可能是 {"stat":"OK","data":[...]} 或直接 {"data":[...]} 或 [...]
                if isinstance(data, list):
                    return {"data": data}
                if isinstance(data, dict):
                    # 有任何 key 包含 data/list/array 就回傳
                    return data
            print(f"  [market_data] {url} HTTP {r.status_code}")
        except Exception as e:
            print(f"  [market_data] {url} attempt {attempt+1} failed: {e}")
        time.sleep(1.0 + random.uniform(0, 0.5))
    return None


def _get_html(url: str, params: dict = None, retries: int = 3, timeout: int = 15) -> Optional[str]:
    """帶重試的 HTML GET"""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.encoding = "utf-8"
            if r.status_code == 200 and r.text:
                return r.text
        except Exception as e:
            print(f"  [market_data] HTML {url} attempt {attempt+1}: {e}")
        time.sleep(1.0 + random.uniform(0, 0.5))
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 三大法人買賣超
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_institutional_trading(date_str: str) -> Optional[dict]:
    """
    抓取三大法人買賣超（TWSE BFI82U）
    date_str: 'YYYYMMDD'

    TWSE BFI82U 實際欄位名稱（無「合計」行時的結構）：
      row 0: 自營商(自行買賣)
      row 1: 自營商(避險)
      row 2: 投信
      row 3: 外資及陸資(不含外資自營商)
      row 4: 外資自營商
      row 5: 合計（= 三大法人合計）
    有時會有「自營商合計」「外資及陸資合計」等合計行。
    """
    # ⚠️ Bug fix: TWSE BFI82U 正確參數是 "date"，不是 "dayDate"
    url = "https://www.twse.com.tw/exchangeReport/BFI82U"
    data = _get_json(url, params={"response": "json", "date": date_str})
    if not data or not data.get("data"):
        return None

    result = {
        "date": date_str,
        "foreign": {"buy": 0, "sell": 0, "net": 0},
        "trust": {"buy": 0, "sell": 0, "net": 0},
        "dealer": {"buy": 0, "sell": 0, "net": 0},
        "total_net": 0,
    }

    # 累加器（外資和自營商各有 2 個子行需要加總）
    foreign_buy, foreign_sell, foreign_net = 0, 0, 0
    dealer_buy, dealer_sell, dealer_net = 0, 0, 0
    has_foreign_total = False
    has_dealer_total = False

    for row in data["data"]:
        if len(row) < 4:
            continue
        name = str(row[0]).strip()
        b, s, n = _safe_int(row[1]), _safe_int(row[2]), _safe_int(row[3])

        # ── 外資 ──
        # 如果有「外資及陸資合計」直接用它
        if "外資" in name and "合計" in name:
            result["foreign"] = {"buy": b, "sell": s, "net": n}
            has_foreign_total = True
        # 否則累加「外資及陸資(不含外資自營商)」+「外資自營商」
        elif "外資" in name:
            foreign_buy += b
            foreign_sell += s
            foreign_net += n

        # ── 投信 ──
        elif "投信" in name:
            result["trust"] = {"buy": b, "sell": s, "net": n}

        # ── 自營商 ──
        elif "自營商" in name and "合計" in name:
            result["dealer"] = {"buy": b, "sell": s, "net": n}
            has_dealer_total = True
        elif "自營商" in name:
            dealer_buy += b
            dealer_sell += s
            dealer_net += n

        # ── 三大法人合計（最後一行通常是「合計」）──
        elif ("三大法人" in name) or (name == "合計"):
            result["total_net"] = n

    # 如果沒有找到合計行，用累加值
    if not has_foreign_total and (foreign_buy or foreign_sell or foreign_net):
        result["foreign"] = {"buy": foreign_buy, "sell": foreign_sell, "net": foreign_net}
    if not has_dealer_total and (dealer_buy or dealer_sell or dealer_net):
        result["dealer"] = {"buy": dealer_buy, "sell": dealer_sell, "net": dealer_net}

    # 如果 total_net 仍為 0，手動加總
    if result["total_net"] == 0:
        result["total_net"] = (
            result["foreign"]["net"] + result["trust"]["net"] + result["dealer"]["net"]
        )

    return result


def fetch_institutional_history(days: int = 6) -> list:
    """抓取近 N 天的三大法人買賣超"""
    results = []
    today = datetime.now(TZ)

    # 往前多抓幾天（跳過假日）
    checked = 0
    for delta in range(days * 2 + 5):
        if len(results) >= days:
            break
        d = today - timedelta(days=delta)
        if d.weekday() >= 5:  # 跳過六日
            continue
        date_str = d.strftime("%Y%m%d")
        print(f"  [institutional] 抓取 {date_str}...")
        data = fetch_institutional_trading(date_str)
        if data and any(
            data[k]["net"] != 0 for k in ("foreign", "trust", "dealer")
        ):
            results.append(data)
            print(f"    ✓ 外資={data['foreign']['net']:,} 投信={data['trust']['net']:,} 自營={data['dealer']['net']:,}")
        time.sleep(0.8 + random.uniform(0, 0.3))

    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 大盤指數 + 成交量（近 N 天）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _roc_date(s: str) -> str:
    """民國年 115/03/19 → 西元 20260319"""
    parts = str(s).strip().split("/")
    if len(parts) == 3:
        y = int(parts[0]) + 1911
        return f"{y}{int(parts[1]):02d}{int(parts[2]):02d}"
    return str(s).replace("/", "")


def fetch_taiex_daily(days: int = 6) -> list:
    """
    抓取加權指數 + 每日成交量
    來源 1（主要）: 舊版 API exchangeReport/FMTQIK（社群驗證穩定）
      回傳: { "stat":"OK", "data":[ ["115/03/19","8,413,...","381,069,...","3,123,...","22,345.67","-123.45"], ... ] }
      欄位: [日期(民國), 成交股數, 成交金額(元), 成交筆數, 加權指數, 漲跌點數]
    來源 2（備援）: Open Data API openapi.twse.com.tw/v1/exchangeReport/FMTQIK
      回傳: JSON array [ {"Date":"115/03/19","TradeVolume":"8413906547",...}, ... ]
    """
    today = datetime.now(TZ)
    results_map = {}

    # ── 來源 1：舊版 API（非 rwd，更穩定） ──
    for month_offset in [0, 1]:
        target = today.replace(day=1) - timedelta(days=month_offset * 28)
        ym = target.strftime("%Y%m01")
        print(f"  [taiex] 舊版API date={ym}")

        url = "https://www.twse.com.tw/exchangeReport/FMTQIK"
        data = _get_json(url, params={"response": "json", "date": ym})
        if not data:
            print(f"  [taiex] 舊版API 無回應")
            continue

        rows = data.get("data") or []
        stat = data.get("stat", "?")
        print(f"  [taiex] 舊版API stat={stat}, rows={len(rows)}")
        if rows:
            print(f"  [taiex] 第一行範例({len(rows[0])}欄): {rows[0]}")

        for row in rows:
            if len(row) < 3:
                continue
            date_str = _roc_date(row[0])
            amount_raw = _safe_int(row[2])
            entry = {
                "date": date_str,
                "volume_shares": _safe_int(row[1]),
                "amount_billion": round(amount_raw / 1e8, 0),
            }
            if len(row) >= 5 and row[4]:
                entry["close"] = _safe_float(row[4])
            if len(row) >= 6 and row[5]:
                entry["change"] = _safe_float(row[5])
            results_map[date_str] = entry
        time.sleep(0.8)

    # ── 來源 2：Open Data API（若來源 1 失敗） ──
    if not results_map:
        print(f"  [taiex] 舊版API 無資料，嘗試 Open Data API...")
        url2 = "https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK"
        data2 = _get_json(url2)
        if data2:
            rows2 = data2.get("data") if isinstance(data2, dict) else data2 if isinstance(data2, list) else []
            # 如果 _get_json 已經包裝了 list → {"data": [...]}
            if isinstance(data2, dict) and "data" not in data2:
                # Open Data 可能直接返回 list，被 _get_json 包裝成 {"data": [...]}
                rows2 = list(data2.values())[0] if data2 else []
            print(f"  [taiex] OpenData rows={len(rows2)}")

            for item in rows2:
                if isinstance(item, dict):
                    date_str = _roc_date(item.get("Date", item.get("日期", "")))
                    amt = _safe_int(item.get("TradeValue", item.get("成交金額", "0")))
                    vol = _safe_int(item.get("TradeVolume", item.get("成交股數", "0")))
                    close = _safe_float(item.get("TAIEX", item.get("發行量加權股價指數", "0")))
                    change = _safe_float(item.get("Change", item.get("漲跌點數", "0")))
                    results_map[date_str] = {
                        "date": date_str,
                        "volume_shares": vol,
                        "amount_billion": round(amt / 1e8, 0),
                        "close": close,
                        "change": change,
                    }
                elif isinstance(item, list) and len(item) >= 3:
                    date_str = _roc_date(item[0])
                    results_map[date_str] = {
                        "date": date_str,
                        "volume_shares": _safe_int(item[1]),
                        "amount_billion": round(_safe_int(item[2]) / 1e8, 0),
                        "close": _safe_float(item[4]) if len(item) >= 5 else 0,
                        "change": _safe_float(item[5]) if len(item) >= 6 else 0,
                    }

    results = sorted(results_map.values(), key=lambda x: x["date"], reverse=True)
    print(f"  [taiex] 最終 {len(results)} 天")
    if results:
        print(f"  [taiex] 最新: {results[0]}")
    return results[:days]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 融資融券變化（全市場）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_margin_trading(date_str: str) -> Optional[dict]:
    """
    抓取融資融券（TWSE MI_MARGN, selectType=MS 為整體市場統計）
    使用舊版 API（非 rwd），回傳格式更穩定。
    creditList: 2 rows [融資行, 融券行]
      融資: [買進, 賣出, 現金償還, 前日餘額, 今日餘額, 限額]
      融券: [賣出, 回補, 現券償還, 前日餘額, 今日餘額, 限額]
    """
    # 舊版 API
    url = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
    data = _get_json(url, params={
        "response": "json",
        "date": date_str,
        "selectType": "MS",
    })
    if not data:
        return None

    result = {
        "date": date_str,
        "margin_buy": 0, "margin_sell": 0, "margin_balance": 0, "margin_change": 0,
        "short_sell": 0, "short_cover": 0, "short_balance": 0, "short_change": 0,
    }

    # ── TWSE stat 欄位檢查（stat="no data" 代表該日無交易） ──
    if data.get("stat", "").lower() not in ("ok", ""):
        print(f"  [margin] {date_str}: stat={data.get('stat')}，非交易日或資料不存在")
        return None

    # ⚠️ Bug fix: TWSE MI_MARGN 現行 API 主要回傳 key 為 "data"（非 "creditList"）
    # 嘗試順序改為 data → creditList → totalList，增加兜底的 HTML 欄位解析
    rows = None
    for key in ["data", "creditList", "totalList"]:
        if key in data and isinstance(data[key], list) and len(data[key]) > 0:
            rows = data[key]
            print(f"  [margin] {date_str}: found key='{key}', rows={len(rows)}")
            break

    if not rows:
        print(f"  [margin] {date_str}: 無資料 (keys={list(data.keys())}, stat={data.get('stat','')})")
        return None

    # Debug: 印出結構
    for i, row in enumerate(rows[:3]):
        if isinstance(row, list):
            print(f"  [margin] row[{i}]({len(row)}欄): {row[:6]}")

    # selectType=MS → 通常 2 行：row[0]=融資, row[1]=融券
    # TWSE 欄位：[買進, 賣出, 現金償還, 前日餘額, 今日餘額, 限額]
    for i, row in enumerate(rows):
        if not isinstance(row, list) or len(row) < 4:
            continue

        if i == 0:  # 第一行 = 融資
            result["margin_buy"]  = _safe_int(row[0])
            result["margin_sell"] = _safe_int(row[1])
            prev_bal = _safe_int(row[3])
            today_bal = _safe_int(row[4]) if len(row) > 4 else 0
            result["margin_balance"] = today_bal
            result["margin_change"]  = today_bal - prev_bal

        elif i == 1:  # 第二行 = 融券
            result["short_sell"]  = _safe_int(row[0])
            result["short_cover"] = _safe_int(row[1])
            prev_bal  = _safe_int(row[3])
            today_bal = _safe_int(row[4]) if len(row) > 4 else 0
            result["short_balance"] = today_bal
            result["short_change"]  = today_bal - prev_bal

    if result["margin_balance"] or result["short_balance"] or result["margin_buy"]:
        return result
    return None


def fetch_margin_history(days: int = 6) -> list:
    """抓取近 N 天的融資融券資料"""
    results = []
    today = datetime.now(TZ)

    for delta in range(days * 2 + 5):
        if len(results) >= days:
            break
        d = today - timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        print(f"  [margin] 抓取 {date_str}...")
        data = fetch_margin_trading(date_str)
        if data and data["margin_balance"] != 0:
            results.append(data)
        time.sleep(0.8 + random.uniform(0, 0.3))

    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 期貨籌碼（三大法人台指期淨部位 + 大額交易人）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_futures_institutional(date_str: str) -> Optional[dict]:
    """
    抓取期交所三大法人期貨（台指期）淨部位
    TAIFEX futContractsDate 表格欄位結構（13 欄）：
      [0]  身份別
      [1]  多方-交易口數        [2]  多方-交易契約金額(千元)
      [3]  空方-交易口數        [4]  空方-交易契約金額(千元)
      [5]  多空淨額-交易口數    [6]  多空淨額-交易契約金額(千元)
      [7]  多方-未平倉口數      [8]  多方-未平倉契約金額(千元)
      [9]  空方-未平倉口數      [10] 空方-未平倉契約金額(千元)
      [11] 多空淨額-未平倉口數  ← 我們要這個
      [12] 多空淨額-未平倉契約金額(千元)
    """
    url = "https://www.taifex.com.tw/cht/3/futContractsDate"

    try:
        from bs4 import BeautifulSoup

        form_data = {
            "queryType": "1",
            "goession": "",
            "doession": "",
            "commodity_id": "TX",
            "queryDate": f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:8]}",
        }

        r = requests.post(url, data=form_data, headers=HEADERS, timeout=15)
        r.encoding = "utf-8"

        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        result = {
            "date": date_str,
            "foreign_net_oi": 0,
            "trust_net_oi": 0,
            "dealer_net_oi": 0,
        }

        # ⚠️ Bug fix: TAIFEX 自營商有兩行（自行買賣 + 避險），原本 if/elif 會互相覆蓋
        # 改用累加器，確保兩行都加入 dealer_net_oi
        dealer_acc = 0
        has_foreign = has_trust = has_dealer = False

        # 找到所有表格的 tr
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 12:
                    continue

                name = tds[0].get_text(strip=True)
                vals = [td.get_text(strip=True) for td in tds]

                # 多空淨額-未平倉口數 = index 11（固定位置）
                net_oi = _safe_int(vals[11]) if len(vals) > 11 else 0
                net_trade = _safe_int(vals[5]) if len(vals) > 5 else 0

                # 驗證：口數通常在 -500,000 ~ +500,000 之間
                if abs(net_oi) > 1_000_000:
                    print(f"  [futures] ⚠ {name} net_oi={net_oi} 異常大，改用 net_trade={net_trade}")
                    net_oi = net_trade

                if "外資" in name and "外資及陸資" in name:
                    # 只取「外資及陸資」主行，排除子分類
                    if not has_foreign:
                        result["foreign_net_oi"] = net_oi
                        has_foreign = True
                elif "外資" in name and not has_foreign:
                    result["foreign_net_oi"] += net_oi
                    has_foreign = True
                elif "投信" in name:
                    result["trust_net_oi"] = net_oi
                    has_trust = True
                elif "自營商" in name:
                    # 自營商有兩子行（自行買賣 + 避險），需累加
                    dealer_acc += net_oi
                    has_dealer = True

        result["dealer_net_oi"] = dealer_acc

        if has_foreign or has_trust or has_dealer:
            return result

    except ImportError:
        print("  [futures] BeautifulSoup not available")
    except Exception as e:
        print(f"  [futures] 解析失敗: {e}")

    return None


def fetch_futures_history(days: int = 6) -> list:
    """抓取近 N 天的期貨三大法人"""
    results = []
    today = datetime.now(TZ)

    for delta in range(days * 2 + 5):
        if len(results) >= days:
            break
        d = today - timedelta(days=delta)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        print(f"  [futures] 抓取 {date_str}...")
        data = fetch_futures_institutional(date_str)
        if data:
            results.append(data)
        time.sleep(1.0 + random.uniform(0, 0.5))

    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. 千張大戶持股比例（TDCC 集保中心，針對個股）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_tdcc_holders(stock_id: str) -> Optional[dict]:
    """
    查詢集保中心個股持股分級（千張以上大戶比例）
    使用 TDCC 公開查詢 API
    """
    try:
        url = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
        # TDCC 使用 POST + form data
        form_data = {
            "REQ_OPR": "SELECT",
            "clession": "",
            "SqlMethod": "StockNo",
            "StockNo": stock_id,
            "scaDates": "",   # 空=最新
            "scaDate": "",
            "clession1": "",
            "Session2": "",
        }

        r = requests.post(url, data=form_data, headers={
            **HEADERS,
            "Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
        }, timeout=15)
        r.encoding = "utf-8"

        if r.status_code != 200:
            return None

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(r.text, "html.parser")

        result = {
            "stock_id": stock_id,
            "total_holders": 0,
            "holders_1000_plus": 0,       # 千張以上人數
            "pct_1000_plus": 0.0,         # 千張以上持股比例
            "holders_400_999": 0,         # 400-999 張
            "pct_400_999": 0.0,
        }

        tables = soup.find_all("table")
        for table in tables:
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 5:
                    continue
                level = tds[0].get_text(strip=True)

                # 找「1,000張以上」的行
                if "1,000" in level or "1000" in level:
                    result["holders_1000_plus"] = _safe_int(tds[1].get_text(strip=True))
                    # 比例通常在最後一欄
                    pct_text = tds[-1].get_text(strip=True).replace("%", "")
                    result["pct_1000_plus"] = _safe_float(pct_text)

                elif ("400" in level and "999" in level) or "400" in level:
                    result["holders_400_999"] = _safe_int(tds[1].get_text(strip=True))
                    pct_text = tds[-1].get_text(strip=True).replace("%", "")
                    result["pct_400_999"] = _safe_float(pct_text)

                elif "合計" in level:
                    result["total_holders"] = _safe_int(tds[1].get_text(strip=True))

        if result["pct_1000_plus"] > 0:
            return result

    except Exception as e:
        print(f"  [tdcc] {stock_id} 查詢失敗: {e}")

    return None


def fetch_tdcc_for_stocks(stock_ids: list, max_stocks: int = 10) -> list:
    """批次查詢千張大戶（限制查詢數避免被封）"""
    results = []
    for sid in stock_ids[:max_stocks]:
        print(f"  [tdcc] 查詢 {sid} 千張大戶...")
        data = fetch_tdcc_holders(sid)
        if data:
            results.append(data)
        time.sleep(1.5 + random.uniform(0, 0.5))
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  主函式：一次抓完所有大盤資料
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_all_market_data(top_stock_ids: list = None, history_days: int = 6) -> dict:
    """
    一次抓完所有大盤籌碼資料，回傳結構化 dict 嵌入 summary.json

    Args:
        top_stock_ids: 需要查千張大戶的股票代碼 list（前 N 檔）
        history_days: 歷史天數（含當天，預設 6 = 當天+前5天）

    Returns:
        {
            "institutional": [...],   # 三大法人近 N 天
            "taiex": [...],           # 大盤指數+量近 N 天
            "margin": [...],          # 融資融券近 N 天
            "futures": [...],         # 期貨三大法人近 N 天
            "tdcc": [...],            # 千張大戶（個股）
            "fetch_errors": [...]     # 抓取失敗記錄
        }
    """
    print("=" * 50)
    print("[market_data] 開始抓取大盤籌碼資料")
    print(f"  history_days={history_days}, top_stocks={len(top_stock_ids or [])}")
    print("=" * 50)

    result = {
        "institutional": [],
        "taiex": [],
        "margin": [],
        "futures": [],
        "tdcc": [],
        "fetch_errors": [],
    }

    # 1. 三大法人
    try:
        print("\n[1/5] 三大法人買賣超...")
        result["institutional"] = fetch_institutional_history(history_days)
        print(f"  ✓ 取得 {len(result['institutional'])} 天")
    except Exception as e:
        err = f"三大法人抓取失敗: {e}"
        print(f"  ✗ {err}")
        result["fetch_errors"].append(err)

    # 2. 大盤指數 + 成交量
    try:
        print("\n[2/5] 大盤指數 + 成交量...")
        result["taiex"] = fetch_taiex_daily(history_days)
        print(f"  ✓ 取得 {len(result['taiex'])} 天")
    except Exception as e:
        err = f"大盤指數抓取失敗: {e}"
        print(f"  ✗ {err}")
        result["fetch_errors"].append(err)

    # 3. 融資融券
    try:
        print("\n[3/5] 融資融券...")
        result["margin"] = fetch_margin_history(history_days)
        print(f"  ✓ 取得 {len(result['margin'])} 天")
    except Exception as e:
        err = f"融資融券抓取失敗: {e}"
        print(f"  ✗ {err}")
        result["fetch_errors"].append(err)

    # 4. 期貨三大法人
    try:
        print("\n[4/5] 期貨籌碼（三大法人台指期）...")
        result["futures"] = fetch_futures_history(history_days)
        print(f"  ✓ 取得 {len(result['futures'])} 天")
    except Exception as e:
        err = f"期貨籌碼抓取失敗: {e}"
        print(f"  ✗ {err}")
        result["fetch_errors"].append(err)

    # 5. 千張大戶（個股）
    if top_stock_ids:
        try:
            print(f"\n[5/5] 千張大戶持股（{len(top_stock_ids[:10])} 檔）...")
            result["tdcc"] = fetch_tdcc_for_stocks(top_stock_ids, max_stocks=10)
            print(f"  ✓ 取得 {len(result['tdcc'])} 檔")
        except Exception as e:
            err = f"千張大戶抓取失敗: {e}"
            print(f"  ✗ {err}")
            result["fetch_errors"].append(err)
    else:
        print("\n[5/5] 千張大戶：無 top_stock_ids，跳過")

    total_errors = len(result["fetch_errors"])
    print(f"\n[market_data] 完成，錯誤 {total_errors} 筆")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  格式化（供 prompt 使用）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fmt(n: int) -> str:
    """格式化數字（帶千分位, 億元單位轉換）"""
    if abs(n) >= 1e8:
        return f"{n/1e8:.1f}億"
    elif abs(n) >= 1e4:
        return f"{n/1e4:.1f}萬"
    return f"{n:,}"


def format_market_context_for_prompt(market_data: dict) -> str:
    """
    將 market_data 格式化成文字，塞進 AI prompt
    """
    lines = []

    # ── 三大法人 ──
    inst = market_data.get("institutional") or []
    if inst:
        lines.append("【三大法人買賣超（近日，元）】")
        for d in inst[:6]:
            date = d["date"]
            fg = d["foreign"]["net"]
            tr = d["trust"]["net"]
            dl = d["dealer"]["net"]
            total = d["total_net"]
            lines.append(
                f"  {date}｜外資 {_fmt(fg)}｜投信 {_fmt(tr)}｜自營 {_fmt(dl)}｜合計 {_fmt(total)}"
            )
        lines.append("")

    # ── 大盤指數 + 成交量 ──
    taiex = market_data.get("taiex") or []
    if taiex:
        lines.append("【大盤指數＋成交金額（近日）】")
        for d in taiex[:6]:
            date = d["date"]
            amt = d.get("amount_billion", 0)
            close = d.get("close", 0)
            chg = d.get("change", 0)
            sign = "+" if chg > 0 else ""
            lines.append(
                f"  {date}｜收盤 {close}｜漲跌 {sign}{chg}｜成交金額 {amt:.0f}億"
            )

        # 量能比較
        if len(taiex) >= 2:
            today_amt = taiex[0].get("amount_billion", 0)
            avg5 = sum(d.get("amount_billion", 0) for d in taiex[1:6]) / max(len(taiex[1:6]), 1)
            if avg5 > 0:
                ratio = today_amt / avg5
                if ratio > 1.2:
                    lines.append(f"  ★ 今日量能 {today_amt:.0f}億，為前5日均量 {avg5:.0f}億 的 {ratio:.1f}倍（放量）")
                elif ratio < 0.8:
                    lines.append(f"  ★ 今日量能 {today_amt:.0f}億，為前5日均量 {avg5:.0f}億 的 {ratio:.1f}倍（縮量）")
                else:
                    lines.append(f"  ★ 今日量能 {today_amt:.0f}億，與前5日均量 {avg5:.0f}億 持平")
        lines.append("")

    # ── 融資融券 ──
    margin = market_data.get("margin") or []
    if margin:
        lines.append("【融資融券變化（近日，張）】")
        for d in margin[:6]:
            date = d["date"]
            mc = d.get("margin_change", 0)
            mb = d.get("margin_balance", 0)
            sc = d.get("short_change", 0)
            sb = d.get("short_balance", 0)
            lines.append(
                f"  {date}｜融資增減 {_fmt(mc)}｜融資餘額 {_fmt(mb)}｜融券增減 {_fmt(sc)}｜融券餘額 {_fmt(sb)}"
            )
        lines.append("")

    # ── 期貨籌碼 ──
    futures = market_data.get("futures") or []
    if futures:
        lines.append("【期貨三大法人台指期淨部位（近日，口）】")
        for d in futures[:6]:
            date = d["date"]
            fg = d.get("foreign_net_oi", 0)
            tr = d.get("trust_net_oi", 0)
            dl = d.get("dealer_net_oi", 0)
            lines.append(
                f"  {date}｜外資 {_fmt(fg)}｜投信 {_fmt(tr)}｜自營 {_fmt(dl)}"
            )
        lines.append("")

    # ── 千張大戶 ──
    tdcc = market_data.get("tdcc") or []
    if tdcc:
        lines.append("【千張大戶持股比例（觀察清單個股）】")
        for d in tdcc:
            sid = d["stock_id"]
            pct = d.get("pct_1000_plus", 0)
            cnt = d.get("holders_1000_plus", 0)
            lines.append(f"  {sid}｜千張以上: {cnt}人, 持股 {pct:.1f}%")
        lines.append("")

    if not lines:
        lines.append("【大盤籌碼資料】本次未能取得（可能為非交易日或 API 異常）")

    return "\n".join(lines)


if __name__ == "__main__":
    """獨立測試用"""
    data = fetch_all_market_data(
        top_stock_ids=["2330", "2344", "2303"],
        history_days=6,
    )
    print("\n" + "=" * 50)
    print(format_market_context_for_prompt(data))
    print("=" * 50)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
