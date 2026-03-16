import os
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

# 你在 workflow 可用 GEMINI_MODEL 覆蓋；預設用 gemini-2.5-flash
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Gemini Developer API 的 generateContent 端點（官方 API 參考）
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SUMMARY_PATH = os.path.join("output", "summary.json")
AI_TXT_PATH = os.path.join("output", "ai_analysis.txt")

# 你選 B（標準版 30~60 行）建議 maxOutputTokens 1200~1600
# 這裡預設 1400，兼顧完整與手機閱讀
DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "1400"))


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
    """
    交易導向 prompt（B：標準版 30~60 行）
    - 純文字、手機好讀、不用表格
    - 嚴格限制：只使用 summary/top_preview 提供的資料，不得編造新聞/題材/財報
    """
    top_preview = summary.get("top_preview") or []
    days = summary.get("days")
    gen_at = summary.get("generated_at")
    total_rows = summary.get("total_rows", 0)
    ok = summary.get("brokers_ok", 0)
    fail = summary.get("brokers_fail", 0)
    top_n = summary.get("top_n", 10)
    errors = summary.get("errors") or []

    lines = []
    lines.append("你是一位資深台股交易員與籌碼分析師。請用繁體中文、純文字、手機好讀格式輸出。")
    lines.append("嚴格規則：只根據我提供的資料（外資Top清單、淨超、均價、現價、乖離），不要編造新聞/題材/財報。")
    lines.append("不要用表格、不要用空白對齊；段落之間空一行；每點以 1) 2) 3) 編號。")
    lines.append(f"目標長度：30～60行（精準、可執行，不冗長）。")
    lines.append("")
    lines.append(f"報表資訊：產生時間={gen_at}；近{days}日；資料筆數={total_rows}；券商OK={ok}；FAIL={fail}")
    if errors:
        lines.append("注意：若有錯誤摘要，只能就『有資料的外資』下結論，缺資料者不得推論。")
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

    # 輸出框架：交易導向、可執行
    lines.append("請依照以下輸出結構（每區塊中間空一行）：")
    lines.append("A) 今日交易結論（3~5點）：用『因此/所以』描述，給出優先順序。")
    lines.append("B) 外資力量排行榜：列出『總淨超Top3外資』，各給 2 點（偏多/偏短/偏觀察）。")
    lines.append("C) 明日觀察清單（5檔）：每檔必含：")
    lines.append("   1) 進場條件（用文字描述：突破/回測/不跌破均價等，不要捏造K線數值）")
    lines.append("   2) 停損邏輯（用均價/乖離/淨超集中度推導）")
    lines.append("   3) 了結邏輯（乖離擴大、現價遠離均價、淨超集中反轉等）")
    lines.append("D) 風控提醒（3點）：追價風險/集中度風險/乖離風險。")
    lines.append("E) 一句話摘要（≤25字，適合手機通知預覽）。")

    return "\n".join(lines)


def call_gemini(prompt: str) -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing (check GitHub Secrets).")

    headers = {
        # Gemini API 官方要求用 x-goog-api-key header 帶 API key 
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": DEFAULT_MAX_OUTPUT_TOKENS,
        },
    }

    r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    # ✅ 串接所有 parts，避免只取到第一句（你之前 ai_analysis 只有一句就是典型症狀）[2](https://ai.google.dev/gemini-api/docs/long-context)[2](https://ai.google.dev/gemini-api/docs/long-context)
    candidates = data.get("candidates") or []
    if not candidates:
        return json.dumps(data, ensure_ascii=False)

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return text if text else json.dumps(data, ensure_ascii=False)


def embed_into_summary(summary: dict, analysis_text: str):
    summary["ai_provider"] = "gemini"
    summary["ai_model"] = GEMINI_MODEL
    summary["ai_generated_at"] = datetime.now(TZ).isoformat()
    summary["ai_analysis"] = (analysis_text or "").strip()


def main():
    summary = load_summary()
    prompt = build_prompt(summary)

    try:
        analysis = call_gemini(prompt)
        embed_into_summary(summary, analysis)
        save_ai_text(summary["ai_analysis"])
        save_summary(summary)
        print("[OK] Gemini analysis saved: output/ai_analysis.txt and embedded into output/summary.json")

    except Exception as e:
        # ✅ 就算失敗，也回填到 summary.json，讓 mailer 一定有內容（顯示失敗原因）[2](https://ai.google.dev/gemini-api/docs/long-context)
        err_text = f"Gemini 分析失敗：{type(e).__name__}: {e}"
        embed_into_summary(summary, err_text)
        save_ai_text(err_text)
        save_summary(summary)
        print("[WARN]", err_text)
        # 不 raise：避免擋住後續寄信/上傳 artifacts（你 workflow 已用 if: always()）
        return


if __name__ == "__main__":
    main()
