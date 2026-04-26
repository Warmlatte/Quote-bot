import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets",
)
# reportlab TTFont 只支援 TrueType outlines（TTF/TTC），不支援 CFF/OTF。
# 依優先順序嘗試字型，全部失敗時拋出 FileNotFoundError。
_FONT_PATHS: list[tuple[str, int]] = [
    (os.path.join(_ASSETS_DIR, "NotoSansCJK-Regular.ttc"), 0),       # assets TTС（建議）
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),                  # macOS 開發環境
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),    # Docker/Linux
    ("/usr/share/fonts/truetype/noto/NotoSansCJKtc-Regular.ttf", 0),  # Debian fonts-noto-cjk-extra
]
_FONT_NAME = "NotoSansCJK"
_font_registered = False


def _ensure_font() -> None:
    global _font_registered
    if _font_registered:
        return
    for path, idx in _FONT_PATHS:
        if not os.path.exists(path):
            continue
        try:
            pdfmetrics.registerFont(TTFont(_FONT_NAME, path, subfontIndex=idx))
            _font_registered = True
            return
        except Exception:
            continue
    raise FileNotFoundError(
        "找不到可用的 CJK 字型（TTF/TTC）。"
        f"請提供以下任一字型：{[p for p, _ in _FONT_PATHS]}"
    )


def _style(size: int = 10, bold: bool = False, align: str = "LEFT") -> ParagraphStyle:
    align_map = {"LEFT": 0, "CENTER": 1, "RIGHT": 2}
    return ParagraphStyle(
        name=f"cjk_{size}_{bold}",
        fontName=_FONT_NAME,
        fontSize=size,
        leading=size * 1.4,
        alignment=align_map.get(align, 0),
    )


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
        bottomMargin=20 * mm,
    )

    story = []
    sp = lambda n=8: Spacer(1, n * mm)

    # ── 標題區 ────────────────────────────────────────────────────────────────
    story.append(Paragraph("骰吧 The Roll Bar", _style(18, align="CENTER")))
    story.append(Paragraph("光固化 3D 列印代工服務報價單", _style(13, align="CENTER")))
    story.append(sp(3))
    story.append(Paragraph(f"估價單編號：{quote_number}", _style(10)))
    story.append(Paragraph(f"客戶名稱：{customer_name}", _style(10)))
    story.append(Paragraph(f"日期：{date.today().isoformat()}", _style(10)))
    story.append(sp())

    # ── 委託規格明細 ──────────────────────────────────────────────────────────
    story.append(Paragraph("委託規格明細", _style(12)))
    story.append(sp(2))

    detail_data = [["檔名", "體積 (ml)", "件數"]]
    for f in file_details:
        detail_data.append([f["filename"], f"{f['volume_ml']:.4f}", str(f["body_count"])])

    detail_table = Table(detail_data, colWidths=[90 * mm, 40 * mm, 25 * mm])
    detail_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
    ]))
    story.append(detail_table)

    if error_files:
        story.append(sp(2))
        story.append(Paragraph(f"⚠ 異常檔案（已跳過）：{', '.join(error_files)}", _style(9)))

    story.append(sp())

    # ── 費用計算 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("費用計算", _style(12)))
    story.append(sp(2))

    cost_data = [
        ["樹脂種類", resin_label],
        ["材料費", f"NT$ {material_cost:,}"],
        ["加工費", f"NT$ {processing_fee:,}"],
        ["小計", f"NT$ {subtotal:,}"],
    ]
    if auto_discount_amount > 0:
        cost_data.append(["固定折扣 (95折)", f"- NT$ {auto_discount_amount:,}"])
    if manual_discount != "無":
        cost_data.append(["手動折扣", manual_discount])
    cost_data.append(["最終總價", f"NT$ {final_total:,}"])
    cost_data.append(["訂單狀態", order_status])

    cost_table = Table(cost_data, colWidths=[60 * mm, 95 * mm])
    final_row = len(cost_data) - 2  # "最終總價" row index
    cost_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, final_row), (-1, final_row), _FONT_NAME),
        ("FONTSIZE", (0, final_row), (-1, final_row), 11),
        ("BACKGROUND", (0, final_row), (-1, final_row), colors.lightyellow),
    ]))
    story.append(cost_table)
    story.append(sp())

    # ── 委託須知 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("委託須知", _style(12)))
    story.append(sp(2))
    story.append(Paragraph(
        "請確認報價內容後方可接受，接受後視同確認委託。"
        "列印完成後如有瑕疵問題請於收件 3 日內聯繫工作室。",
        _style(9),
    ))
    story.append(sp())

    # ── 聯絡資訊 ──────────────────────────────────────────────────────────────
    story.append(Paragraph("聯絡資訊", _style(12)))
    story.append(sp(2))
    story.append(Paragraph("骰吧工作室", _style(10)))
    story.append(Paragraph("Instagram：the.roll.bar", _style(10)))
    story.append(Paragraph("Email：official@therollbar.xyz", _style(10)))

    doc.build(story)
    return output_path
