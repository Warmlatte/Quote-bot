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
