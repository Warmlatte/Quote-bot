# PDF 範本還原設計規格

**日期**：2026-04-29
**狀態**：待實作
**影響模組**：`bot/pdf_gen/generator.py`

---

## 目標

將現有的 PDF 報價單生成程式改寫，使輸出結果的版面配置與靜態內容完全符合
`3D列印範本.pdf` 範本文件，僅動態替換客戶資訊與計費數字。

---

## 資源路徑

| 資源 | 路徑 |
|---|---|
| 範本 PDF | `/Users/yan/Documents/300 TheRollBer/350 專案資源/355 3D Quote bot 估價機器人/355-7 其他資源/3D列印範本.pdf` |
| Logo（去背 PNG）| `/Users/yan/Documents/300 TheRollBer/350 專案資源/355 3D Quote bot 估價機器人/355-3 Image 資源/TRB_LOGO_去背.png` |

Logo 路徑在生成時透過參數或常數傳入，不硬編碼絕對路徑；預設複製到 `assets/TRB_LOGO.png`。

---

## PDF 結構

### 頁面設定

- 紙張：A4
- 邊距：上 20 mm、下 25 mm（留給頁尾）、左右各 20 mm
- 字型：NotoSansCJK（現有邏輯不變）

### 每頁固定元素

**頁尾**（canvas 回呼，每頁皆有）：
```
骰吧工作室 | Instagram：the.roll.bar | Email：official@therollbar.xyz
```
置中，9pt，灰色，距頁底 10 mm。

### 第一頁：標題區（靜態）

```
骰吧 The Roll Bar                     ← 18pt 粗體置中
光固化 3D 列印代工服務報價單            ← 13pt 置中（注意：非「範例」）
──────────────────────────────────   ← HRFlowable 橫線
```

---

## 各區塊規格

### 一、委託規格明細（動態）

**基本資訊**（bullet 清單）：
```
• 估價單編號：{quote_number}
• 客戶名稱：{customer_name}
• 日期：{date}
• 樹脂種類：{resin_label}
• 物件總件數：{total_body_count} 件
• 樹脂體積總計：{total_volume_ml:.2f} ml
```

**檔案明細表**（有多少列就顯示多少）：

| 檔名 | 體積 (ml) | 件數 |
|---|---|---|
| {filename} | {volume_ml:.4f} | {body_count} |

表頭背景淺灰，格線 0.5pt 灰色，體積與件數欄置中。

若有 `error_files`，在表格下方加註：
```
⚠ 異常檔案（已跳過）：{error_files joined by ", "}
```

**費用明細表**：

| 項目 | 金額 |
|---|---|
| 材料費 | NT$ {material_cost:,} |
| 加工費 | NT$ {processing_fee:,} |
| 小計 | NT$ {subtotal:,} |
| 固定折扣 (95折) ← 僅 auto_discount_amount > 0 時顯示 | - NT$ {auto_discount_amount:,} |
| 手動折扣 ← 僅 manual_discount != "無" 時顯示 | {manual_discount} |
| **最終總價** | **NT$ {final_total:,}** |
| 訂單狀態 | {order_status} |

「最終總價」列：11pt，背景淡黃色。

---

### 二、委託須知與條款（靜態）

標題：`二、委託須知與條款`（12pt 粗體）

引言段落（9pt）：
> 客製化 3D 列印屬依消費者要求所為之客製化給付，一旦進入機台列印程序即會產生不可逆之耗材與時間成本。

編號列表（9pt）：
1. 報價與確認：請於收到報價單後 3 日內確認並進行匯款作業。
2. 常規訂單（全額付清）：單筆報價總額於新台幣 3,000 元（含）以下之訂單，為簡化行政流程，請於排程前全額付清。
3. 中大型專案（階梯式定金）：單筆報價總額超過新台幣 3,000 元之訂單，需先預付 50% 總額作為專案定金。我們將於確認定金入帳後正式啟動排程，並請於收到「成品完工照片」通知後 3 日內結清尾款，以便為您安排出貨。
4. 終止政策：確認排程後，恕不接受無故取消或退還定金。若因原始 3D 圖檔存在無法修復之嚴重物理缺陷導致無法列印，本工作室將主動中止任務，並全額無息退款。
5. 匯款資訊：確認訂單後將另行提供匯款帳戶資訊。

---

### 三、光固化製程說明（靜態）

標題：`三、光固化製程說明`（12pt 粗體）

編號列表（9pt）：
1. 大型物件之「抽殼」與「導流孔」：為確保大型微縮模型（如巨獸、地形）的長期結構穩定性，並顯著減輕最終成品的重量以提升您在 TRPG 遊戲桌上的把玩手感，針對大型物件，本工作室專業工程師將進行「內部抽殼」結構優化。
2. 防爆裂製程：為釋放列印過程中的內部壓力並完全排出殘留的液態樹脂，確保模型長年保存絕不龜裂爆破，我們將於模型底部或視覺隱蔽處設置直徑約 1–3 mm 之「內部導流孔（排水孔）」。此工法為國際高階 3D 列印之標準必備製程，旨在保障最高列印品質，非屬產品瑕疵。
3. 塗裝與補土：若您具備高階塗裝需求，該導流孔極易使用常規模型綠補土自行填平。本工作室標準代工專注於提供高品質之「未塗裝列印素模」，標準費用內不包含補土與無縫填補作業。
4. 特殊製程需求：若客戶有特殊需求（例如不希望抽殼或指定導流孔位置），請於委託前主動告知，以便工程師評估可行性並於報價時納入考量。

