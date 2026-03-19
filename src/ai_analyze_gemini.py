# src/ai_analyze_gemini.py
# Gemini analysis runner v8 — model fallback + prompt compression
# ----------------------------------------------------------------
# 修正重點：
#   1. 預設模型改為 gemini-2.5-flash（2.0 系列已於 3/3 退役）
#   2. 429 Quota Exceeded 時自動 fallback 到下一個模型（不再無效重試）
#   3. Prompt 精簡：只送 Top 5 外資 × Top 5 檔（減少 60% token 消耗）
#   4. Fixup pass 改為可選（節省 API 呼叫次數）
#   5. 完整的錯誤分類日誌

import os
import json
import re
import time
import random
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")

# 匯入大盤籌碼格式化工具
try:
    from market_data import format_market_context_for_prompt
    HAS_MARKET_FORMAT = True
except ImportError:
    HAS_MARKET_FORMAT = False

# ── 模型設定 ──────────────────────────────────────
# ⚠️ gemini-2.0-flash / 1.5-flash / 2.0-flash-lite 已於 2026/3/3 退役！
# 目前免費可用：gemini-2.5-flash / 2.5-flash-lite / 2.5-pro
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# 429 時的 Fallback 順序
FALLBACK_MODELS = [
    "gemini-2.5-flash",       # 10 RPM / 250 RPD（免費）
    "gemini-2.5-flash-lite",  # 15 RPM / 1000 RPD（免費，額度最高）
    "gemini-2.5-pro",         # 5 RPM / 100 RPD（免費，最強但限額低）
]

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

SUMMARY_PATH = os.path.join("output", "summary.json")
AI_TXT_PATH = os.path.join("output", "ai_analysis.txt")

TEMP = float(os.getenv("GEMINI_TEMPERATURE", "0.3"))

MAX_OUT_RAW = (os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "") or "").strip()
MAX_OUT = int(MAX_OUT_RAW) if MAX_OUT_RAW.isdigit() else None

# 每個模型的重試次數（針對 5xx / 暫時性錯誤）
RETRIES_PER_MODEL = int(os.getenv("GEMINI_RETRIES", "3"))
BASE_SLEEP = float(os.getenv("GEMINI_RETRY_BASE_SLEEP", "2.0"))

# Fixup pass 開關（設為 0 可節省一次 API 呼叫）
ENABLE_FIXUP = os.getenv("GEMINI_ENABLE_FIXUP", "1").strip() != "0"

# Prompt 精簡設定
PROMPT_TOP_BROKERS = int(os.getenv("PROMPT_TOP_BROKERS", "5"))  # 送幾家外資
PROMPT_TOP_STOCKS = int(os.getenv("PROMPT_TOP_STOCKS", "5"))    # 每家送幾檔

ANALYZER_VERSION = "v10-gemini25-market-context"


# ── 檔案讀寫 ──────────────────────────────────────

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


def embed(summary: dict, text: str, model_used: str = ""):
    summary["ai_provider"] = "gemini"
    summary["ai_model"] = model_used or PRIMARY_MODEL
    summary["ai_generated_at"] = datetime.now(TZ).isoformat()
    summary["ai_analysis"] = (text or "").strip()
    summary["ai_analyzer_version"] = ANALYZER_VERSION
    summary["ai_temperature_used"] = TEMP
    summary["ai_max_output_tokens_used"] = MAX_OUT


# ── Prompt 建構（精簡版） ──────────────────────────

