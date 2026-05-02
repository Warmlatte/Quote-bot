import io
import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable,
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
# reportlab TTFont 只支援 TrueType outlines（TTF/TTC），不支援 CFF/OTF。
# 依優先順序嘗試字型，全部失敗時拋出 FileNotFoundError。
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
    alignment = 1 if align == "CENTER" else 2 if align == "RIGHT" else 0
    font = _FONT_NAME_BOLD if bold else _FONT_NAME
    return ParagraphStyle(
        name=f"cjk_{size}_{bold}_{align}",
        fontName=font,
        fontSize=size,
        leading=size * 1.5,
        alignment=alignment,  # type: ignore[arg-type]
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
    manual_discount_amount: int = 0,
    min_order_supplement: int = 0,
    final_total: int = 0,
    shipping_fee: int = 0,
    shipping_address: str = "",
    shipping_free_label: bool = False,
    output_path: str = "",
) -> str:
    _ensure_font()

    def _make_story() -> list:
        s: list = []

        # ── 標題區 ──────────────────────────────────────────────────────────
        s.append(Paragraph("骰吧 The Roll Bar", _style(18, align="CENTER")))
        s.append(_sp(2))
        s.append(Paragraph("光固化 3D 列印代工服務報價單", _style(13, align="CENTER")))
        s.append(_sp(4))
        s.append(HRFlowable(width="100%", thickness=1, color=colors.black, spaceAfter=4 * mm))

        # ── 一、委託規格明細 ─────────────────────────────────────────────────
        s.append(_section_title("一、委託規格明細"))
        s.append(_sp(3))

        total_body_count = sum(f["body_count"] for f in file_details)
        total_volume_ml = sum(f["volume_ml"] for f in file_details)

        spec_bullets = [
            f"估價單編號：{quote_number}",
            f"客戶名稱：{customer_name}",
            f"日期：{date.today().isoformat()}",
            f"樹脂種類：{resin_label}",
            f"物件總件數：{total_body_count} 件",
            f"樹脂體積總計：{total_volume_ml:.1f} ml",
        ]
        if shipping_address:
            spec_bullets.append(f"寄送地址：{shipping_address}")

        for line in spec_bullets:
            s.append(_bullet(line))
        s.append(_sp(4))

        # 檔案明細表
        detail_data = [["檔名", "體積 (ml)", "件數"]]
        for f in file_details:
            detail_data.append([f["filename"], f"{f['volume_ml']:.1f}", str(f["body_count"])])

        detail_table = Table(detail_data, colWidths=[100 * mm, 40 * mm, 25 * mm])
        detail_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]))
        s.append(detail_table)

        if error_files:
            s.append(_sp(2))
            s.append(Paragraph(f"⚠ 異常檔案（已跳過）：{', '.join(error_files)}", _style(9)))

        s.append(_sp(4))

        # 費用明細表
        cost_data: list[list[str]] = [
            ["材料費", f"NT$ {material_cost:,}"],
            ["加工費", f"NT$ {processing_fee:,}"],
            ["小計", f"NT$ {subtotal:,}"],
        ]
        if auto_discount_amount > 0:
            cost_data.append(["固定折扣 (95折)", f"- NT$ {auto_discount_amount:,}"])
        if manual_discount_amount > 0:
            cost_data.append(["折扣", f"- NT$ {manual_discount_amount:,}"])
        if min_order_supplement > 0:
            cost_data.append(["低消補足", f"+ NT$ {min_order_supplement:,}"])
        if shipping_address:
            ship_val = "免運費" if shipping_free_label else f"NT$ {shipping_fee:,}"
            cost_data.append(["運費", ship_val])
        final_row_idx = len(cost_data)
        cost_data.append(["最終總價", f"NT$ {final_total:,}"])
        # 訂單狀態列已移除

        cost_table = Table(cost_data, colWidths=[60 * mm, 105 * mm])
        cost_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTNAME", (0, final_row_idx), (-1, final_row_idx), _FONT_NAME_BOLD),
            ("FONTSIZE", (0, final_row_idx), (-1, final_row_idx), 11),
            ("BACKGROUND", (0, final_row_idx), (-1, final_row_idx), colors.lightyellow),
        ]))
        s.append(cost_table)

        # ── 靜態條款區塊 ────────────────────────────────────────────────────
        s.extend(_build_section_2())
        s.extend(_build_section_3())
        s.extend(_build_section_4())

        # ── 聯絡資訊 ────────────────────────────────────────────────────────
        s.append(_sp(6))
        s.append(Paragraph("聯絡方式：", _style(10)))
        s.append(Paragraph("骰吧工作室", _style(10)))
        s.append(Paragraph("Instagram：the.roll.bar", _style(10)))
        s.append(Paragraph("Email：official@therollbar.xyz", _style(10)))

        return s

    # ── 第一遍：計算總頁數 ───────────────────────────────────────────────────
    _last_page: list[int] = [0]

    def _count_pages(canvas, doc) -> None:
        _last_page[0] = doc.page

    _doc_count = SimpleDocTemplate(
        io.BytesIO(), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=42 * mm,
    )
    _doc_count.build(_make_story(), onFirstPage=_count_pages, onLaterPages=_count_pages)
    total_pages = _last_page[0]

    # ── 頁尾 + 最後一頁 Logo（置於頁尾文字正上方）────────────────────────────
    def _footer_and_logo(canvas, doc) -> None:
        _draw_footer(canvas, doc)
        if doc.page == total_pages and os.path.exists(_LOGO_PATH):
            img_reader = ImageReader(_LOGO_PATH)
            img_w, img_h = img_reader.getSize()
            logo_w = 30 * mm
            logo_h = logo_w * img_h / img_w
            logo_x = (A4[0] - logo_w) / 2
            logo_y = 16 * mm  # 頁尾文字在 10mm，上方留 6mm 間距
            canvas.saveState()
            canvas.drawImage(_LOGO_PATH, logo_x, logo_y, width=logo_w, height=logo_h)
            canvas.restoreState()

    # ── 第二遍：生成最終 PDF ─────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=20 * mm, bottomMargin=42 * mm,
    )
    doc.build(_make_story(), onFirstPage=_footer_and_logo, onLaterPages=_footer_and_logo)
    return output_path
