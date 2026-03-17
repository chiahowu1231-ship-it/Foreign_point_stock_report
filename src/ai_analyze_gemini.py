import os
import json
import re
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

# 你指定：使用 Gemini 3.1 Pro Preview（官方 model code）[1](https://www.datastudios.org/post/gemini-token-limits-and-context-windows)[2](https://futureagi.com/blogs/google-gemini-2-5-pro-2025)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")

# Gemini Developer API generateContent endpoint（官方 API reference）[3](https://deepmind.google/models/model-cards/gemini-3-1-flash-lite/)[4](https://www.datastudios.org/post/google-gemini-context-window-token-limits-model-comparison-and-workflow-strategies-for-late-2025)
ENDPOINT = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

SUMMARY_PATH = os.path.join("output", "summary.json")
AI_TXT_PATH = os.path.join("output", "ai_analysis.txt")

# 你可以在 workflow 注入覆蓋
TEMP = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))

# 建議仍提供 maxOutputTokens（可用 env 控制）
# 若你想「不限制」，把 GEMINI_MAX_OUTPUT_TOKENS 留空即可（腳本會自動不送 maxOutputTokens）
MAX_OUT_RAW = (os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "1400") or "").strip()
MAX_OUT = int(MAX_OUT_RAW) if MAX_OUT_RAW.isdigit() else None

ANALYZER_VERSION = "v5-gemini-3.1-pro-fixup-30lines-AE"


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
    summary["ai_max_output_tokens_used"] = MAX_OUT  # None 代表未送 maxOutputTokens


def build_prompt(summary: dict) -> str:
    """
    交易導向 + 硬性規則（你指定）
    - 全文至少 30 行
    - A/B/D 各至少 3 點
    - C 必須 5 檔、每檔 3 行（進場/停損/了結）
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
    lines.append("不要表格、不要用空白對齊；段落之間空一行；每點以 1) 2) 3) 編號。")
    lines.append("")
    lines.append(f"報表資訊：產生時間={gen_at}；近{days}日；資料筆數={total_rows}；券商OK={ok}；FAIL={fail}")
    if errors:
        lines.append("注意：若 FAIL>0，只能就『有資料的外資』下結論；缺資料者不得推論。")
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
    lines.append("C) 明日觀察清單（5檔）：每檔必含三行：")
    lines.append("   1) 進場條件（文字描述：突破/回測/不跌破均價等；不要捏造K線數值）")
    lines.append("   2) 停損邏輯（用均價/乖離/淨超集中度推導）")
    lines.append("   3) 了結邏輯（乖離擴大、現價遠離均價、淨超集中反轉等）")
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
    """
    補齊 prompt：把草稿貼回去，要求「只補不足」並輸出完整 A~E
    """
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
        # 官方 API reference：使用 x-goog-api-key header 帶 API key [3](https://deepmind.google/models/model-cards/gemini-3-1-flash-lite/)[4](https://www.datastudios.org/post/google-gemini-context-window-token-limits-model-comparison-and-workflow-strategies-for-late-2025)
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    gen_cfg = {"temperature": TEMP}
    if MAX_OUT is not None:
        gen_cfg["maxOutputTokens"] = MAX_OUT

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_cfg,
    }

    r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=180)
    r.raise_for_status()
    data = r.json()

    # 串接所有 parts，避免只取到第一段（你之前短回的典型原因）
    cands = data.get("candidates") or []
    if not cands:
        return json.dumps(data, ensure_ascii=False)

    parts = (cands[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    return text if text else json.dumps(data, ensure_ascii=False)


def validate(text: str) -> list:
    """
    回傳問題清單；空 list 表示通過
    """
    s = (text or "").strip()
    problems = []
    if not s:
        return ["empty"]

    # 行數至少 30
    if len(s.splitlines()) < 30:
        problems.append("line_count<30")

    # 必須包含 A~E
    for key in ["A)", "B)", "C)", "D)", "E)"]:
        if key not in s:
            problems.append(f"missing {key}")

    # A/B/D 各至少 3 點
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

    # C 至少 5 檔（用編號項目判斷）
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
    print(f"[INFO] model={GEMINI_MODEL}")  # gemini-3.1-pro-preview [1](https://www.datastudios.org/post/gemini-token-limits-and-context-windows)[2](https://futureagi.com/blogs/google-gemini-2-5-pro-2025)
    print(f"[INFO] temperature={TEMP}")
    print(f"[INFO] maxOutputTokens={'NONE' if MAX_OUT is None else MAX_OUT}")

    try:
        draft = call_gemini(prompt)
        p1 = validate(draft)

        # 不達標 → 第二輪補齊
        if p1:
            print("[WARN] first pass not enough:", "; ".join(p1))
            final_text = call_gemini(fixup_prompt(draft))
            p2 = validate(final_text)
            if p2:
                print("[WARN] second pass still not enough:", "; ".join(p2))
                analysis = final_text  # 仍寫入，避免 mailer 空白
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