def build_prompt(summary: dict) -> str:
    """
    v9 prompt：外資分點 + 大盤籌碼（三大法人、量能、融資融券、期貨、千張大戶）
    """
    top_preview = summary.get("top_preview") or []
    days = summary.get("days")
    gen_at = summary.get("generated_at")
    total_rows = summary.get("total_rows", 0)
    ok = summary.get("brokers_ok", 0)
    fail = summary.get("brokers_fail", 0)
    errors = summary.get("errors") or []

    lines = []
    lines.append("你是一位資深台股交易員與籌碼分析師。請用繁體中文、純文字、手機好讀格式輸出。")
    lines.append("嚴格規則：只根據我提供的資料分析，不要編造新聞/題材/財報。")
    lines.append("不要表格、不要用空白對齊；段落之間空一行；每點以 1) 2) 3) 編號。")
    lines.append("")
    lines.append(f"報表資訊：產生時間={gen_at}；近{days}日；資料筆數={total_rows}；券商OK={ok}；FAIL={fail}")
    if errors:
        lines.append("注意：若 FAIL>0，只能就『有資料的外資』下結論；缺資料者不得推論。")
    lines.append("")

    # ── 大盤籌碼資料（新增）──
    market_data = summary.get("market_data") or {}
    if HAS_MARKET_FORMAT and market_data:
        market_text = format_market_context_for_prompt(market_data)
        if market_text.strip():
            lines.append("=" * 40)
            lines.append("以下是大盤籌碼資料（用於判斷多空環境、量能趨勢、法人動向）：")
            lines.append(market_text)
            lines.append("=" * 40)
            lines.append("")
    elif market_data:
        # 沒有 format function，手動組裝簡要版
        inst = market_data.get("institutional") or []
        if inst and len(inst) > 0:
            today_inst = inst[0]
            lines.append("【今日三大法人】")
            lines.append(f"  外資淨買超: {today_inst['foreign']['net']:,} 元")
            lines.append(f"  投信淨買超: {today_inst['trust']['net']:,} 元")
            lines.append(f"  自營商淨買超: {today_inst['dealer']['net']:,} 元")
            lines.append("")

    # ── 外資分點明細 ──
    brokers_to_send = top_preview[:PROMPT_TOP_BROKERS]
    lines.append(f"外資明細（依外資總淨超排序，前{len(brokers_to_send)}家，每家Top{PROMPT_TOP_STOCKS}）：")
    for block in brokers_to_send:
        lines.append(f"- {block.get('broker','')}｜總淨超 {block.get('total_net',0)} 張")
        for r in (block.get("rows") or [])[:PROMPT_TOP_STOCKS]:
            lines.append(
                f"  * {r.get('sid','')} {r.get('name','')}｜淨超 {r.get('net',0)}｜均價 {r.get('avg','')}｜現價 {r.get('price','')}｜乖離 {r.get('bias','')}"
            )
    lines.append("")

    # ── 千張大戶（如果有）──
    tdcc = market_data.get("tdcc") or []
    if tdcc:
        lines.append("【觀察個股千張大戶持股比例】")
        for d in tdcc:
            lines.append(f"  {d['stock_id']}｜千張以上: {d.get('holders_1000_plus',0)}人, 持股 {d.get('pct_1000_plus',0):.1f}%")
        lines.append("")

    # ── 輸出結構指令 ──
    lines.append("請依照以下輸出結構（每區塊中間空一行）：")
    lines.append("")
    lines.append("A) 大盤環境判斷（3~5點）：")
    lines.append("   - 根據三大法人買賣超趨勢（近5日方向）、成交量變化（放量/縮量/持平）、")
    lines.append("     融資融券增減、期貨淨部位，判斷目前多空氛圍。")
    lines.append("   - 與前5天比較：量能是放大還是萎縮？法人連買還是轉賣？")
    lines.append("")
    lines.append("B) 外資力量排行榜：列出『總淨超Top3外資』，各至少 3 點（偏多/偏短/偏觀察）。")
    lines.append("")
    lines.append("C) 明日觀察清單（5檔）：每檔必含三行：")
    lines.append("   1) 進場條件（文字描述：突破/回測/不跌破均價等；不要捏造K線數值）")
    lines.append("   2) 停損邏輯（用均價/乖離/淨超集中度推導）")
    lines.append("   3) 了結邏輯（乖離擴大、現價遠離均價、淨超集中反轉等）")
    lines.append("   - 若有千張大戶資料，請一併考慮大戶持股集中度對操作的影響。")
    lines.append("")
    lines.append("D) 風控提醒（至少3點）：")
    lines.append("   - 結合大盤量能、法人動向、融資水位給出具體風控建議。")
    lines.append("")
    lines.append("E) 一句話摘要（≤25字）。")
    lines.append("")
    lines.append("硬性規則：")
    lines.append("- 全文至少 35 行（含標題與編號行）。")
    lines.append("- A / B / D 各至少 3 點（用 1) 2) 3)）。")
    lines.append("- C 必須 5 檔、每檔至少 3 行（進場/停損/了結）。")
    lines.append("- 大盤環境判斷(A)必須引用具體數據（如「外資連3日買超共XX億」「量能較5日均量放大X倍」）。")
    lines.append("- 若不足 35 行或缺任一段落，請自行補齊直到符合規則。")

    return "\n".join(lines)


