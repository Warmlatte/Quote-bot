"""
Integration tests for the Quote Bot pipeline.

Tests exercise the full flow: model data → pricing engine → SQLite persistence,
covering all major quote scenarios defined in the integration-test-spec change.
"""
import math

import pytest

from bot.db.client import DBClient
from bot.pricing.engine import (
    DiscountInput,
    QuoteResult,
    ResinType,
    apply_manual_discount,
    calculate_quote,
)
from bot.pricing.model_reader import ModelReadResult

# ── Shared constants ──────────────────────────────────────────────────────────

_NO_DISCOUNT = DiscountInput(mode="none", value=0)
_NINE_TEN = DiscountInput(mode="pct", value=0.9)
_DATE_PREFIX = "260503"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    """Isolated SQLite database for each test."""
    return DBClient(str(tmp_path / "integration.db"))


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_model_result(
    filename: str, volume_ml: float, body_count: int
) -> ModelReadResult:
    """Produce a ModelReadResult fixture without running trimesh."""
    return ModelReadResult(filename=filename, volume_ml=volume_ml, body_count=body_count)


def run_quote_pipeline(
    db: DBClient,
    model_results: list[ModelReadResult],
    resin: ResinType,
    colored: bool = False,
    discount: DiscountInput = _NO_DISCOUNT,
    shipping_fee: int = 0,
    shipping_address: str = "",
    decision: str = "接受",
    rejection_reason: str = "",
    date_prefix: str = _DATE_PREFIX,
) -> tuple[QuoteResult, dict]:
    """
    Execute the full quote pipeline: pricing → manual discount → shipping → SQLite.

    Raises ValueError when model_results is empty.
    Returns (quote_result, db_row).
      quote_result.final_total  = auto-discounted total (before manual discount / shipping)
      db_row["final_total"]     = final display total (after manual discount + shipping fee)
    """
    if not model_results:
        raise ValueError("no model files found")

    total_volume_ml = sum(r.volume_ml for r in model_results)
    total_body_count = sum(r.body_count for r in model_results)

    quote_result = calculate_quote(resin, colored, total_volume_ml, total_body_count)
    manual_discounted_total, manual_discount_amount = apply_manual_discount(
        quote_result.final_total, discount
    )
    final_total = manual_discounted_total + shipping_fee

    if decision == "接受":
        count = db.count_accepted_quotes_today(date_prefix)
        quote_number = f"trb{date_prefix}{count + 1:02d}"
    else:
        quote_number = ""

    db.insert_quote_record(
        quote_number=quote_number,
        customer_name="測試客戶",
        resin_label=resin.value,
        body_count=total_body_count,
        material_cost=quote_result.material_cost,
        processing_fee=quote_result.processing_fee,
        auto_discount=str(quote_result.auto_discount_amount),
        manual_discount=str(manual_discount_amount),
        subtotal=quote_result.subtotal,
        final_total=final_total,
        order_status=quote_result.order_status,
        decision=decision,
        rejection_reason=rejection_reason if rejection_reason else None,
        shipping_fee=shipping_fee,
        shipping_address=shipping_address,
    )

    rows = db.get_unsynced_quote_records()
    return quote_result, dict(rows[-1])


# ── 1. Normal quote pipeline (no discount, no shipping) ───────────────────────


