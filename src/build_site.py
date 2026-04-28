#!/usr/bin/env python3
# src/build_site.py
# ─────────────────────────────────────────────────────────────────────────────
# 流程：
#   1. 清空並重建 site/ 目錄
#   2. 把 output/summary.json 複製為 site/data.json（前端 fetch 用）
#   3. 把 web/ 底下所有檔案複製到 site/（index.html、style.css 等）
#   4. 產生 site/404.html（GitHub Pages SPA fallback，redirect 回首頁）
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
import shutil
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

SITE_DIR    = "site"
WEB_DIR     = "web"
SUMMARY_SRC = os.path.join("output", "summary.json")
DATA_DST    = os.path.join(SITE_DIR, "data.json")


def build():
    # ── 1. 清空並重建 site/ ─────────────────────────
    if os.path.exists(SITE_DIR):
        shutil.rmtree(SITE_DIR)
    os.makedirs(SITE_DIR, exist_ok=True)
    print(f"[build_site] {SITE_DIR}/ 目錄已清空並重建")

    # ── 2. 讀取 summary.json ───────────────────────
    if not os.path.exists(SUMMARY_SRC):
        print(f"[build_site] ⚠ {SUMMARY_SRC} 不存在，產生 fallback data.json")
        summary = {
            "generated_at": datetime.now(TZ).isoformat(),
            "timezone":     "Asia/Taipei",
            "success":      False,
            "errors":       ["summary.json 不存在"],
            "days":         5,
            "total_rows":   0,
            "brokers_total": 0,
            "brokers_ok":    0,
            "brokers_fail":  0,
            "top_preview":   [],
            "ai_analysis":   "",
            "market_data":   {},
        }
    else:
        with open(SUMMARY_SRC, "r", encoding="utf-8") as f:
            summary = json.load(f)
        ai_chars = len((summary.get("ai_analysis") or ""))
        print(f"[build_site] summary.json 載入成功："
              f"rows={summary.get('total_rows', 0)}, "
              f"brokers={len(summary.get('top_preview') or [])}, "
              f"ai_chars={ai_chars}")

    # ── 3. 寫出 site/data.json ─────────────────────
    with open(DATA_DST, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[build_site] ✓ {DATA_DST} ({os.path.getsize(DATA_DST):,} bytes)")

    # ── 4. 複製 web/ 全部檔案到 site/ ───────────────
    if os.path.isdir(WEB_DIR):
        for fname in sorted(os.listdir(WEB_DIR)):
            src = os.path.join(WEB_DIR, fname)
            dst = os.path.join(SITE_DIR, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                print(f"[build_site] ✓ {dst} ({os.path.getsize(dst):,} bytes)")
            elif os.path.isdir(src):
                shutil.copytree(src, dst)
                print(f"[build_site] ✓ {dst}/ (目錄)")
    else:
        print(f"[build_site] ⚠ {WEB_DIR}/ 目錄不存在")
        print(f"[build_site]    請依說明把 index.html 與 style.css 放到 {WEB_DIR}/")

    # ── 5. 產生 site/404.html（fallback redirect） ─
    with open(os.path.join(SITE_DIR, "404.html"), "w", encoding="utf-8") as f:
        f.write(
            "<!DOCTYPE html>\n<html lang=\"zh-TW\">\n<head>\n"
            "  <meta charset=\"UTF-8\">\n"
            "  <meta http-equiv=\"refresh\" content=\"0; url=./\">\n"
            "  <title>重新導向中…</title>\n</head>\n"
            "<body>\n  <script>window.location.replace('./');</script>\n"
            "</body>\n</html>\n"
        )
    print(f"[build_site] ✓ {SITE_DIR}/404.html")

    # ── 6. 統計 ────────────────────────────────────
    files = [f for f in os.listdir(SITE_DIR)
             if os.path.isfile(os.path.join(SITE_DIR, f))]
    total = sum(os.path.getsize(os.path.join(SITE_DIR, f)) for f in files)
    print(f"[build_site] 完成！{SITE_DIR}/ 共 {len(files)} 個檔案，"
          f"總大小 {total/1024:.1f} KB")


if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print(f"[build_site] ✗ 失敗: {e}")
        sys.exit(1)
