# PDF 範本還原 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重寫 `bot/pdf_gen/generator.py`，使生成的報價單 PDF 版面與 `3D列印範本.pdf` 完全一致，包含四個區塊、每頁頁尾、橫線分隔及 Logo。

**Architecture:** 僅修改 `generator.py`（單一責任）；函式簽名不變，內部新增靜態段落建構 helper、canvas 頁尾回呼、Bold 字重備援機制。靜態條款文字硬編碼在模組內，不從外部讀取。

**Tech Stack:** Python 3.12、reportlab 4.2、pypdf（新增，用於測試驗證）

---

## File Map

| 動作 | 檔案 |
|---|---|
| Modify | `bot/pdf_gen/generator.py` |
| Modify | `tests/test_pdf_generator.py` |
| Modify | `requirements.txt` |
| Create | `assets/TRB_LOGO.png`（複製自外部路徑） |

---

## Task 1：準備資產與測試依賴

**Files:**
- Modify: `requirements.txt`
- Create: `assets/TRB_LOGO.png`

- [ ] **Step 1：複製 Logo 到 assets/**

```bash
cp '/Users/yan/Documents/300 TheRollBer/350 專案資源/355 3D Quote bot 估價機器人/355-3 Image 資源/TRB_LOGO_去背.png' \
   '/Users/yan/Documents/300 TheRollBer/340 進行中專案/342 3D Printing Quote bot 估價機器人/Quote_Bot/assets/TRB_LOGO.png'
```

Expected: 無輸出，`assets/TRB_LOGO.png` 存在。

- [ ] **Step 2：將 pypdf 加入 requirements.txt**

在 `requirements.txt` 最後加一行：

```
pypdf==4.3.1
```

- [ ] **Step 3：安裝 pypdf**

```bash
pip install pypdf==4.3.1
```

Expected: `Successfully installed pypdf-4.3.1`（或已安裝訊息）。

- [ ] **Step 4：確認 Logo 和依賴就緒**

```bash
python -c "import pypdf; print('pypdf OK')"
ls assets/TRB_LOGO.png
```

Expected:
```
pypdf OK
assets/TRB_LOGO.png
```

---

## Task 2：撰寫失敗測試（TDD RED）

**Files:**
- Modify: `tests/test_pdf_generator.py`

用以下內容**完整取代** `tests/test_pdf_generator.py`：

- [ ] **Step 1：更新測試檔**

```python
import os
import pytest
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(PROJECT_ROOT, "assets", "NotoSansCJK-Regular.otf")

BASE_KWARGS = dict(
    quote_number="Q20260426-001",
    customer_name="測試客戶",
    resin_label="RPG高精度樹脂",
    file_details=[
        {"filename": "model_a.stl", "volume_ml": 125.51, "body_count": 2},
        {"filename": "model_b.stl", "volume_ml": 0.79, "body_count": 1},
    ],
    error_files=[],
    material_cost=444,
    processing_fee=230,
    subtotal=674,
    auto_discount_amount=0,
    manual_discount="無",
    final_total=674,
    order_status="正常",
)


def _read_pdf_text(path: str) -> str:
    import pypdf
    reader = pypdf.PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _get_pdf_page_count(path: str) -> int:
    import pypdf
    return len(pypdf.PdfReader(path).pages)


def test_basic_pdf_generated(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf

    output = str(tmp_path / "quote.pdf")
    result = generate_quote_pdf(**BASE_KWARGS, output_path=output)

    assert result == output
    assert os.path.exists(output)
    assert os.path.getsize(output) > 0


def test_pdf_with_discounts_and_errors(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf

    output = str(tmp_path / "quote_discount.pdf")
    generate_quote_pdf(
        **{**BASE_KWARGS,
           "auto_discount_amount": 350,
           "manual_discount": "九折+免運",
           "error_files": ["broken.stl"],
           "output_path": output},
    )

    assert os.path.exists(output)
    assert os.path.getsize(output) > 0


def test_missing_font_raises(tmp_path):
    from bot.pdf_gen import generator

    output = str(tmp_path / "quote.pdf")
    fake_paths = [("/nonexistent/font.ttc", 0)]
    with patch.object(generator, "_FONT_PATHS", fake_paths):
        generator._font_registered = False
        with pytest.raises(FileNotFoundError):
            generator.generate_quote_pdf(**BASE_KWARGS, output_path=output)
    generator._font_registered = False


def test_pdf_has_multiple_pages(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf

    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**BASE_KWARGS, output_path=output)
    assert _get_pdf_page_count(output) >= 2


def test_pdf_contains_dynamic_content(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf

    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**BASE_KWARGS, output_path=output)
    text = _read_pdf_text(output)
    assert "Q20260426-001" in text
    assert "測試客戶" in text
    assert "model_a.stl" in text


def test_pdf_contains_static_sections(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf

    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**BASE_KWARGS, output_path=output)
    text = _read_pdf_text(output)
    assert "委託須知" in text
    assert "光固化製程" in text
    assert "排程及物流" in text


def test_pdf_footer_present(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf

    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**BASE_KWARGS, output_path=output)
    text = _read_pdf_text(output)
    assert "the.roll.bar" in text
```

- [ ] **Step 2：執行新測試，確認新增的 4 個測試失敗（舊的 3 個應仍通過）**

```bash
cd '/Users/yan/Documents/300 TheRollBer/340 進行中專案/342 3D Printing Quote bot 估價機器人/Quote_Bot'
pytest tests/test_pdf_generator.py -v
```

Expected 結果：
- `test_basic_pdf_generated` — PASS
- `test_pdf_with_discounts_and_errors` — PASS
- `test_missing_font_raises` — PASS
- `test_pdf_has_multiple_pages` — **FAIL**
- `test_pdf_contains_dynamic_content` — PASS 或 FAIL（取決於現有 PDF 是否含這些字串）
- `test_pdf_contains_static_sections` — **FAIL**（現有程式碼無靜態條款）
- `test_pdf_footer_present` — **FAIL**（現有程式碼無頁尾）

---

## Task 3：實作新版 generator.py

**Files:**
- Modify: `bot/pdf_gen/generator.py`

用以下**完整內容取代** `bot/pdf_gen/generator.py`：

- [ ] **Step 1：完整覆寫 generator.py**

```python
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
)
_FONT_PATHS: list[tuple[str, int]] = [
    (os.path.join(_ASSETS_DIR, "NotoSansCJK-Regular.ttc"), 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJKtc-Regular.ttf", 0),
]
_FONT_NAME = "NotoSansCJK"
_FONT_NAME_BOLD = "NotoSansCJK"  # updated to "NotoSansCJK-Bold" if bold registration succeeds
_LOGO_PATH = os.path.join(_ASSETS_DIR, "TRB_LOGO.png")
_font_registered = False


def _ensure_font() -> None:
    global _font_registered, _FONT_NAME_BOLD
    if _font_registered:
        return
    for path, idx in _FONT_PATHS:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(_FONT_NAME, path, subfontIndex=idx))
            _font_registered = True
            # try to register bold variant (subfontIndex=1)
            try:
                pdfmetrics.registerFont(TTFont(_FONT_NAME + "-Bold", path, subfontIndex=1))
                _FONT_NAME_BOLD = _FONT_NAME + "-Bold"
            except Exception:
                _FONT_NAME_BOLD = _FONT_NAME
            return
        except Exception:
            continue
    raise FileNotFoundError(
        "找不到可用的 CJK 字型（TTF/TTC）。"
        f"請提供以下任一字型：{[p for p, _ in _FONT_PATHS]}"
    )


def _draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont(_FONT_NAME, 9)
    canvas.setFillGray(0.5)
    canvas.drawCentredString(
        A4[0] / 2,
        10 * mm,
        "骰吧工作室 | Instagram：the.roll.bar | Email：official@therollbar.xyz",
    )
    canvas.restoreState()


def _style(size: int = 10, bold: bool = False, align: str = "LEFT") -> ParagraphStyle:
    align_map = {"LEFT": 0, "CENTER": 1, "RIGHT": 2}
    font = _FONT_NAME_BOLD if bold else _FONT_NAME
    return ParagraphStyle(
        name=f"cjk_{size}_{bold}_{align}",
        fontName=font,
        fontSize=size,
        leading=size * 1.5,
        alignment=align_map.get(align, 0),
    )


def _section_title(text: str) -> Paragraph:
    return Paragraph(text, _style(12, bold=True))


def _list_item(text: str, size: int = 9) -> Paragraph:
    return Paragraph(
        text,
        ParagraphStyle(
            name=f"list_{size}",
            fontName=_FONT_NAME,
            fontSize=size,
            leading=size * 1.5,
            leftIndent=5 * mm,
        ),
    )


def _bullet(text: str, size: int = 10) -> Paragraph:
    return Paragraph(
        f"• {text}",
        ParagraphStyle(
            name=f"bullet_{size}",
            fontName=_FONT_NAME,
            fontSize=size,
            leading=size * 1.5,
            leftIndent=3 * mm,
        ),
    )


def _sp(n: int = 4) -> Spacer:
    return Spacer(1, n * mm)


def _build_section_2() -> list:
    items: list = [
        _sp(6),
        _section_title("二、委託須知與條款"),
        _sp(3),
        Paragraph(
            "客製化 3D 列印屬依消費者要求所為之客製化給付，一旦進入機台列印程序即會產生不可逆之耗材與時間成本。",
            _style(9),
        ),
        _sp(2),
    ]
    clauses = [
        "報價與確認：請於收到報價單後 3 日內確認並進行匯款作業。",
        "常規訂單（全額付清）：單筆報價總額於新台幣 3,000 元（含）以下之訂單，為簡化行政流程，請於排程前全額付清。",
        "中大型專案（階梯式定金）：單筆報價總額超過新台幣 3,000 元之訂單，需先預付 50% 總額作為專案定金。我們將於確認定金入帳後正式啟動排程，並請於收到「成品完工照片」通知後 3 日內結清尾款，以便為您安排出貨。",
        "終止政策：確認排程後，恕不接受無故取消或退還定金。若因原始 3D 圖檔存在無法修復之嚴重物理缺陷導致無法列印，本工作室將主動中止任務，並全額無息退款。",
        "匯款資訊：確認訂單後將另行提供匯款帳戶資訊。",
    ]
    for i, text in enumerate(clauses, 1):
        items.append(_list_item(f"{i}. {text}"))
        items.append(_sp(1))
    return items


def _build_section_3() -> list:
    items: list = [
        _sp(6),
        _section_title("三、光固化製程說明"),
        _sp(3),
    ]
    points = [
        "大型物件之「抽殼」與「導流孔」：為確保大型微縮模型（如巨獸、地形）的長期結構穩定性，並顯著減輕最終成品的重量以提升您在 TRPG 遊戲桌上的把玩手感，針對大型物件，本工作室專業工程師將進行「內部抽殼」結構優化。",
        "防爆裂製程：為釋放列印過程中的內部壓力並完全排出殘留的液態樹脂，確保模型長年保存絕不龜裂爆破，我們將於模型底部或視覺隱蔽處設置直徑約 1–3 mm 之「內部導流孔（排水孔）」。此工法為國際高階 3D 列印之標準必備製程，旨在保障最高列印品質，非屬產品瑕疵。",
        "塗裝與補土：若您具備高階塗裝需求，該導流孔極易使用常規模型綠補土自行填平。本工作室標準代工專注於提供高品質之「未塗裝列印素模」，標準費用內不包含補土與無縫填補作業。",
        "特殊製程需求：若客戶有特殊需求（例如不希望抽殼或指定導流孔位置），請於委託前主動告知，以便工程師評估可行性並於報價時納入考量。",
    ]
    for i, text in enumerate(points, 1):
        items.append(_list_item(f"{i}. {text}"))
        items.append(_sp(1))
    return items


def _build_section_4() -> list:
    items: list = [
        _sp(6),
        _section_title("四、排程及物流交期與品管售後"),
        _sp(3),
    ]
    points = [
        "精緻小批量製作：為確保每一件模型皆能在最佳狀態與最嚴謹的後處理程序下完成，本工作室秉持採「精緻化製作」模式，依款項確認順序嚴格安排機台排程。",
        "標準交期：雙方確認圖檔無誤並完成付款後，約需 7 至 14 個工作天（不含法定例假日）完成列印、精密後處理與包裝作業並寄出。",
        "運送方式：預設提供超商店到店服務。若為精密或大型地形件，強烈建議使用宅配或預約工作室面交。單筆訂單超過新台幣 7,000 元享免運優惠。",
        "免費重印保證：若您收到的成品因「我方列印製程問題」導致結構受損或明顯瑕疵，請於簽收後 3 日內拍照回報，本工作室將提供免費重印乙次服務。",
        "檔案免責：若瑕疵肇因於客戶提供之「原始 3D 檔案」本身的結構脆弱、破圖或懸空未支撐，則不在免費重印範圍內。",
        "日常把玩提醒：我們採用具備高韌性之優質樹脂，但微縮模型的纖細部件（如法杖、劍刃）仍具備一定物理極限。請盡量避免從桌面高處跌落，並避免長時間受強烈陽光直射以防材質脆化。",
    ]
    for i, text in enumerate(points, 1):
        items.append(_list_item(f"{i}. {text}"))
        items.append(_sp(1))
    return items


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
    _ensure_font()

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=25 * mm,
    )

    story: list = []

    # ── 標題區 ────────────────────────────────────────────────────────────────
    story.append(Paragraph("骰吧 The Roll Bar", _style(18, align="CENTER")))
    story.append(_sp(2))
    story.append(Paragraph("光固化 3D 列印代工服務報價單", _style(13, align="CENTER")))
    story.append(_sp(4))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black, spaceAfter=4 * mm))

    # ── 一、委託規格明細 ───────────────────────────────────────────────────────
    story.append(_section_title("一、委託規格明細"))
    story.append(_sp(3))

    total_body_count = sum(f["body_count"] for f in file_details)
    total_volume_ml = sum(f["volume_ml"] for f in file_details)

    for line in [
        f"估價單編號：{quote_number}",
        f"客戶名稱：{customer_name}",
        f"日期：{date.today().isoformat()}",
        f"樹脂種類：{resin_label}",
        f"物件總件數：{total_body_count} 件",
        f"樹脂體積總計：{total_volume_ml:.2f} ml",
    ]:
        story.append(_bullet(line))
    story.append(_sp(4))

    # 檔案明細表
    detail_data = [["檔名", "體積 (ml)", "件數"]]
    for f in file_details:
        detail_data.append([f["filename"], f"{f['volume_ml']:.4f}", str(f["body_count"])])

    detail_table = Table(detail_data, colWidths=[100 * mm, 40 * mm, 25 * mm])
    detail_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    story.append(detail_table)

    if error_files:
        story.append(_sp(2))
        story.append(Paragraph(f"⚠ 異常檔案（已跳過）：{', '.join(error_files)}", _style(9)))

    story.append(_sp(4))

    # 費用明細表
    cost_data: list[list[str]] = [
        ["材料費", f"NT$ {material_cost:,}"],
        ["加工費", f"NT$ {processing_fee:,}"],
        ["小計", f"NT$ {subtotal:,}"],
    ]
    if auto_discount_amount > 0:
        cost_data.append(["固定折扣 (95折)", f"- NT$ {auto_discount_amount:,}"])
    if manual_discount != "無":
        cost_data.append(["手動折扣", manual_discount])
    final_row_idx = len(cost_data)
    cost_data.append(["最終總價", f"NT$ {final_total:,}"])
    cost_data.append(["訂單狀態", order_status])

    cost_table = Table(cost_data, colWidths=[60 * mm, 105 * mm])
    cost_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, final_row_idx), (-1, final_row_idx), _FONT_NAME_BOLD),
        ("FONTSIZE", (0, final_row_idx), (-1, final_row_idx), 11),
        ("BACKGROUND", (0, final_row_idx), (-1, final_row_idx), colors.lightyellow),
    ]))
    story.append(cost_table)

    # ── 靜態條款區塊 ──────────────────────────────────────────────────────────
    story.extend(_build_section_2())
    story.extend(_build_section_3())
    story.extend(_build_section_4())

    # ── 聯絡資訊 + Logo ───────────────────────────────────────────────────────
    story.append(_sp(6))
    story.append(Paragraph("聯絡方式：", _style(10)))
    story.append(Paragraph("骰吧工作室", _style(10)))
    story.append(Paragraph("Instagram：the.roll.bar", _style(10)))
    story.append(Paragraph("Email：official@therollbar.xyz", _style(10)))
    story.append(_sp(6))

    if os.path.exists(_LOGO_PATH):
        logo = RLImage(_LOGO_PATH, width=60 * mm, height=60 * mm, kind="proportional")
        logo.hAlign = "CENTER"
        story.append(logo)

    doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)
    return output_path
```

---

## Task 4：執行測試（TDD GREEN）

**Files:** 無修改

- [ ] **Step 1：執行完整測試套件**

```bash
cd '/Users/yan/Documents/300 TheRollBer/340 進行中專案/342 3D Printing Quote bot 估價機器人/Quote_Bot'
pytest tests/test_pdf_generator.py -v
```

Expected：全部 7 個測試 PASS。

若 `test_pdf_has_multiple_pages` 仍 FAIL（靜態條款未撐出第 2 頁），增加 `bottomMargin=25*mm` 或在 `_build_section_4()` 後加 `PageBreak`。若其他測試 FAIL，依錯誤訊息診斷。

- [ ] **Step 2：執行覆蓋率確認 ≥ 80%**

```bash
pytest tests/test_pdf_generator.py --cov=bot/pdf_gen --cov-report=term-missing
```

Expected：`bot/pdf_gen/generator.py` 覆蓋率 ≥ 80%。

---

## Task 5：Commit

**Files:** 無修改

- [ ] **Step 1：確認所有變更**

```bash
cd '/Users/yan/Documents/300 TheRollBer/340 進行中專案/342 3D Printing Quote bot 估價機器人/Quote_Bot'
git status
git diff requirements.txt
```

- [ ] **Step 2：暫存並提交**

```bash
git add requirements.txt assets/TRB_LOGO.png bot/pdf_gen/generator.py tests/test_pdf_generator.py
git commit -m "feat: rewrite PDF generator to match template layout with static clauses and footer"
```

Expected：commit 成功，post-commit hook 自動 push 到 GitHub。

---

## Self-Review

**Spec coverage check:**

| 規格需求 | 對應 Task |
|---|---|
| 標題 + 橫線 | Task 3 Step 1（`generate_quote_pdf` 標題區） |
| 每頁頁尾 | Task 3 Step 1（`_draw_footer`） |
| Section 一 bullet 摘要 | Task 3 Step 1（bullet loop） |
| Section 一 檔案明細表 | Task 3 Step 1（`detail_table`） |
| Section 一 費用明細表 | Task 3 Step 1（`cost_table`） |
| Section 二 委託須知 | Task 3 Step 1（`_build_section_2`） |
| Section 三 光固化製程 | Task 3 Step 1（`_build_section_3`） |
| Section 四 排程交期 | Task 3 Step 1（`_build_section_4`） |
| 聯絡資訊 + Logo | Task 3 Step 1（最後段落） |
| Bold 字重備援 | Task 3 Step 1（`_ensure_font` + `_FONT_NAME_BOLD`） |
| 函式簽名不變 | Task 3 Step 1（`generate_quote_pdf` 簽名） |
| 測試覆蓋率 ≥ 80% | Task 4 Step 2 |

**Placeholder scan:** 無 TBD / TODO。所有程式碼區塊均為完整可執行內容。

**Type consistency:** `_FONT_NAME_BOLD` 在 `_ensure_font()` 中初始化為 `"NotoSansCJK"`，在 `_style()` 與 `cost_table` 中使用，命名一致。`_sp()`、`_bullet()`、`_list_item()`、`_section_title()` 皆在 Task 3 中定義後使用。