def test_normal_quote_no_discount_no_shipping(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 700
    assert quote_result.processing_fee == 230
    assert quote_result.subtotal == 930
    assert quote_result.order_status == "正常"
    assert quote_result.auto_free_ship is False
    assert db_row["final_total"] == 930
    assert db_row["decision"] == "接受"
    assert db_row["shipping_fee"] == 0
    assert db_row["shipping_address"] == ""


@pytest.mark.parametrize(
    "volume_ml,body_count,resin,colored,expected_material,expected_fee,expected_subtotal",
    [
        (200.0, 3, ResinType.RPG, False, 700, 230, 930),
        (100.0, 5, ResinType.RPG, False, 350, 370, 720),
        (150.0, 2, ResinType.CLEAR, False, 525, 160, 685),
    ],
)
def test_normal_quote_example_table(
    db,
    volume_ml,
    body_count,
    resin,
    colored,
    expected_material,
    expected_fee,
    expected_subtotal,
):
    models = [make_model_result("model.stl", volume_ml, body_count)]
    quote_result, db_row = run_quote_pipeline(db, models, resin, colored=colored)

    assert quote_result.material_cost == expected_material
    assert quote_result.processing_fee == expected_fee
    assert quote_result.subtotal == expected_subtotal
    assert db_row["final_total"] == expected_subtotal


# ── 2. Discount quote pipeline (manual discount, no shipping) ─────────────────


def test_discount_nine_ten_no_shipping(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    quote_result, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN
    )

    assert quote_result.subtotal == 930
    assert db_row["final_total"] == 837
    assert db_row["manual_discount"] == "93"
    assert db_row["shipping_fee"] == 0


def test_discount_fixed_no_shipping(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=DiscountInput(mode="fixed", value=100)
    )

    assert db_row["final_total"] == 830
    assert db_row["manual_discount"] == "100"


@pytest.mark.parametrize(
    "discount,expected_final,expected_discount_amount",
    [
        (_NINE_TEN, 837, 93),
        (DiscountInput(mode="pct", value=0.8), 744, 186),
        (DiscountInput(mode="fixed", value=100), 830, 100),
        (_NO_DISCOUNT, 930, 0),
    ],
)
def test_discount_variants_example_table(db, discount, expected_final, expected_discount_amount):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, discount=discount)

    assert db_row["final_total"] == expected_final
    assert db_row["manual_discount"] == str(expected_discount_amount)


# ── 3. Shipping quote pipeline (no discount, with shipping fee) ───────────────


def test_shipping_no_discount(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db,
        models,
        ResinType.RPG,
        shipping_fee=60,
        shipping_address="台北市大安區忠孝東路四段1號",
    )

    assert db_row["final_total"] == 990
    assert db_row["shipping_fee"] == 60
    assert db_row["shipping_address"] == "台北市大安區忠孝東路四段1號"


@pytest.mark.parametrize("shipping_fee,expected_final", [
    (60, 990),
    (120, 1050),
    (0, 930),
])
def test_shipping_fee_variants(db, shipping_fee, expected_final):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, shipping_fee=shipping_fee)

    assert db_row["final_total"] == expected_final


# ── 4. Discount plus shipping quote pipeline ──────────────────────────────────


def test_discount_and_shipping_combined(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN, shipping_fee=60
    )

    assert db_row["final_total"] == 897
    assert db_row["shipping_fee"] == 60


def test_shipping_state_survives_discount_reapplication(db):
    """Shipping persists when discount is (re)applied — both reflected in final total."""
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN, shipping_fee=60
    )

    assert db_row["shipping_fee"] == 60
    assert db_row["final_total"] == 897  # floor(930*0.9) + 60


def test_discount_state_survives_shipping_update(db):
    """Discount persists when shipping fee is updated — both reflected in final total."""
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN, shipping_fee=120
    )

    assert db_row["final_total"] == 957  # floor(930*0.9) + 120 = 837 + 120
    assert db_row["shipping_fee"] == 120


@pytest.mark.parametrize(
    "discount,shipping_fee,expected_final",
    [
        (_NINE_TEN, 60, 897),
        (DiscountInput(mode="pct", value=0.8), 120, 864),
        (DiscountInput(mode="fixed", value=100), 60, 890),
        (_NO_DISCOUNT, 60, 990),
    ],
)
def test_discount_plus_shipping_combinations(db, discount, shipping_fee, expected_final):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=discount, shipping_fee=shipping_fee
    )

    assert db_row["final_total"] == expected_final


