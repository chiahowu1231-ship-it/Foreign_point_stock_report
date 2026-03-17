import os
import json
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SUMMARY_PATH = os.path.join("output", "summary.json")
AI_TXT_PATH = os.path.join("output", "ai_analysis.txt")

# 你選 B：標準版，建議 1200~1600；你指定要驗證 1400 是否生效
DEFAULT_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "1400"))
DEFAULT_TEMPERATURE = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))


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


def embed_into_summary(summary: dict, analysis_text: str):
    summary["ai_provider"] = "gemini"
    summary["ai_model"] = GEMINI_MODEL
    summary["ai_generated_at"] = datetime.now(TZ).isoformat()
    summary["ai_analysis"] = (analysis_text or "").strip()


def build_prompt(summary: dict) -> str:
    """
    交易導向 prompt（B：標準版 30~60 行，且加硬性規則）
    - 純文字、手機好讀、不用表格
    - 僅使用 summary/top_preview 資料，不得編造新聞/題材/財報
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
    lines.append("目標長度：30～60行（精準、可執行，不冗長）。")
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

    # 輸出框架
    lines.append("請依照以下輸出結構（每區塊中間空一行）：")
    lines.append("A) 今日交易結論（3~5點）：用『因此/所以』描述，給出優先順序。")
    lines.append("B) 外資力量排行榜：列出『總淨超Top3外資』，各給 2 點（偏多/偏短/偏觀察）。")
    lines.append("C) 明日觀察清單（5檔）：每檔必含三行：")
    lines.append("   1) 進場條件（文字描述：突破/回測/不跌破均價等；不要捏造K線數值）")
    lines.append("   2) 停損邏輯（用均價/乖離/淨超集中度推導）")
    lines.append("   3) 了結邏輯（乖離擴大、現價遠離均價、淨超集中反轉等）")
    lines.append("D) 風控提醒（至少3點）。")
    lines.append("E) 一句話摘要（≤25字，適合手機通知預覽）。")
    lines.append("")

    # ===== 你要求的硬性規則（關鍵）=====
    lines.append("硬性規則：")
    lines.append("- 全文至少 30 行（含標題與編號行）。")
    lines.append("- A / B / D 各至少 3 點（用 1) 2) 3)）。")
    lines.append("- C 必須 5 檔，每檔至少 3 行（進場/停損/了結）。")
    lines.append("- 若內容不足 30 行或缺任一段落，請自行補齊直到符合規則。")

    return "\n".join(lines)


def build_fixup_prompt(draft: str) -> str:
    """
    若第一次回覆不達標：用「補齊模式」要求模型只補不足部分，避免重寫浪費 token。
    """
    return "\n".join([
        "你是資深台股交易員與籌碼分析師。以下是一份草稿，請你『只補齊不足』，不要重寫全部。",
        "目標：輸出完整 A/B/C/D/E，並符合硬性規則：全文至少30行；A/B/D 各至少3點；C 必須5檔且每檔3行（進場/停損/了結）。",
        "請直接輸出『完整版本』，保持純文字、段落間空一行、每點用 1) 2) 3)。",
        "",
        "【草稿開始】",
        draft.strip(),
        "【草稿結束】",
    ])


def call_gemini(prompt: str) -> str:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing (check GitHub Secrets).")

    headers = {
        "x-goog-api-key": api_key,   # Gemini API 官方 header 
        "Content-Type": "application/json",
    }

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": DEFAULT_TEMPERATURE,
            "maxOutputTokens": DEFAULT_MAX_OUTPUT_TOKENS,
        },
    }

    r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()

    # ✅ 串接所有 parts，避免只拿到第一段 
    candidates = data.get("candidates") or []
    if not candidates:
        return json.dumps(data, ensure_ascii=False)

    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return text if text else json.dumps(data, ensure_ascii=False)


def validate_analysis(text: str) -> list:
    """
    回傳問題清單（空 list 表示通過）
    """
    problems = []
    s = (text or "").strip()
    if not s:
        return ["empty"]

    # 行數（含空行也算行；你要求至少 30 行）
    line_count = len(s.splitlines())
    if line_count < 30:
        problems.append(f"line_count<{30} (got {line_count})")

    # 必須包含 A~E 標記
    for key in ["A)", "B)", "C)", "D)", "E)"]:
        if key not in s:
            problems.append(f"missing {key}")

    # A/B/D 至少各 3 點（用 1) 2) 3)）
    def count_numbered_points(section_prefix: str) -> int:
        # 擷取從該段落開始到下一段落（A->B, B->C, C->D, D->E）
        idx = s.find(section_prefix)
        if idx < 0:
            return 0
        end = len(s)
        for nxt in ["B)", "C)", "D)", "E)"]:
            if nxt == section_prefix:
                continue
            j = s.find(nxt, idx + 2)
            if j > 0:
                end = min(end, j)
        block = s[idx:end]
        return len(re.findall(r"(?m)^\s*\d\)\s+", block))

    for sec in ["A)", "B)", "D)"]:
        if count_numbered_points(sec) < 3:
            problems.append(f"{sec} points<3")

    # C 必須 5 檔：用簡單規則抓 1)~5) 或 ①②③④⑤ 不強制你用哪個，但至少偵測 5 項
    c_idx = s.find("C)")
    if c_idx >= 0:
        d_idx = s.find("D)", c_idx)
        c_block = s[c_idx:(d_idx if d_idx > 0 else len(s))]
        # 允許兩種：1)~5) 或 1.~5. 或 - 形式但 5 檔不易判；我們先以 numbered 為主
        c_items = len(re.findall(r"(?m)^\s*\d\)\s+", c_block))
        if c_items < 5:
            # 若不是用 numbered，可能用「個股：」行，我們再抓代碼模式（四碼或0050）
            code_items = len(re.findall(r"(?m)^\s*(?:\d{4}|0\d{3})\b", c_block))
            if max(c_items, code_items) < 5:
                problems.append("C items<5")

    return problems


def main():
    summary = load_summary()
    prompt = build_prompt(summary)

    # ✅ 打印實際生效的 token 設定，幫你確認 1400 是否生效
    print(f"[INFO] GEMINI_MODEL={GEMINI_MODEL}")
    print(f"[INFO] GEMINI_MAX_OUTPUT_TOKENS={DEFAULT_MAX_OUTPUT_TOKENS}")
    print(f"[INFO] GEMINI_TEMPERATURE={DEFAULT_TEMPERATURE}")

    try:
        draft = call_gemini(prompt)
        problems = validate_analysis(draft)

        # 若不達標：自動補齊一次（第二輪）
        if problems:
            print("[WARN] First pass not enough:", "; ".join(problems))
            fix_prompt = build_fixup_prompt(draft)
            final_text = call_gemini(fix_prompt)
            problems2 = validate_analysis(final_text)

            if problems2:
                print("[WARN] Second pass still not enough:", "; ".join(problems2))
                # 即使仍不達標，也照樣寫入（避免 mailer 顯示空白）
                analysis = final_text
            else:
                analysis = final_text
        else:
            analysis = draft

        embed_into_summary(summary, analysis)
        save_ai_text(summary["ai_analysis"])
        save_summary(summary)
        print("[OK] Gemini analysis saved: output/ai_analysis.txt and embedded into output/summary.json")

    except Exception as e:
        err_text = f"Gemini 分析失敗：{type(e).__name__}: {e}"
        embed_into_summary(summary, err_text)
        save_ai_text(err_text)
        save_summary(summary)
        print("[WARN]", err_text)
        return


if __name__ == "__main__":
    main()
