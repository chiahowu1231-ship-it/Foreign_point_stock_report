import os
import json
import re
import time
import random
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

# ✅ 預設改成 Gemini 2.5 Pro（你要試的模型）
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
SUMMARY_PATH = os.path.join("output", "summary.json")
AI_TXT_PATH = os.path.join("output", "ai_analysis.txt")

TEMP = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))

# ✅ 若你要「不限制輸出」，請不要在 workflow 設 GEMINI_MAX_OUTPUT_TOKENS
# 這裡預設空字串 -> MAX_OUT=None -> 不送 maxOutputTokens
MAX_OUT_RAW = (os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "") or "").strip()
MAX_OUT = int(MAX_OUT_RAW) if MAX_OUT_RAW.isdigit() else None

# 重試參數（避免 429）
RETRIES = int(os.getenv("GEMINI_RETRIES", "5"))
BASE_SLEEP = float(os.getenv("GEMINI_RETRY_BASE_SLEEP", "2.0"))

ANALYZER_VERSION = "v6-gemini-2.5-pro-fixup-30lines-AE"


def load_summary():
    with open(SUMMARY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_summary(summary: dict):
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def save_ai_text(text: str):
    os.makedirs("output", exist_ok=True)
    with open(AI_TXT_PATH, "w", encoding="utf-8") as f:
        f.write((text or "").strip() + "\n")


def embed(summary: dict, text: str):
    summary["ai_provider"] = "gemini"
    summary["ai_model"] = GEMINI_MODEL
    summary["ai_generated_at"] = datetime.now(TZ).isoformat()
    summary["ai_analysis"] = (text or "").strip()
    summary["ai_analyzer_version"] = ANALYZER_VERSION
    summary["ai_temperature_used"] = TEMP
    summary["ai_max_output_tokens_used"] = MAX_OUT


def build_prompt(summary: dict) -> str:
    top_preview = summary.get("top_preview") or []
    days = summary.get("days")
    gen_at = summary.get("generated_at")
    total_rows = summary.get("total_rows", 0)
    ok = summary.get("brokers_ok", 0)
    fail = summary.get("brokers_fail", 0)
    top_n = summary.get("top_n", 10)

    lines = []
    lines.append("你是一位資深台股交易員與籌碼分析師。請用繁體中文、純文字、手機好讀格式輸出。")
    lines.append("嚴格規則：只根據我提供的資料（外資Top清單、淨超、均價、現價、乖離），不要編造新聞/題材/財報。")
    lines.append("不要表格、不要用空白對齊；段落之間空一行；每點以 1) 2) 3) 編號。")
    lines.append("")
    lines.append(f"報表資訊：產生時間={gen_at}；近{days}日；資料筆數={total_rows}；券商OK={ok}；FAIL={fail}")
    lines.append("")

    lines.append(f"外資明細（依外資總淨超排序）Top{top_n}：")
    for block in top_preview:
        lines.append(f"- {block.get('broker','')}｜總淨超 {block.get('total_net',0)} 張")
        for r in (block.get("rows") or [])[:top_n]:
            lines.append(
                f"  * {r.get('sid','')} {r.get('name','')}｜淨超 {r.get('net',0)}｜均價 {r.get('avg','')}｜現價 {r.get('price','')}｜乖離 {r.get('bias','')}"
            )
    lines.append("")

    lines.append("請依照以下輸出結構（每區塊中間空一行）：")
    lines.append("A) 今日交易結論（3~5點）：用『因此/所以』描述，給出優先順序。")
    lines.append("B) 外資力量排行榜：列出『總淨超Top3外資』，各至少 3 點（偏多/偏短/偏觀察）。")
    lines.append("C) 明日觀察清單（5檔）：每檔必含三行：進場條件 / 停損邏輯 / 了結邏輯。")
    lines.append("D) 風控提醒（至少3點）。")
    lines.append("E) 一句話摘要（≤25字）。")
    lines.append("")
    lines.append("硬性規則：")
    lines.append("- 全文至少 30 行（含標題與編號行）。")
    lines.append("- A / B / D 各至少 3 點（用 1) 2) 3)）。")
    lines.append("- C 必須 5 檔、每檔至少 3 行（進場/停損/了結）。")
    lines.append("- 若不足 30 行或缺任一段落，請自行補齊直到符合規則。")

    return "\n".join(lines)


def fixup_prompt(draft: str) -> str:
    return "\n".join([
        "你是資深台股交易員與籌碼分析師。以下草稿不完整，請你『只補齊不足』並輸出完整 A~E。",
        "硬性規則：全文至少30行；A/B/D 各至少3點；C 必須5檔且每檔3行（進場/停損/了結）。",
        "請直接輸出『完整版本』，保持純文字、段落間空一行、每點用 1) 2) 3)。",
        "",
        "【草稿開始】",
        draft.strip(),
        "【草稿結束】",
    ])


def call_gemini(prompt: str) -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing (check GitHub Secrets).")

    headers = {
        "x-goog-api-key": api_key,  # Gemini API 標準 header [4](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite-preview)[5](https://futureagi.com/blogs/google-gemini-2-5-pro-2025)
        "Content-Type": "application/json",
    }

    gen_cfg = {"temperature": TEMP}
    if MAX_OUT is not None:
        gen_cfg["maxOutputTokens"] = MAX_OUT

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_cfg,
    }

    last_exc = None
    for attempt in range(RETRIES):
        try:
            r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=180)

            # 429 / 5xx -> 指數退避
            if r.status_code in (429, 500, 502, 503, 504):
                sleep_s = BASE_SLEEP * (2 ** attempt) + random.uniform(0, 0.8)
                print(f"[WARN] Gemini HTTP {r.status_code} retry in {sleep_s:.1f}s ({attempt+1}/{RETRIES})")
                time.sleep(sleep_s)
                continue

            r.raise_for_status()
            data = r.json()

            cands = data.get("candidates") or []
            if not cands:
                return json.dumps(data, ensure_ascii=False)

            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            return text if text else json.dumps(data, ensure_ascii=False)

        except Exception as e:
            last_exc = e
            sleep_s = BASE_SLEEP * (2 ** attempt) + random.uniform(0, 0.8)
            print(f"[WARN] Gemini exception retry in {sleep_s:.1f}s: {type(e).__name__}: {e}")
            time.sleep(sleep_s)

    raise last_exc


