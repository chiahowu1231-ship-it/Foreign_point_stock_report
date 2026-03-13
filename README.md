# 外資分點狙擊分析（GitHub Actions）

## 功能
- 週一～週五 **台灣時間 14:30** 自動執行（GitHub Actions cron 以 UTC 計算，已換算為 UTC 06:30）
- 固定抓 5 日區間（可用環境變數 DAYS 覆蓋）
- 產出 Excel/PDF：`output/IKE_Report_YYYYMMDD.xlsx` / `.pdf`
- Excel 內含 `Report` 與 `Failures` 兩個 Sheet；乖離率條件式上色（淺綠/藍/深紅）
- PDF 使用 **方案 B1（嵌入字型）**，避免亂碼
- 產出後 Email 寄給你自己並 BCC 指定人員
- 上傳 artifacts 並 `retention-days=7` 自動清理

## 必做：放入字型檔（方案 B1）
請在 repo 根目錄新增 `fonts/`，並上傳：
- `fonts/NotoSansTC-Regular.ttf`

否則 `src/run_report.py` 會報錯（避免產出亂碼 PDF）。

## GitHub Secrets 設定
Repo → Settings → Secrets and variables → Actions → New repository secret

- SMTP_USER: 你的 Gmail（例如 chiahowu1231@gmail.com）
- SMTP_PASS: Gmail App Password（16 碼，不是登入密碼）
- MAIL_FROM: 寄件者（通常同 SMTP_USER）
- MAIL_TO: 收件者（你自己）
- MAIL_BCC: 逗號分隔 BCC 名單

## 手動測試
Actions → 選本 workflow → Run workflow