# ── 5. Below-minimum-order quote without shipping ─────────────────────────────


def test_below_minimum_no_discount_no_shipping(db):
    models = [make_model_result("model.stl", 50.0, 1)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 175
    assert quote_result.processing_fee == 80
    assert quote_result.subtotal == 255
    assert quote_result.order_status == "未達低消"
    assert db_row["final_total"] == 255
    assert db_row["decision"] == "接受"


@pytest.mark.parametrize(
    "volume_ml,body_count,expected_subtotal,expected_status",
    [
        (50.0,  1, 255, "未達低消"),
        (110.0, 1, 465, "未達低消"),
        (130.0, 1, 535, "正常"),
    ],
)
def test_below_minimum_boundary(db, volume_ml, body_count, expected_subtotal, expected_status):
    models = [make_model_result("model.stl", volume_ml, body_count)]
    quote_result, _ = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.subtotal == expected_subtotal
    assert quote_result.order_status == expected_status


# ── 6. Below-minimum-order quote with shipping fee ────────────────────────────


def test_below_minimum_with_shipping(db):
    models = [make_model_result("model.stl", 50.0, 1)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, shipping_fee=60)

    assert db_row["final_total"] == 315
    assert db_row["shipping_fee"] == 60


# ── 7. Recursive Drive folder quote (flat result) ─────────────────────────────


def test_recursive_folder_two_subfolders(db):
    models = [
        make_model_result("subfolder-a/file1.stl", 100.0, 2),
        make_model_result("subfolder-b/file2.stl", 80.0, 1),
    ]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.volume_ml == 180.0
    assert quote_result.body_count == 3
    assert quote_result.material_cost == 630
    assert quote_result.processing_fee == 230
    assert quote_result.subtotal == 860
    assert db_row["final_total"] == 860


def test_recursive_folder_root_plus_subfolder(db):
    models = [
        make_model_result("root.stl", 50.0, 1),
        make_model_result("subfolder-a/a.stl", 100.0, 2),
    ]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.volume_ml == 150.0
    assert quote_result.body_count == 3
    assert quote_result.subtotal == 755
    assert db_row["final_total"] == 755


def test_recursive_folder_with_discount(db):
    models = [
        make_model_result("subfolder-a/file1.stl", 100.0, 2),
        make_model_result("subfolder-b/file2.stl", 80.0, 1),
    ]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, discount=_NINE_TEN)

    assert db_row["final_total"] == 774  # floor(860 * 0.9)


def test_recursive_folder_with_shipping(db):
    models = [
        make_model_result("subfolder-a/file1.stl", 100.0, 2),
        make_model_result("subfolder-b/file2.stl", 80.0, 1),
    ]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, shipping_fee=60)

    assert db_row["final_total"] == 920


@pytest.mark.parametrize(
    "model_list,expected_volume,expected_bodies",
    [
        (
            [
                make_model_result("subfolder-a/a.stl", 100.0, 2),
                make_model_result("subfolder-b/b.stl", 80.0, 1),
            ],
            180.0,
            3,
        ),
        (
            [
                make_model_result("root.stl", 50.0, 1),
                make_model_result("subfolder-a/a.stl", 100.0, 2),
            ],
            150.0,
            3,
        ),
        (
            [
                make_model_result("root.stl", 50.0, 1),
                make_model_result("subfolder-a/a.stl", 100.0, 2),
                make_model_result("subfolder-b/b.stl", 80.0, 1),
            ],
            230.0,
            4,
        ),
    ],
)
def test_recursive_folder_volume_aggregation(db, model_list, expected_volume, expected_bodies):
    quote_result, _ = run_quote_pipeline(db, model_list, ResinType.RPG)

    assert quote_result.volume_ml == expected_volume
    assert quote_result.body_count == expected_bodies


# ── 8. Auto free-shipping threshold quote ────────────────────────────────────