def fixup_prompt(draft: str) -> str:
    return "\n".join([
        "你是資深台股交易員與籌碼分析師。以下草稿不完整，請你『只補齊不足』並輸出完整 A~E。",
        "硬性規則：全文至少30行；A/B/D 各至少3點；C 必須5檔且每檔3行（進場/停損/了結）。",
        "請直接輸出『完整版本』，保持純文字、段落間空一行、每點用 1) 2) 3)。",
        "",
        "【草稿開始】",
        (draft or "").strip(),
        "【草稿結束】",
    ])


# ── API 呼叫（支援模型 Fallback） ──────────────────

def is_quota_exceeded(status_code: int, body_text: str) -> bool:
    """區分『配額耗盡(quota exceeded)』vs『暫時限速(rate limit)』"""
    if status_code != 429:
        return False
    # Google API 在 quota exceeded 時 message 會包含 "quota"
    return "quota" in body_text.lower() or "billing" in body_text.lower()


def call_gemini_single(prompt: str, model: str) -> str:
    """
    對單一模型呼叫 Gemini API，支援 5xx 重試。
    若遇到 429 Quota Exceeded，直接拋出不重試（交給外層 fallback）。
    """
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY missing (check GitHub Secrets).")

    endpoint = f"{API_BASE}/{model}:generateContent"
    headers = {
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

    last_exc = None

    for attempt in range(RETRIES_PER_MODEL):
        try:
            r = requests.post(endpoint, headers=headers, json=payload, timeout=180)
            body = r.text[:500]

            # ── 429 處理 ──
            if r.status_code == 429:
                if is_quota_exceeded(r.status_code, r.text):
                    # 配額耗盡 → 直接拋出，讓外層 fallback 到其他模型
                    raise QuotaExceededError(
                        f"[{model}] 配額耗盡 (429 Quota Exceeded): {body}"
                    )
                else:
                    # 暫時限速 → 重試
                    last_exc = RuntimeError(f"[{model}] HTTP 429 Rate Limit: {body}")
                    sleep_s = BASE_SLEEP * (2 ** attempt) + random.uniform(0, 1.0)
                    print(f"[WARN] {model} rate-limited, retry in {sleep_s:.1f}s ({attempt+1}/{RETRIES_PER_MODEL})")
                    time.sleep(sleep_s)
                    continue

            # ── 5xx 重試 ──
            if r.status_code in (500, 502, 503, 504):
                last_exc = RuntimeError(f"[{model}] HTTP {r.status_code}: {body}")
                sleep_s = BASE_SLEEP * (2 ** attempt) + random.uniform(0, 1.0)
                print(f"[WARN] {model} server error, retry in {sleep_s:.1f}s ({attempt+1}/{RETRIES_PER_MODEL})")
                time.sleep(sleep_s)
                continue

            # ── 其他 HTTP 錯誤 ──
            r.raise_for_status()

            # ── 解析回應 ──
            data = r.json()
            cands = data.get("candidates") or []
            if not cands:
                return json.dumps(data, ensure_ascii=False)

            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            return text if text else json.dumps(data, ensure_ascii=False)

        except QuotaExceededError:
            raise  # 不重試，直接往上拋
        except Exception as e:
            if isinstance(e, QuotaExceededError):
                raise
            last_exc = e
            sleep_s = BASE_SLEEP * (2 ** attempt) + random.uniform(0, 1.0)
            print(f"[WARN] {model} exception, retry in {sleep_s:.1f}s: {type(e).__name__}: {e}")
            time.sleep(sleep_s)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"[{model}] 全部 {RETRIES_PER_MODEL} 次重試失敗")


class QuotaExceededError(RuntimeError):
    """429 Quota Exceeded — 需要切換模型，重試無效"""
    pass


