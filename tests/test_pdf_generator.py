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
