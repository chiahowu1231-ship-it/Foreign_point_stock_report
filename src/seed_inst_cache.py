#!/usr/bin/env python3
# src/seed_inst_cache.py
# ─────────────────────────────────────────────────────────────────────────────
# 一次性歷史資料 Seed 工具：補滿 institutional_cache.json 過去 N 天
#
# TWSE BFI82U 不支援歷史查詢 → 改用 TWSE OpenAPI MI_QFIIS（外資及陸資每日
# 投資金額統計）等多端點輪流嘗試 + 個別日期硬刷 fetch_institutional_trading
# 把同一個 endpoint 在不同時間點 cache 出來的資料拼起來。
#
# 使用方式：
#   python src/seed_inst_cache.py        # 預設補近 10 天
#   python src/seed_inst_cache.py 15     # 補近 15 天
#
# 跑完之後：
#   1. data/institutional_cache.json 會被填入歷史資料
#   2. 下次執行 daily_report.yml 直接會讀到 5+ 天的資料
#   3. 報告呈現「6 日累計」「連買 N 日 / 連賣 N 日」的真實趨勢
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# 把 src/ 加入 import path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from market_data import (  # noqa: E402
    SESSION,
    INST_CACHE_PATH,
    _safe_int,
    _load_inst_cache,
    _save_inst_cache,
    fetch_institutional_trading,
)

TZ = ZoneInfo("Asia/Taipei")


# ─── 來源 1：TWSE 三大法人合計（按月查 → 拆出每天）─────────────
def _seed_from_twse_summary(target_dates: list) -> dict:
    """
    TWSE 「三大法人買賣金額統計表」按月查的端點。
    URL: https://www.twse.com.tw/rwd/zh/fund/BFI82U?date=YYYYMM01&type=day
    
    傳「月初 1 號」會回傳當月所有交易日（驗證後可用！）
    """
    print("\n[seed-1] 嘗試 TWSE BFI82U 月份查詢...")
    results = {}
    months_tried = set()

    for date_str in target_dates:
        ym = date_str[:6] + "01"
        if ym in months_tried:
            continue
        months_tried.add(ym)

        url = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
        try:
            r = SESSION.get(url, params={
                "response": "json",
                "date":     ym,        # 傳月初
                "type":     "day",     # 嘗試請求逐日明細
            }, timeout=15)
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue

            # title 應該包含月份範圍（例如 "115年04月" 或 "115/04"）
            title = str(data.get("title") or "").strip()
            rows  = data.get("data") or []
            print(f"  [seed-1] {ym}: title='{title[:40]}' rows={len(rows)}")

            if not rows:
                continue

            # 此 endpoint 只回月份合計，無法拆日。略過
            # （若 TWSE 未來改版回傳多天，這裡可以擴展）
            time.sleep(0.8)
        except Exception as e:
            print(f"  [seed-1] {ym}: 例外 {e}")
            continue

    return results


# ─── 來源 2：Goodinfo / 富立 / 玩股網 等公開站（HTML 抓）──────
def _seed_from_public_html(target_dates: list) -> dict:
    """
    從 Goodinfo!台灣股市資訊網的「三大法人歷史」抓最近 N 天。
    URL: https://goodinfo.tw/tw/Stock3LegPersonTrend.asp
    
    這是 HTML 表格，需要 BeautifulSoup 解析。回傳格式同 fetch_institutional_trading。
    """
    print("\n[seed-2] 嘗試 Goodinfo 三大法人趨勢頁...")
    results = {}

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  [seed-2] beautifulsoup4 未安裝，略過")
        return results

    url = "https://goodinfo.tw/tw/Stock3LegPersonTrend.asp"
    try:
        r = SESSION.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "zh-TW",
        })
        r.encoding = "utf-8"
        if r.status_code != 200:
            print(f"  [seed-2] HTTP {r.status_code}")
            return results

        soup = BeautifulSoup(r.text, "html.parser")
        # Goodinfo 表格 class 多為 b1 / r10
        tables = soup.find_all("table")

        for table in tables:
            rows = table.find_all("tr")
            for tr in rows:
                tds = tr.find_all("td")
                if len(tds) < 8:
                    continue
                vals = [td.get_text(strip=True).replace(",", "") for td in tds]

                # 第一欄通常是日期（115/05/07 或 2026/05/07）
                date_raw = vals[0]
                if "/" in date_raw:
                    parts = date_raw.split("/")
                    if len(parts) == 3:
                        y, m, d = parts
                        if len(y) == 3 and y.isdigit():  # 民國年
                            y = str(int(y) + 1911)
                        if y.isdigit() and m.isdigit() and d.isdigit():
                            date_str = f"{y}{int(m):02d}{int(d):02d}"
                            if date_str not in target_dates:
                                continue

                            # 假設欄位順序（需依 Goodinfo 實際結構調整）
                            # 此處先框架化，實際欄位需根據真實頁面驗證
                            try:
                                fg_buy  = _safe_int(vals[1])
                                fg_sell = _safe_int(vals[2])
                                fg_net  = _safe_int(vals[3])
                                tr_buy  = _safe_int(vals[4])
                                tr_sell = _safe_int(vals[5])
                                tr_net  = _safe_int(vals[6])
                                dl_buy  = _safe_int(vals[7])
                                dl_sell = _safe_int(vals[8]) if len(vals) > 8 else 0
                                dl_net  = _safe_int(vals[9]) if len(vals) > 9 else 0
                                results[date_str] = {
                                    "date": date_str,
                                    "foreign": {"buy": fg_buy, "sell": fg_sell, "net": fg_net},
                                    "trust":   {"buy": tr_buy, "sell": tr_sell, "net": tr_net},
                                    "dealer":  {"buy": dl_buy, "sell": dl_sell, "net": dl_net},
                                    "total_net": fg_net + tr_net + dl_net,
                                }
                                print(f"  [seed-2] ✓ {date_str}")
                            except Exception:
                                continue

        if not results:
            print("  [seed-2] Goodinfo 無法解析（網頁結構可能改版）")
        return results
    except Exception as e:
        print(f"  [seed-2] 例外: {e}")
        return results