def call_gemini_with_fallback(prompt: str) -> tuple[str, str]:
    """
    依序嘗試 PRIMARY_MODEL → FALLBACK_MODELS，直到成功。
    回傳 (response_text, model_used)。
    """
    # 建立嘗試順序：PRIMARY → FALLBACK（去除重複）
    models_to_try = [PRIMARY_MODEL]
    for m in FALLBACK_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    last_err = None
    for model in models_to_try:
        try:
            print(f"[INFO] 嘗試模型: {model}")
            text = call_gemini_single(prompt, model)
            print(f"[OK] {model} 回應成功 ({len(text)} chars)")
            return text, model
        except QuotaExceededError as e:
            print(f"[WARN] {model} 配額耗盡，嘗試下一個模型...")
            last_err = e
            continue
        except Exception as e:
            print(f"[WARN] {model} 失敗: {type(e).__name__}: {e}")
            last_err = e
            continue

    # 全部模型都失敗
    if last_err is not None:
        raise last_err
    raise RuntimeError("所有 Gemini 模型皆失敗")


# ── 驗證 ──────────────────────────────────────────

def validate(text: str) -> list:
    """驗證輸出是否符合格式要求。回傳問題清單，空 list = 通過。"""
    s = (text or "").strip()
    problems = []
    if not s:
        return ["empty"]

    if len(s.splitlines()) < 35:
        problems.append("line_count<35")

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


# ── 主程式 ──────────────────────────────────────────

def main():
    summary = load_summary()
    prompt = build_prompt(summary)

    print("=" * 60)
    print(f"[INFO] analyzer     = {ANALYZER_VERSION}")
    print(f"[INFO] primary_model= {PRIMARY_MODEL}")
    print(f"[INFO] fallbacks    = {FALLBACK_MODELS}")
    print(f"[INFO] temperature  = {TEMP}")
    print(f"[INFO] maxOutTokens = {'NONE' if MAX_OUT is None else MAX_OUT}")
    print(f"[INFO] prompt_len   = {len(prompt)} chars")
    print(f"[INFO] enable_fixup = {ENABLE_FIXUP}")
    print(f"[INFO] prompt_brokers={PROMPT_TOP_BROKERS} stocks={PROMPT_TOP_STOCKS}")
    print("=" * 60)

    model_used = PRIMARY_MODEL

    try:
        # ── 第一次呼叫 ──
        draft, model_used = call_gemini_with_fallback(prompt)
        p1 = validate(draft)

        if p1 and ENABLE_FIXUP:
            print(f"[WARN] 第一次驗證未通過: {'; '.join(p1)}")
            print("[INFO] 執行 fixup pass...")
            try:
                final_text, model_used = call_gemini_with_fallback(fixup_prompt(draft))
                p2 = validate(final_text)
                if p2:
                    print(f"[WARN] fixup 後仍未完全通過: {'; '.join(p2)}（仍使用此結果）")
                analysis = final_text
            except Exception as fixup_err:
                print(f"[WARN] fixup 失敗: {fixup_err}（使用第一次結果）")
                analysis = draft
        elif p1:
            print(f"[WARN] 驗證未通過但 fixup 已停用: {'; '.join(p1)}")
            analysis = draft
        else:
            print("[OK] 第一次驗證通過，無需 fixup")
            analysis = draft

        embed(summary, analysis, model_used)
        save_ai_text(summary["ai_analysis"])
        save_summary(summary)
        print(f"[OK] AI 分析完成 (model={model_used}, lines={len(analysis.splitlines())})")

    except Exception as e:
        err = f"Gemini 分析失敗：{type(e).__name__}: {e}"
        embed(summary, err, model_used)
        save_ai_text(err)
        save_summary(summary)
        print(f"[ERROR] {err}")

        # ✅ 印出除錯建議
        if "429" in str(e) or "quota" in str(e).lower():
            print("")
            print("=" * 60)
            print("【429 Quota Exceeded 除錯指南】")
            print("1. 檢查 Google AI Studio 帳戶配額：https://aistudio.google.com/")
            print("2. 免費帳戶的 gemini-2.5-pro 每日限額 100 RPD")
            print("3. 建議使用 gemini-2.5-flash（免費 10 RPM / 250 RPD）")
            print("4. 或啟用 Google Cloud Billing 解除限制")
            print("5. 確認 GEMINI_API_KEY 對應的 Project 有正確啟用 API")
            print("=" * 60)


if __name__ == "__main__":
    main()
