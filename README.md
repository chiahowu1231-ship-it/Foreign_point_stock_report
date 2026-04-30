# TAIWAN 外資分點狙擊分析系統 v11

> 🤖 全自動每日台股籌碼分析 ─ 結合外資分點追蹤、三大法人籌碼、Gemini AI 深度分析，
> 產出 Email + PDF 附件 + GitHub Pages 網頁版三種輸出形式。

## ✨ 主要功能

### 📊 資料抓取
- **外資分點追蹤**：8 家國際外資（小摩、大摩、美林、高盛、花旗、匯豐、瑞銀、麥格理）每日買賣超明細
- **大盤環境速覽**：
  - 大盤指數 ＋ 成交金額（含 5 日均量比較，自動判斷放量/縮量）
  - 三大法人買賣超（含 N 日累計與連買/連賣統計）
  - 期貨三大法人台指期淨未平倉口數（雙層 API：OpenData JSON ＋ HTML 備援）
  - 千張大戶持股比例（針對觀察清單個股）

### 🤖 AI 深度分析（Gemini 2.5 Pro）
五個維度全自動分析：
- **A）大盤籌碼環境研判**：法人共識度、量價配合、多空結論
- **B）外資力量深度剖析**：Top 3 外資操作風格、集中度、跨外資交叉驗證
- **C）明日觀察清單**：含進場條件、停損邏輯、了結邏輯、半凱利部位建議
- **D）風控與資金配置**：持股水位、單一標的上限、系統性風險研判
- **F）外資交叉比對亮點**：多家外資共同進場標的，附均價、現價、乖離率

### 📧 三種輸出
1. **HTML Email** — 專業金融報告風格，全 RWD 響應式設計
2. **PDF 附件** — A4 完整版分析報告（`TAIWAN外資分點狙擊分析報告_YYYYMMDD.pdf`）
3. **GitHub Pages 網頁版** 🆕 — 可隨時在手機/平板/電腦查看，互動式收折段落

---

## 🗂 檔案結構

```
.
├── .github/workflows/
│   └── daily_report.yml          # GitHub Actions 排程 + Pages 部署
├── src/
│   ├── config.py                 # 外資分點清單（8 家外資券商）
│   ├── run_report.py             # 主報表產生器（呼叫 market_data）
│   ├── market_data.py            # 大盤籌碼抓取（雙重 API + dedup 防護）
│   ├── ai_analyze_gemini.py      # Gemini AI 分析（含累計數據 prompt）
│   ├── mailer.py                 # Email + PDF 產生（reportlab）
│   └── build_site.py             # GitHub Pages 靜態網頁建構器 🆕
├── web/                          # 🆕 網頁版樣板
│   ├── index.html                # RWD 首頁（瀏覽器 fetch data.json）
│   └── style.css                 # 深色金融儀表板樣式
├── fonts/
│   └── NotoSansTC-Regular.ttf    # PDF 中文字型
├── output/                       # 執行後產出（不進 git）
│   ├── IKE_Report_*.xlsx
│   ├── IKE_Report_*.pdf
│   ├── TAIWAN外資分點狙擊分析報告_*.pdf
│   ├── summary.json
│   └── debug/*.html
├── site/                         # 🆕 build_site.py 產生（不進 git）
│   ├── index.html
│   ├── style.css
│   ├── data.json                 # 由 summary.json 複製而來
│   └── 404.html
├── requirements.txt
└── README.md
```

---

## ⚙️ 設定

### GitHub Secrets
Repo → **Settings** → **Secrets and variables** → **Actions** → 新增以下：

| Secret | 說明 | 必填 |
|--------|------|:----:|
| `GEMINI_API_KEY` | Google AI Studio API Key | ✅ |
| `SMTP_USER` | Gmail 帳號 | ✅ |
| `SMTP_PASS` | Gmail App Password（16 碼） | ✅ |
| `MAIL_FROM` | 寄件者（通常同 `SMTP_USER`） | ✅ |
| `MAIL_TO` | 收件者 | ✅ |
| `MAIL_BCC` | 密件副本（逗號分隔多人） | ⬜ |

### GitHub Pages 啟用
Repo → **Settings** → **Pages** → **Source** 選 **GitHub Actions**

啟用後，網址：
```
https://<你的帳號>.github.io/<repo名稱>/
```

