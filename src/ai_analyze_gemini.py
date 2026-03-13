def build_prompt(summary: dict) -> str:
    """
    交易導向 prompt（iPhone 友善）
    - 不用表格
    - 段落間空一行
    - 每點用 1) 2) 3)
    - 僅根據 summary/top_preview 的資料下結論，不要編造未提供的資訊
    """
    top_preview = summary.get("top_preview") or []
    days = summary.get("days")
    gen_at = summary.get("generated_at")
    ok = summary.get("brokers_ok", 0)
    fail = summary.get("brokers_fail", 0)
    total_rows = summary.get("total_rows", 0)
    errors = summary.get("errors") or []
    top_n = summary.get("top_n", 10)

    lines = []
    lines.append("你是一位資深台股交易員與籌碼分析師。請用繁體中文、純文字、手機好讀格式輸出。")
    lines.append("嚴格規則：只根據我提供的資料（外資Top清單、淨超、均價、現價、乖離），不要編造題材/財報/新聞。")
    lines.append("輸出中不要用表格、不要對齊空白。段落之間空一行，每點以 1) 2) 3) 編號。")
    lines.append("")
    lines.append(f"報表資訊：產生時間={gen_at}；區間=近{days}日；資料筆數={total_rows}；券商OK={ok}；FAIL={fail}")
    if errors:
        lines.append("注意：若 FAIL>0，只能對有資料的券商做結論；失敗券商不得推論其買賣。")
    lines.append("")

    # 把每家外資 TopN 濃縮成可讀輸入
    if top_preview:
        lines.append(f"外資明細（依外資總淨超排序）Top{top_n}：")
        for block in top_preview:
            broker = block.get("broker", "")
            total_net = block.get("total_net", 0)
            lines.append(f"- {broker}｜總淨超 {total_net} 張")
            for r in (block.get("rows") or [])[:top_n]:
                lines.append(
                    f"  * {r.get('sid','')} {r.get('name','')}｜淨超 {r.get('net',0)}｜均價 {r.get('avg','')}｜現價 {r.get('price','')}｜乖離 {r.get('bias','')}"
                )
        lines.append("")

    # 交易導向輸出格式（可執行）
    lines.append("請依照以下輸出結構（每個區塊中間空一行）：")
    lines.append("A) 今日交易結論（3~5點）：用『因此/所以』描述，可直接下單的優先順序。")
    lines.append("B) 外資力量排行榜：列出『總淨超Top3外資』，各給 2 點（偏多/偏短/偏觀察）。")
    lines.append("C) 明日觀察清單（5檔）：每檔必含：")
    lines.append("   1) 進場條件（例如：突破/回測/不跌破某均價等，請用文字描述，不要給不存在的K線數據）")
    lines.append("   2) 風險點與停損邏輯（用均價/乖離/淨超集中度推導）")
    lines.append("   3) 可能的獲利了結邏輯（例如：乖離擴大、淨超集中、現價遠離均價等）")
    lines.append("D) 風控提醒（3點）：例如『乖離過大追價風險』『淨超集中單一外資風險』『fail券商缺資料風險』")
    lines.append("")
    lines.append("額外限制：")
    lines.append("- 不要提及新聞、財報、產業題材（我沒提供）")
    lines.append("- 若現價或均價為 0 或缺值，該檔只能列為『資料不足』，不得硬判斷")
    lines.append("- 最後加一段『一句話摘要』，≤ 25 字，適合當手機通知預覽")

    return "\n".join(lines)
``