def validate(text: str) -> list:
    s = (text or "").strip()
    problems = []
    if not s:
        return ["empty"]

    if len(s.splitlines()) < 30:
        problems.append("line_count<30")

    for key in ["A)", "B)", "C)", "D)", "E)"]:
        if key not in s:
            problems.append(f"missing {key}")

    def count_points(prefix: str) -> int:
        i = s.find(prefix)
        if i < 0:
            return 0
        end = len(s)
        for nxt in ["B)", "C)", "D)", "E)"]:
            if nxt == prefix:
                continue
            j = s.find(nxt, i + 2)
            if j > 0:
                end = min(end, j)
        block = s[i:end]
        return len(re.findall(r"(?m)^\s*\d\)\s+", block))

    for sec in ["A)", "B)", "D)"]:
        if count_points(sec) < 3:
            problems.append(f"{sec} points<3")

    c_i = s.find("C)")
    if c_i >= 0:
        d_i = s.find("D)", c_i)
        c_block = s[c_i:(d_i if d_i > 0 else len(s))]
        if len(re.findall(r"(?m)^\s*\d\)\s+", c_block)) < 5:
            problems.append("C items<5")

    return problems


def main():
    summary = load_summary()
    prompt = build_prompt(summary)

    print(f"[INFO] analyzer={ANALYZER_VERSION}")
    print(f"[INFO] model={GEMINI_MODEL}")
    print(f"[INFO] temperature={TEMP}")
    print(f"[INFO] maxOutputTokens={'NONE' if MAX_OUT is None else MAX_OUT}")
    print(f"[INFO] retries={RETRIES} base_sleep={BASE_SLEEP}")

    try:
        draft = call_gemini(prompt)
        p1 = validate(draft)

        if p1:
            print("[WARN] first pass not enough:", "; ".join(p1))
            final_text = call_gemini(fixup_prompt(draft))
            p2 = validate(final_text)
            if p2:
                print("[WARN] second pass still not enough:", "; ".join(p2))
                analysis = final_text
            else:
                analysis = final_text
        else:
            analysis = draft

        embed(summary, analysis)
        save_ai_text(summary["ai_analysis"])
        save_summary(summary)
        print("[OK] ai_analysis written to summary.json and ai_analysis.txt")

    except Exception as e:
        err = f"Gemini 分析失敗：{type(e).__name__}: {e}"
        embed(summary, err)
        save_ai_text(err)
        save_summary(summary)
        print("[WARN]", err)


if __name__ == "__main__":
    main()