---

### 四、排程及物流交期與品管售後（靜態）

標題：`四、排程及物流交期與品管售後`（12pt 粗體）

編號列表（9pt）：
1. 精緻小批量製作：為確保每一件模型皆能在最佳狀態與最嚴謹的後處理程序下完成，本工作室秉持採「精緻化製作」模式，依款項確認順序嚴格安排機台排程。
2. 標準交期：雙方確認圖檔無誤並完成付款後，約需 7 至 14 個工作天（不含法定例假日）完成列印、精密後處理與包裝作業並寄出。
3. 運送方式：預設提供超商店到店服務。若為精密或大型地形件，強烈建議使用宅配或預約工作室面交。單筆訂單超過新台幣 7,000 元享免運優惠。
4. 免費重印保證：若您收到的成品因「我方列印製程問題」導致結構受損或明顯瑕疵，請於簽收後 3 日內拍照回報，本工作室將提供免費重印乙次服務。
5. 檔案免責：若瑕疵肇因於客戶提供之「原始 3D 檔案」本身的結構脆弱、破圖或懸空未支撐，則不在免費重印範圍內。
6. 日常把玩提醒：我們採用具備高韌性之優質樹脂，但微縮模型的纖細部件（如法杖、劍刃）仍具備一定物理極限。請盡量避免從桌面高處跌落，並避免長時間受強烈陽光直射以防材質脆化。

---

### 結尾（靜態）

```
聯絡方式：
骰吧工作室
Instagram：the.roll.bar
Email：official@therollbar.xyz

[Logo 圖片，置中，寬約 60mm]
```

---

## 技術實作細節

### 每頁頁尾：canvas 回呼

```python
def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(_FONT_NAME, 9)
    canvas.setFillColorRGB(0.5, 0.5, 0.5)
    footer_text = "骰吧工作室 | Instagram：the.roll.bar | Email：official@therollbar.xyz"
    canvas.drawCentredString(A4[0] / 2, 10 * mm, footer_text)
    canvas.restoreState()

doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
```

### 字型：Bold 字重註冊

NotoSansCJK TTC 包含多個 subfont，subfontIndex=1 通常為 Bold。
在 `_ensure_font()` 中額外嘗試註冊 Bold 字重（`_FONT_NAME + "-Bold"`）：
```python
pdfmetrics.registerFont(TTFont(_FONT_NAME + "-Bold", path, subfontIndex=1))
```
若註冊失敗（路徑或 index 不符），則 fallback 至 Regular，區塊標題改用 fontSize=13 視覺區分。

### 區塊標題樣式

```python
# 12pt Bold（或 13pt Regular fallback）
section_title_style = ParagraphStyle(
    name="section_title",
    fontName=_FONT_NAME_BOLD,   # "NotoSansCJK-Bold" or fallback to _FONT_NAME
    fontSize=12,
    leading=17,
    spaceBefore=6 * mm,
    spaceAfter=3 * mm,
)
```

### 編號列表

使用 `ListFlowable` + `ListItem`，或手動 `Paragraph` 加縮排（兩者皆可）。
建議手動方式以維持字型一致性：
```python
Paragraph("1. 報價與確認：...", list_item_style)
```

### Logo 圖片

```python
from reportlab.platypus import Image as RLImage
logo = RLImage(_LOGO_PATH, width=60 * mm, height=60 * mm, kind="proportional")
```

`_LOGO_PATH` 常數指向 `assets/TRB_LOGO.png`（開發時手動放置，Docker 映像內含）。

---

## 函式簽名（維持不變）

```python
def generate_quote_pdf(
    quote_number: str,
    customer_name: str,
    resin_label: str,
    file_details: list[dict],
    error_files: list[str],
    material_cost: int,
    processing_fee: int,
    subtotal: int,
    auto_discount_amount: int,
    manual_discount: str,
    final_total: int,
    order_status: str,
    output_path: str,
) -> str:
```

新增內部計算：
- `total_body_count = sum(f["body_count"] for f in file_details)`
- `total_volume_ml = sum(f["volume_ml"] for f in file_details)`

---

## 需複製的資產

| 來源 | 目標 |
|---|---|
| `/Users/yan/.../TRB_LOGO_去背.png` | `assets/TRB_LOGO.png` |

複製指令（一次性手動執行）：
```bash
cp '/Users/yan/Documents/300 TheRollBer/350 專案資源/355 3D Quote bot 估價機器人/355-3 Image 資源/TRB_LOGO_去背.png' assets/TRB_LOGO.png
```

---

## 測試策略

現有 `tests/test_pdf_generator.py` 需更新：
- 驗證 PDF 生成後檔案存在且非空
- 驗證 PDF 頁數 ≥ 2（包含靜態條款）
- 驗證動態欄位（quote_number、customer_name）出現在 PDF 文字中（使用 `pdfplumber` 或 `pypdf`）
- 不測試像素級排版，只驗證內容完整性

---

## 不在範圍內

- 修改 `commands/quote.py` 或其他呼叫端
- 新增 PDF 相關 API 或設定項目
- 範本 PDF 的 OTF/TTF 字型精確匹配（NotoSansCJK 已足夠接近）
