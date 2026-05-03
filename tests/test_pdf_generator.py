import os
import pytest
from typing import Any
from unittest.mock import patch


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(PROJECT_ROOT, "assets", "NotoSansCJK-Regular.otf")

BASE_KWARGS: dict[str, Any] = dict(
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
    manual_discount_amount=0,
    final_total=674,
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
           "manual_discount_amount": 100,
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


# ── New signature tests (manual_discount_amount / shipping params) ──────────

NEW_BASE_KWARGS: dict[str, Any] = dict(
    quote_number="Q20260426-001",
    customer_name="測試客戶",
    resin_label="RPG高精度樹脂",
    file_details=[
        {"filename": "model_a.stl", "volume_ml": 125.51, "body_count": 2},
    ],
    error_files=[],
    material_cost=444,
    processing_fee=230,
    subtotal=674,
    auto_discount_amount=0,
    manual_discount_amount=0,
    final_total=674,
)


def test_new_signature_no_discount_no_shipping(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**NEW_BASE_KWARGS, output_path=output)
    assert os.path.exists(output)
    assert os.path.getsize(output) > 1000
    text = _read_pdf_text(output)
    assert "手動折扣" not in text
    assert "訂單狀態" not in text


def test_new_signature_discount_label_is_zhekow(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**{**NEW_BASE_KWARGS, "manual_discount_amount": 100, "final_total": 574}, output_path=output)
    text = _read_pdf_text(output)
    assert "折扣" in text
    assert "- NT$ 100" in text
    assert "手動折扣" not in text


def test_new_signature_shipping_fee_row_shown(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(
        **{**NEW_BASE_KWARGS, "shipping_address": "台北市大安區", "shipping_fee": 60, "final_total": 734},
        output_path=output,
    )
    text = _read_pdf_text(output)
    assert "運費" in text
    assert "NT$ 60" in text
    assert "台北市大安區" in text


def test_new_signature_shipping_free_label(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(
        **{**NEW_BASE_KWARGS, "shipping_address": "台北市大安區", "shipping_fee": 0, "shipping_free_label": True},
        output_path=output,
    )
    text = _read_pdf_text(output)
    assert "免運費" in text


def test_new_signature_order_status_absent(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote.pdf")
    generate_quote_pdf(**NEW_BASE_KWARGS, output_path=output)
    text = _read_pdf_text(output)
    assert "訂單狀態" not in text


def test_min_order_supplement_row_shown(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote_min_order.pdf")
    generate_quote_pdf(
        **{**NEW_BASE_KWARGS,
           "material_cost": 50,
           "processing_fee": 80,
           "subtotal": 130,
           "final_total": 500,
           "min_order_supplement": 370},
        output_path=output,
    )
    text = _read_pdf_text(output)
    assert "低消補足" in text
    assert "NT$ 370" in text
    assert "NT$ 500" in text


def test_no_supplement_row_when_zero(tmp_path):
    from bot.pdf_gen.generator import generate_quote_pdf
    output = str(tmp_path / "quote_no_supplement.pdf")
    generate_quote_pdf(**NEW_BASE_KWARGS, output_path=output)
    text = _read_pdf_text(output)
    assert "低消補足" not in text