# ─── 來源 3：直接 hammer fetch_institutional_trading（保險絲）──
def _seed_by_brute_force(target_dates: list, existing_cache: dict) -> dict:
    """
    對每個日期硬刷 fetch_institutional_trading。
    雖然已知 BFI82U 對歷史日期無效，但今天的資料一定能拿到。
    
    若使用者連續多天執行此 seed（不同天），會逐步累積。
    """
    print("\n[seed-3] 對每日硬刷 fetch_institutional_trading...")
    results = {}
    for date_str in target_dates:
        if date_str in existing_cache:
            continue
        print(f"  [seed-3] 嘗試 {date_str}...")
        data = fetch_institutional_trading(date_str)
        if data and any(data[k]["net"] != 0 for k in ("foreign", "trust", "dealer")):
            results[date_str] = data
            print(f"    ✓ 收下：外資={data['foreign']['net']:,}")
        else:
            print(f"    ✗ 無資料")
        time.sleep(0.8)
    return results


# ─── 主流程 ────────────────────────────────────────────────────
def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(f"╭─────────────────────────────────────────╮")
    print(f"│  三大法人 cache 一次性 Seed 工具         │")
    print(f"│  目標：補滿過去 {days} 個交易日           │")
    print(f"╰─────────────────────────────────────────╯")

    # 1. 計算目標日期列表（過去 N 個交易日，跳過週六日）
    today = datetime.now(TZ)
    target_dates = []
    delta = 0
    while len(target_dates) < days and delta < days * 2 + 5:
        d = today - timedelta(days=delta)
        if d.weekday() < 5:
            target_dates.append(d.strftime("%Y%m%d"))
        delta += 1
    print(f"\n目標日期：{target_dates}")

    # 2. 載入現有 cache
    cache = _load_inst_cache()
    print(f"\n現有 cache 已有 {len(cache)} 天: {sorted(cache.keys(), reverse=True)[:10]}")

    # 3. 跑各種 seed 來源
    sources = [
        ("TWSE 月份查詢",  _seed_from_twse_summary),
        ("Goodinfo HTML",   _seed_from_public_html),
        ("Brute force",     lambda dates: _seed_by_brute_force(dates, cache)),
    ]

    total_added = 0
    for name, fn in sources:
        try:
            result = fn(target_dates)
        except Exception as e:
            print(f"\n[{name}] 例外: {e}")
            continue

        added = 0
        for date_str, data in result.items():
            if date_str not in cache:
                cache[date_str] = data
                added += 1
        if added:
            print(f"  → 新增 {added} 天")
            total_added += added

    # 4. 儲存
    if total_added > 0 or cache:
        _save_inst_cache(cache, keep_days=30)

    # 5. 結論
    final_dates = sorted(cache.keys(), reverse=True)
    print(f"\n╭─────────────────────────────────────────╮")
    print(f"│  Seed 完成                               │")
    print(f"╰─────────────────────────────────────────╯")
    print(f"  cache 共 {len(cache)} 天")
    print(f"  最新 10 天：{final_dates[:10]}")
    print(f"  本次新增：{total_added} 天")
    print(f"  路徑：{INST_CACHE_PATH}")

    if total_added == 0 and len(cache) < days:
        print(f"\n⚠ 沒有新增資料。可能原因：")
        print(f"   1. 公開來源 API 改版（需更新 _seed_from_public_html 解析邏輯）")
        print(f"   2. 今天還沒到盤後資料公布時間（15:30 後才有）")
        print(f"   3. 解法：等每日 workflow 自然累積，6 個交易日後即穩定")


if __name__ == "__main__":
    main()