### 環境變數（在 `daily_report.yml` 內調整）
| 變數 | 預設 | 說明 |
|------|------|------|
| `DAYS` | `5` | 外資分點查詢天數 |
| `TOP_N` | `10` | 每家外資顯示前 N 檔 |
| `MARKET_DATA` | `1` | 大盤籌碼抓取（`0`=停用） |
| `MARKET_HISTORY_DAYS` | `6` | 大盤資料歷史天數 |
| `GEMINI_MODEL` | `gemini-2.5-pro` | AI 模型（額度滿自動 fallback flash → flash-lite） |
| `GEMINI_TEMPERATURE` | `0.3` | AI 創意度（越低越穩定） |
| `PROMPT_TOP_BROKERS` | `7` | Prompt 餵給 AI 的外資數量 |
| `PROMPT_TOP_STOCKS` | `7` | Prompt 餵給 AI 的個股數量 |
| `GEMINI_ENABLE_FIXUP` | `1` | AI 二次修正（`0`=停用省一次 API call） |

---

## 🚀 排程

每週一至五 **台灣時間 18:00** 自動執行（cron `0 10 * * 1-5`）。

執行流程（約 5–10 分鐘）：

```
┌────────────────────────────────────────────────────┐
│  Job 1: report                                     │
│  ├─ 1. 產生 Excel / PDF 報表（外資分點明細）       │
│  ├─ 2. 抓取大盤籌碼（含累計統計）                  │
│  ├─ 3. Gemini AI 五維度分析                        │
│  ├─ 4. 建立 site/ 靜態頁                           │
│  ├─ 5. 寄送 Email（含 PDF 附件 + 網頁版連結）      │
│  └─ 6. 上傳 Pages artifact                         │
│                                                    │
│  Job 2: deploy-pages                               │
│  └─ 部署到 https://xxx.github.io/repo/             │
└────────────────────────────────────────────────────┘
```

---

## 🛠 手動測試

```bash
# 安裝依賴
pip install -r requirements.txt

# 1. 產生外資分點 + 大盤籌碼資料
python src/run_report.py

# 2. AI 分析（需 GEMINI_API_KEY）
GEMINI_API_KEY=xxx python src/ai_analyze_gemini.py

# 3. 寄信（需 SMTP secrets）
SMTP_USER=xxx@gmail.com SMTP_PASS=xxxx python src/mailer.py

# 4. 建立 Pages 站點（本地預覽）
python src/build_site.py
cd site && python -m http.server 8000
# 開啟 http://localhost:8000
```

---

## 🐛 故障排除

### 三大法人資料每日重複
**症狀**：6 天的買賣超數字完全相同
**原因**：TWSE 新版 RWD API 對 `date` 參數寬容過頭，永遠回傳「最新交易日」
**修正**：v11 已採用「舊版 dayDate API 優先 + 新版 title 日期驗證 + dedup 簽章防護」

### 自營商期貨永遠 = 0
**症狀**：期貨表格自營商欄位為 0
**原因**：TAIFEX HTML 表格第一列有 `rowspan="3"` 商品名稱欄，導致欄位 index 偏移
**修正**：改用 TAIFEX OpenData JSON API 為主，HTML 動態欄位定位為備援

### Gemini 429 / 模型 fallback
**狀態**：v11 自動三層 fallback `gemini-2.5-pro → flash → flash-lite`
**檢查**：若 Email/網頁顯示模型是 `flash-lite`，代表 pro 配額已達上限，建議：
- 申請付費帳戶（Pro 配額大幅提升）
- 或在 yml 改 `GEMINI_MODEL: gemini-2.5-flash`（穩定性高於 lite）

### GitHub Pages 部署失敗（404）
**錯誤訊息**：`Get Pages site failed... Not Found`
**原因**：Pages 還沒在 repo settings 啟用
**修正**：
1. Settings → Pages → Source 選 **GitHub Actions**
2. 或在 yml 內 `configure-pages` 步驟加上 `enablement: true`

### Email 顯示「白字白底看不見」
**原因**：Gmail / Outlook 不渲染 `linear-gradient`，導致背景透明 + 白字疊在白底
**修正**：v11 已將所有 gradient 改為純色 + 強制 `TEXTCOLOR` 兜底

---

## 📌 版本歷程

| 版本 | 重點更新 |
|:----:|---------|
| v9 | 加入大盤籌碼抓取、AI prompt 含市場 context |
| v10 | 專業 Email 風格、PDF A4 完整報告、Header/Footer 頁尾頁碼 |
| v11 | GitHub Pages 網頁版、雙重 API 防呆、移除一句話摘要 |

---

## ⚠️ 免責聲明

帶進帶出是違法的行為，此資料皆為網上根據盤後資訊大數據所取得訊息，
非作為或被視為買進或售出標的的邀請或意象，請自行依據取得資訊評估風險與獲利，
**有賺有賠請斟酌**。

本系統僅供研究與學術用途，不構成任何投資建議。
