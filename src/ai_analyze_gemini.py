import os
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
SUMMARY_PATH = os.path.join("output", "summary.json")
AI_TXT_PATH = os.path.join("output", "ai_analysis.txt")


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


def build_prompt(summary: dict) -> str:
    # 交易導向（你要的版本：純文字、手機好讀、不用表格）
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
    lines.append("不要表格、不用對齊空白；段落間空一行；每點用 1) 2) 3) 編號。")
    lines.append("")
    lines.append(f"報表資訊：產生時間={gen_at}；近{days}日；資料筆數={total_rows}；券商OK={ok}；FAIL={fail}")
    lines.append("")

    if top_preview:
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
    lines.append("B) 外資力量排行榜：列出『總淨超Top3外資』，各給 2 點（偏多/偏短/偏觀察）。")
    lines.append("C) 明日觀察清單（5檔）：每檔必含：進場條件、停損邏輯、了結邏輯（用均價/乖離/淨超集中度推導）。")
    lines.append("D) 風控提醒（3點）。")
    lines.append("E) 一句話摘要（≤25字）。")
    return "\n".join(lines)


def call_gemini(prompt: str) -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing (check GitHub Secrets).")

    headers = {
        "x-goog-api-key": api_key,  # Gemini 官方 header 
        "Content-Type": "application/json",
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 900},
    }

    r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return json.dumps(data, ensure_ascii=False)


def main():
    summary = load_summary()
    prompt = build_prompt(summary)

    try:
        analysis = call_gemini(prompt).strip()
        summary["ai_provider"] = "gemini"
        summary["ai_model"] = GEMINI_MODEL
        summary["ai_generated_at"] = datetime.now(TZ).isoformat()
        summary["ai_analysis"] = analysis

        save_ai_text(analysis)
        save_summary(summary)

        print("[OK] Gemini analysis saved and embedded into output/summary.json")

    except Exception as e:
        # ✅ 就算失敗，也要把錯誤寫回 summary，避免 mailer 顯示「尚未取得」
        err_text = f"Gemini 分析失敗：{type(e).__name__}: {e}"
        summary["ai_provider"] = "gemini"
        summary["ai_model"] = GEMINI_MODEL
        summary["ai_generated_at"] = datetime.now(TZ).isoformat()
        summary["ai_analysis"] = err_text

        save_ai_text(err_text)
        save_summary(summary)

        print("[WARN]", err_text)
        # 讓 workflow 仍可繼續寄信/上傳 artifacts
        # 若你想要此 step 也顯示紅燈，可改成 raise
        return


if __name__ == "__main__":
    main()