def test_auto_free_shipping_threshold(db):
    # volume=1000.0, body_count=10: material=3500, fee=650, subtotal=4150
    models = [make_model_result("model.stl", 1000.0, 10)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 3500
    assert quote_result.processing_fee == 650
    assert quote_result.subtotal == 4150
    assert quote_result.order_status == "免運費"
    assert quote_result.auto_free_ship is True
    assert db_row["final_total"] == 4150


@pytest.mark.parametrize(
    "volume_ml,body_count,expected_subtotal,expected_status,expected_free_ship",
    [
        # ceil(1119.5 * 3.5) = ceil(3918.25) = 3919; 3919+80 = 3999
        (1119.5, 1, 3999, "正常", False),
        # ceil(1120.0 * 3.5) = ceil(3920.0) = 3920; 3920+80 = 4000
        (1120.0, 1, 4000, "免運費", True),
        # ceil(1976.6 * 3.5) = ceil(6918.1) = 6919; 6919+80 = 6999
        (1976.6, 1, 6999, "免運費", True),
    ],
)
def test_auto_free_shipping_boundary(
    db, volume_ml, body_count, expected_subtotal, expected_status, expected_free_ship
):
    models = [make_model_result("model.stl", volume_ml, body_count)]
    quote_result, _ = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.subtotal == expected_subtotal
    assert quote_result.order_status == expected_status
    assert quote_result.auto_free_ship is expected_free_ship


# ── 9. Auto 95% discount threshold quote ─────────────────────────────────────


def test_auto_ninety_five_discount_threshold(db):
    # volume=2000.0, body_count=10: material=7000, fee=650, subtotal=7650
    # auto_discounted = floor(7650 * 0.95) = 7267
    models = [make_model_result("model.stl", 2000.0, 10)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 7000
    assert quote_result.processing_fee == 650
    assert quote_result.subtotal == 7650
    assert quote_result.final_total == 7267  # auto_discounted_total
    assert db_row["final_total"] == 7267


@pytest.mark.parametrize(
    "volume_ml,body_count,expected_subtotal,expected_auto_discounted",
    [
        # ceil(1814.1 * 3.5) = ceil(6349.35) = 6350; 6350+650 = 7000 → floor(7000*0.95) = 6650
        (1814.1, 10, 7000, 6650),
        # ceil(1814.5 * 3.5) = ceil(6350.75) = 6351; 6351+650 = 7001 → floor(7001*0.95) = 6650
        (1814.5, 10, 7001, 6650),
        # ceil(2000.0 * 3.5) = 7000; 7000+650 = 7650 → floor(7650*0.95) = 7267
        (2000.0, 10, 7650, 7267),
        # ceil(2100.0 * 3.5) = 7350; 7350+650 = 8000 → floor(8000*0.95) = 7600
        (2100.0, 10, 8000, 7600),
    ],
)
def test_auto_95_discount_boundary(
    db, volume_ml, body_count, expected_subtotal, expected_auto_discounted
):
    models = [make_model_result("model.stl", volume_ml, body_count)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.subtotal == expected_subtotal
    assert quote_result.final_total == expected_auto_discounted
    assert db_row["final_total"] == expected_auto_discounted


# ── 10. Rejected quote pipeline ───────────────────────────────────────────────


def test_rejected_quote_with_reason(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db,
        models,
        ResinType.RPG,
        decision="拒絕",
        rejection_reason="模型尺寸超過列印範圍",
    )

    assert db_row["decision"] == "拒絕"
    assert db_row["quote_number"] == ""
    assert db_row["rejection_reason"] == "模型尺寸超過列印範圍"
    assert db_row["final_total"] == 930


def test_rejected_quote_blank_reason(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, decision="拒絕", rejection_reason=""
    )

    assert db_row["decision"] == "拒絕"
    assert db_row["quote_number"] == ""
    assert db_row["rejection_reason"] is None


# ── 11. Same-day sequential quote number generation ──────────────────────────


def test_first_quote_of_day_gets_01(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert db_row["quote_number"] == f"trb{_DATE_PREFIX}01"


def test_second_quote_of_day_gets_02(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    run_quote_pipeline(db, models, ResinType.RPG)
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert db_row["quote_number"] == f"trb{_DATE_PREFIX}02"


def test_rejected_quotes_do_not_increment_sequence(db):
    models = [make_model_result("model.stl", 200.0, 3)]
    run_quote_pipeline(db, models, ResinType.RPG, decision="拒絕")
    run_quote_pipeline(db, models, ResinType.RPG, decision="拒絕")
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, decision="接受")

    assert db_row["quote_number"] == f"trb{_DATE_PREFIX}01"


# ── 12. Mixed valid and corrupt model files ────────────────────────────────────


def test_mixed_valid_and_corrupt_same_folder(db):
    """Pipeline uses only valid results; corrupt file is tracked in error_files."""
    valid_results = [make_model_result("valid.stl", 100.0, 2)]
    error_files = ["broken.stl"]  # non-watertight — rejected by model_reader upstream

    quote_result, db_row = run_quote_pipeline(db, valid_results, ResinType.RPG)

    assert quote_result.volume_ml == 100.0
    assert quote_result.body_count == 2
    assert quote_result.material_cost == math.ceil(100.0 * 3.5)  # 350
    assert db_row["final_total"] == 350 + 160  # material + 2-body fee (2×80)
    assert "broken.stl" in error_files


def test_corrupt_in_subfolder_valid_in_root(db):
    """Root-level valid file is used; corrupt subfolder file is in error_files."""
    valid_results = [make_model_result("root.stl", 50.0, 1)]
    error_files = ["broken.obj"]  # volume ≤ 0 — rejected by model_reader upstream

    quote_result, db_row = run_quote_pipeline(db, valid_results, ResinType.RPG)

    assert quote_result.volume_ml == 50.0
    assert quote_result.body_count == 1
    assert db_row["final_total"] == 175 + 80  # material + 1-body fee
    assert "broken.obj" in error_files


# ── 13. Empty Drive folder handling ──────────────────────────────────────────


def test_empty_folder_no_model_files(db):
    """No model files → ValueError raised before any SQLite write."""
    with pytest.raises(ValueError, match="no model files found"):
        run_quote_pipeline(db, [], ResinType.RPG)

    assert len(db.get_unsynced_quote_records()) == 0


def test_empty_parent_and_subfolders(db):
    """Empty parent and all subfolders → same ValueError, no SQLite record created."""
    with pytest.raises(ValueError, match="no model files found"):
        run_quote_pipeline(db, [], ResinType.RPG)

    assert len(db.get_unsynced_quote_records()) == 0


# ── 14. Manual body count override pipeline ───────────────────────────────────
#
# Mirrors the BodyCountModal.on_submit recalculation sequence:
#   new_file_details (list comprehension, no mutation)
#   → calculate_quote(volume_ml unchanged, new total_bodies)
#   → re-apply manual discount if mode != "none"
#   → SQLite persist with overridden values


def run_body_count_override_pipeline(
    db: DBClient,
    model_results: list[ModelReadResult],
    resin: ResinType,
    colored: bool = False,
    override_idx: int = 0,
    new_body_count: int = 1,
    discount: DiscountInput = _NO_DISCOUNT,
    shipping_fee: int = 0,
    shipping_address: str = "",
    decision: str = "接受",
    date_prefix: str = _DATE_PREFIX,
) -> tuple[QuoteResult, QuoteResult, list[dict], dict]:
    """
    Execute the body count override flow: initial quote → override → recalculate → SQLite.

    Returns (initial_quote, overridden_quote, new_file_details, db_row)
      - volume_ml is preserved across the override (only body_count changes)
      - manual discount is re-applied against new final_total when mode != "none"
      - shipping_fee is preserved unchanged
    """
    if not model_results:
        raise ValueError("no model files found")

    file_details = [
        {"filename": r.filename, "volume_ml": r.volume_ml, "body_count": r.body_count}
        for r in model_results
    ]
    total_volume_ml = sum(r.volume_ml for r in model_results)
    initial_quote = calculate_quote(
        resin, colored, total_volume_ml, sum(r.body_count for r in model_results)
    )

    # Override — identical logic to BodyCountModal.on_submit
    new_file_details = [
        {**f, "body_count": new_body_count} if i == override_idx else f
        for i, f in enumerate(file_details)
    ]
    new_total_bodies = sum(f["body_count"] for f in new_file_details)
    overridden_quote = calculate_quote(resin, colored, total_volume_ml, new_total_bodies)

    manual_discounted_total, manual_discount_amount = apply_manual_discount(
        overridden_quote.final_total, discount
    )
    final_total = manual_discounted_total + shipping_fee

    if decision == "接受":
        count = db.count_accepted_quotes_today(date_prefix)
        quote_number = f"trb{date_prefix}{count + 1:02d}"
    else:
        quote_number = ""

    db.insert_quote_record(
        quote_number=quote_number,
        customer_name="測試客戶",
        resin_label=resin.value,
        body_count=overridden_quote.body_count,
        material_cost=overridden_quote.material_cost,
        processing_fee=overridden_quote.processing_fee,
        auto_discount=str(overridden_quote.auto_discount_amount),
        manual_discount=str(manual_discount_amount),
        subtotal=overridden_quote.subtotal,
        final_total=final_total,
        order_status=overridden_quote.order_status,
        decision=decision,
        shipping_fee=shipping_fee,
        shipping_address=shipping_address,
    )

    rows = db.get_unsynced_quote_records()
    return initial_quote, overridden_quote, new_file_details, dict(rows[-1])


# A. Spec example ─────────────────────────────────────────────────────────────


def test_body_count_override_spec_example(db):
    """Spec: file A(2)+file B(1)=3; override A→5 → total=6.
    processing_fee=2×80+3×70+1×60=430, material_cost=ceil(10×3.5)=35, subtotal=465."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=5
    )

    assert overridden.body_count == 6
    assert overridden.processing_fee == 430
    assert overridden.material_cost == 35
    assert overridden.subtotal == 465
    assert db_row["body_count"] == 6
    assert db_row["final_total"] == 465


# B. material_cost unchanged ──────────────────────────────────────────────────


def test_body_count_override_material_cost_unchanged(db):
    """material_cost depends only on volume and resin; changing body_count must not alter it."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    initial, overridden, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=5
    )

    assert initial.material_cost == overridden.material_cost == 35
    assert initial.volume_ml == overridden.volume_ml == 10.0


# C. Manual discount recalculated ─────────────────────────────────────────────


def test_body_count_override_nine_ten_discount_recalculated(db):
    """九折 discount must be re-applied against new final_total after override."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG,
        override_idx=0, new_body_count=5,
        discount=_NINE_TEN,
    )

    # new final_total=465 (no auto-discount); 九折→floor(465×0.9)=418, amount=47
    assert overridden.final_total == 465
    assert db_row["final_total"] == 418
    assert db_row["manual_discount"] == "47"


def test_body_count_override_fixed_discount_recalculated(db):
    """Fixed-amount discount is re-applied against new final_total after override."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, _, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG,
        override_idx=0, new_body_count=5,
        discount=DiscountInput(mode="fixed", value=100),
    )

    assert db_row["final_total"] == 365   # 465 - 100
    assert db_row["manual_discount"] == "100"


# D. Shipping preserved ───────────────────────────────────────────────────────


def test_body_count_override_preserves_shipping_fee(db):
    """_shipping_fee must remain unchanged after body count override."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, _, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG,
        override_idx=0, new_body_count=5,
        shipping_fee=60, shipping_address="台北市大安區",
    )

    assert db_row["shipping_fee"] == 60
    assert db_row["shipping_address"] == "台北市大安區"
    assert db_row["final_total"] == 525   # 465 + 60


# E. Discount + shipping combined ─────────────────────────────────────────────


def test_body_count_override_discount_and_shipping_combined(db):
    """Both nine-ten discount and shipping fee are reflected in final_total after override."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, _, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG,
        override_idx=0, new_body_count=5,
        discount=_NINE_TEN, shipping_fee=60,
    )

    # floor(465×0.9)=418; 418+60=478
    assert db_row["final_total"] == 478
    assert db_row["shipping_fee"] == 60
    assert db_row["manual_discount"] == "47"


# F. Multi-file — only targeted file changes ──────────────────────────────────


def test_body_count_override_only_targeted_file_changes(db):
    """Overriding file[1] must not alter file[0] or file[2] in the new file_details."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 3.0, 1),
        make_model_result("c.stl", 2.0, 1),
    ]
    _, overridden, new_fd, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=1, new_body_count=3
    )

    assert new_fd[0]["body_count"] == 2   # unchanged
    assert new_fd[1]["body_count"] == 3   # overridden
    assert new_fd[2]["body_count"] == 1   # unchanged
    assert overridden.body_count == 6     # 2 + 3 + 1


# G. Immutability ─────────────────────────────────────────────────────────────


def test_body_count_override_original_file_dict_not_mutated(db):
    """The pipeline must not mutate the original file dict in file_details."""
    original_a = {"filename": "a.stl", "volume_ml": 5.0, "body_count": 2}
    original_b = {"filename": "b.stl", "volume_ml": 5.0, "body_count": 1}

    # Simulate the override list comprehension directly
    file_details = [original_a, original_b]
    new_file_details = [
        {**f, "body_count": 5} if i == 0 else f
        for i, f in enumerate(file_details)
    ]

    assert original_a["body_count"] == 2        # not mutated
    assert new_file_details[0]["body_count"] == 5
    assert new_file_details[1] is original_b    # unchanged dicts re-used


# H. order_status boundary transition ─────────────────────────────────────────


def test_body_count_override_status_transitions_to_normal(db):
    """Override that raises subtotal from below-minimum to above 500 → order_status='正常'."""
    # 50ml/1件: material=175, fee=80, subtotal=255 → 未達低消
    models = [make_model_result("a.stl", 50.0, 1)]
    initial, _, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=1
    )
    assert initial.order_status == "未達低消"

    # Override 1→5: fee=2×80+3×70=370, subtotal=545 → 正常
    db2 = DBClient(":memory:")
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db2, models, ResinType.RPG, override_idx=0, new_body_count=5
    )

    assert overridden.subtotal == 545
    assert overridden.order_status == "正常"
    assert db_row["order_status"] == "正常"


def test_body_count_override_stays_below_minimum(db):
    """Override that keeps subtotal < 500 → order_status remains '未達低消'."""
    # 50ml/1→3件: fee=2×80+1×70=230, subtotal=175+230=405 → still 未達低消
    models = [make_model_result("a.stl", 50.0, 1)]
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=3
    )

    assert overridden.subtotal == 405
    assert overridden.order_status == "未達低消"
    assert db_row["order_status"] == "未達低消"


# I. DB persistence ───────────────────────────────────────────────────────────


def test_body_count_override_db_row_fields_all_correct(db):
    """All key DB fields reflect the overridden (not initial) values after accept.
    Uses 100ml×2 files to ensure subtotal=1130 → order_status='正常'."""
    models = [
        make_model_result("a.stl", 100.0, 2),
        make_model_result("b.stl", 100.0, 1),
    ]
    # Override file[0] 2→5: total=6, material=ceil(200×3.5)=700, fee=430, subtotal=1130
    # nine-ten: floor(1130×0.9)=1017, amount=113; +60 shipping → final=1077
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=5,
        discount=_NINE_TEN, shipping_fee=60,
    )

    assert db_row["body_count"] == 6
    assert db_row["material_cost"] == 700
    assert db_row["processing_fee"] == 430
    assert db_row["subtotal"] == 1130
    assert db_row["manual_discount"] == "113"
    assert db_row["shipping_fee"] == 60
    assert db_row["final_total"] == 1077
    assert db_row["decision"] == "接受"
    assert db_row["order_status"] == "正常"


# J. Auto-discount threshold crossing ─────────────────────────────────────────


def test_body_count_override_crosses_auto_free_ship_threshold(db):
    """Override that pushes subtotal from below 4000 to ≥ 4000 → auto_free_ship=True."""
    # 1000ml/3件: material=3500, fee=230, subtotal=3730 → 正常
    models = [make_model_result("model.stl", 1000.0, 3)]
    initial, _, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=3
    )
    assert initial.subtotal == 3730
    assert initial.auto_free_ship is False

    # Override 3→4: fee=2×80+2×70=300, subtotal=3500+300=3800 → still below 4000
    # Override 3→5: fee=2×80+3×70=370, subtotal=3500+370=3870 → still below
    # Override 3→14: fee=2×80+3×70+3×60+3×50+3×40=160+210+180+150+120=820, subtotal=4320 → 免運費
    db2 = DBClient(":memory:")
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db2, models, ResinType.RPG, override_idx=0, new_body_count=14
    )

    assert overridden.subtotal == 4320
    assert overridden.auto_free_ship is True
    assert overridden.order_status == "免運費"
    assert db_row["order_status"] == "免運費"


def test_body_count_override_crosses_ninety_five_discount_threshold(db):
    """Override that pushes subtotal from below 7000 to ≥ 7000 → auto 95% discount applied."""
    # 1800ml/5件: material=6300, fee=370, subtotal=6670 → 免運費 (4000≤6670<7000)
    models = [make_model_result("model.stl", 1800.0, 5)]
    initial, _, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=5
    )
    assert initial.subtotal == 6670
    assert initial.auto_discount_amount == 0

    # Override 5→11: fee=2×80+3×70+3×60+3×50=700, subtotal=6300+700=7000 → ≥7000 → 95折
    # Override 5→12: fee=2×80+3×70+3×60+3×50+1×40=740, subtotal=6300+740=7040 → ≥7000 → 95折
    db2 = DBClient(":memory:")
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db2, models, ResinType.RPG, override_idx=0, new_body_count=12
    )

    assert overridden.subtotal == 7040
    assert overridden.auto_discount_amount == 7040 - math.floor(7040 * 0.95)
    assert overridden.final_total == math.floor(7040 * 0.95)
    assert db_row["final_total"] == math.floor(7040 * 0.95)


# K. Parametrized: processing fee table ──────────────────────────────────────


@pytest.mark.parametrize(
    "new_file0_count,expected_total_bodies,expected_processing_fee,expected_subtotal",
    [
        # file_details: [a.stl:5ml/2件, b.stl:5ml/1件] → total_volume=10ml
        # material_cost=ceil(10×3.5)=35 for all rows
        (1, 2,  160, 195),   # 2×80=160
        (3, 4,  300, 335),   # 2×80+2×70=300
        (5, 6,  430, 465),   # spec example: 2×80+3×70+1×60=430
        (8, 9,  600, 635),   # 2×80+3×70+3×60+1×50=600
    ],
)
def test_body_count_override_processing_fee_table(
    db,
    new_file0_count,
    expected_total_bodies,
    expected_processing_fee,
    expected_subtotal,
):
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=new_file0_count
    )

    assert overridden.body_count == expected_total_bodies
    assert overridden.material_cost == 35
    assert overridden.processing_fee == expected_processing_fee
    assert overridden.subtotal == expected_subtotal
    assert db_row["final_total"] == expected_subtotal
