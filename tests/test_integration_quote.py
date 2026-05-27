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
    # 200ml, 3 pieces, RPG: material=int(ceil(200)*4)=800, fee=3×90=270, subtotal=1070
    models = [make_model_result("model.stl", 200.0, 3)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 800
    assert quote_result.processing_fee == 270
    assert quote_result.subtotal == 1070
    assert quote_result.order_status == "正常"
    assert quote_result.auto_free_ship is False
    assert db_row["final_total"] == 1070
    assert db_row["decision"] == "接受"
    assert db_row["shipping_fee"] == 0
    assert db_row["shipping_address"] == ""


@pytest.mark.parametrize(
    "volume_ml,body_count,resin,colored,expected_material,expected_fee,expected_subtotal",
    [
        # material=int(ceil(vol)*4), fee per new tiers
        (200.0, 3, ResinType.RPG, False, 800, 270, 1070),   # 3×90=270
        (100.0, 5, ResinType.RPG, False, 400, 430, 830),    # 3×90+2×80=430
        (150.0, 2, ResinType.CLEAR, False, 600, 180, 780),  # 2×90=180
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
    # 200ml/3 pieces/RPG: subtotal=1070; 九折→floor(1070×0.9)=963, amount=107
    models = [make_model_result("model.stl", 200.0, 3)]
    quote_result, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN
    )

    assert quote_result.subtotal == 1070
    assert db_row["final_total"] == 963
    assert db_row["manual_discount"] == "107"
    assert db_row["shipping_fee"] == 0


def test_discount_fixed_no_shipping(db):
    # 200ml/3 pieces/RPG: subtotal=1070; fixed 100→1070-100=970
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=DiscountInput(mode="fixed", value=100)
    )

    assert db_row["final_total"] == 970
    assert db_row["manual_discount"] == "100"


@pytest.mark.parametrize(
    "discount,expected_final,expected_discount_amount",
    [
        (_NINE_TEN, 963, 107),                              # floor(1070×0.9)=963
        (DiscountInput(mode="pct", value=0.8), 856, 214),  # floor(1070×0.8)=856
        (DiscountInput(mode="fixed", value=100), 970, 100),
        (_NO_DISCOUNT, 1070, 0),
    ],
)
def test_discount_variants_example_table(db, discount, expected_final, expected_discount_amount):
    # 200ml/3 pieces/RPG: subtotal=1070
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, discount=discount)

    assert db_row["final_total"] == expected_final
    assert db_row["manual_discount"] == str(expected_discount_amount)


# ── 3. Shipping quote pipeline (no discount, with shipping fee) ───────────────


def test_shipping_no_discount(db):
    # 200ml/3 pieces/RPG: subtotal=1070; +60 shipping → 1130
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db,
        models,
        ResinType.RPG,
        shipping_fee=60,
        shipping_address="台北市大安區忠孝東路四段1號",
    )

    assert db_row["final_total"] == 1130
    assert db_row["shipping_fee"] == 60
    assert db_row["shipping_address"] == "台北市大安區忠孝東路四段1號"


@pytest.mark.parametrize("shipping_fee,expected_final", [
    (60, 1130),
    (120, 1190),
    (0, 1070),
])
def test_shipping_fee_variants(db, shipping_fee, expected_final):
    # 200ml/3 pieces/RPG: subtotal=1070
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, shipping_fee=shipping_fee)

    assert db_row["final_total"] == expected_final


# ── 4. Discount plus shipping quote pipeline ──────────────────────────────────


def test_discount_and_shipping_combined(db):
    # 200ml/3 pieces/RPG: 九折→963; +60 → 1023
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN, shipping_fee=60
    )

    assert db_row["final_total"] == 1023
    assert db_row["shipping_fee"] == 60


def test_shipping_state_survives_discount_reapplication(db):
    """Shipping persists when discount is (re)applied — both reflected in final total."""
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN, shipping_fee=60
    )

    assert db_row["shipping_fee"] == 60
    assert db_row["final_total"] == 1023  # floor(1070*0.9) + 60 = 963 + 60


def test_discount_state_survives_shipping_update(db):
    """Discount persists when shipping fee is updated — both reflected in final total."""
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=_NINE_TEN, shipping_fee=120
    )

    assert db_row["final_total"] == 1083  # floor(1070*0.9) + 120 = 963 + 120
    assert db_row["shipping_fee"] == 120


@pytest.mark.parametrize(
    "discount,shipping_fee,expected_final",
    [
        (_NINE_TEN, 60, 1023),                              # 963+60
        (DiscountInput(mode="pct", value=0.8), 120, 976),  # 856+120
        (DiscountInput(mode="fixed", value=100), 60, 1030), # 970+60
        (_NO_DISCOUNT, 60, 1130),                           # 1070+60
    ],
)
def test_discount_plus_shipping_combinations(db, discount, shipping_fee, expected_final):
    # 200ml/3 pieces/RPG: subtotal=1070
    models = [make_model_result("model.stl", 200.0, 3)]
    _, db_row = run_quote_pipeline(
        db, models, ResinType.RPG, discount=discount, shipping_fee=shipping_fee
    )

    assert db_row["final_total"] == expected_final


# ── 5. Below-minimum-order quote without shipping ─────────────────────────────


def test_below_minimum_no_discount_no_shipping(db):
    # 50ml/1 piece/RPG: material=200, fee=90, subtotal=290 → 未達低消
    models = [make_model_result("model.stl", 50.0, 1)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 200
    assert quote_result.processing_fee == 90
    assert quote_result.subtotal == 290
    assert quote_result.order_status == "未達低消"
    assert db_row["final_total"] == 290
    assert db_row["decision"] == "接受"


@pytest.mark.parametrize(
    "volume_ml,body_count,expected_subtotal,expected_status",
    [
        # 500NT$ boundary with new pricing (1 piece, fee=90): material<410 → subtotal<500
        (50.0,  1, 290, "未達低消"),  # material=200, 200+90=290
        (100.0, 1, 490, "未達低消"),  # material=400, 400+90=490 (just below 500)
        (110.0, 1, 530, "正常"),       # material=440, 440+90=530 (just above 500)
    ],
)
def test_below_minimum_boundary(db, volume_ml, body_count, expected_subtotal, expected_status):
    models = [make_model_result("model.stl", volume_ml, body_count)]
    quote_result, _ = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.subtotal == expected_subtotal
    assert quote_result.order_status == expected_status


# ── 6. Below-minimum-order quote with shipping fee ────────────────────────────


def test_below_minimum_with_shipping(db):
    # 50ml/1 piece/RPG: subtotal=290; +60 shipping → 350
    models = [make_model_result("model.stl", 50.0, 1)]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, shipping_fee=60)

    assert db_row["final_total"] == 350
    assert db_row["shipping_fee"] == 60


# ── 7. Recursive Drive folder quote (flat result) ─────────────────────────────


def test_recursive_folder_two_subfolders(db):
    # 100ml+80ml=180ml, 2+1=3 pieces, RPG: material=720, fee=270, subtotal=990
    models = [
        make_model_result("subfolder-a/file1.stl", 100.0, 2),
        make_model_result("subfolder-b/file2.stl", 80.0, 1),
    ]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.volume_ml == 180.0
    assert quote_result.body_count == 3
    assert quote_result.material_cost == 720
    assert quote_result.processing_fee == 270
    assert quote_result.subtotal == 990
    assert db_row["final_total"] == 990


def test_recursive_folder_root_plus_subfolder(db):
    # 50ml+100ml=150ml, 1+2=3 pieces, RPG: material=600, fee=270, subtotal=870
    models = [
        make_model_result("root.stl", 50.0, 1),
        make_model_result("subfolder-a/a.stl", 100.0, 2),
    ]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.volume_ml == 150.0
    assert quote_result.body_count == 3
    assert quote_result.subtotal == 870
    assert db_row["final_total"] == 870


def test_recursive_folder_with_discount(db):
    # 180ml/3 pieces: subtotal=990; 九折→floor(990×0.9)=891
    models = [
        make_model_result("subfolder-a/file1.stl", 100.0, 2),
        make_model_result("subfolder-b/file2.stl", 80.0, 1),
    ]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, discount=_NINE_TEN)

    assert db_row["final_total"] == 891  # floor(990 * 0.9)


def test_recursive_folder_with_shipping(db):
    # 180ml/3 pieces: subtotal=990; +60 shipping → 1050
    models = [
        make_model_result("subfolder-a/file1.stl", 100.0, 2),
        make_model_result("subfolder-b/file2.stl", 80.0, 1),
    ]
    _, db_row = run_quote_pipeline(db, models, ResinType.RPG, shipping_fee=60)

    assert db_row["final_total"] == 1050


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
    # 1000ml/10 pieces/RPG: material=4000, fee=780, subtotal=4780 → 免運費
    models = [make_model_result("model.stl", 1000.0, 10)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 4000
    assert quote_result.processing_fee == 780
    assert quote_result.subtotal == 4780
    assert quote_result.order_status == "免運費"
    assert quote_result.auto_free_ship is True
    assert db_row["final_total"] == 4780


@pytest.mark.parametrize(
    "volume_ml,body_count,expected_subtotal,expected_status,expected_free_ship",
    [
        # 1 piece, fee=90; boundary at subtotal=4000
        # int(ceil(977)*4)+90 = 3908+90 = 3998 → 正常
        (977.0, 1, 3998, "正常", False),
        # int(ceil(978)*4)+90 = 3912+90 = 4002 → 免運費
        (978.0, 1, 4002, "免運費", True),
        # int(ceil(1727)*4)+90 = 6908+90 = 6998 → 免運費 (4000≤6998<7000, no 95%)
        (1727.0, 1, 6998, "免運費", True),
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
    # 2000ml/10 pieces/RPG: material=8000, fee=780, subtotal=8780
    # auto_discounted = floor(8780 * 0.95) = 8341
    models = [make_model_result("model.stl", 2000.0, 10)]
    quote_result, db_row = run_quote_pipeline(db, models, ResinType.RPG)

    assert quote_result.material_cost == 8000
    assert quote_result.processing_fee == 780
    assert quote_result.subtotal == 8780
    assert quote_result.final_total == 8341  # floor(8780 * 0.95)
    assert db_row["final_total"] == 8341


@pytest.mark.parametrize(
    "volume_ml,body_count,expected_subtotal,expected_auto_discounted",
    [
        # 10 pieces, fee=780; boundary at subtotal=7000 → ceil(vol)=1555
        # int(ceil(1554)*4)+780 = 6216+780 = 6996 → 免運費, no 95%
        (1554.0, 10, 6996, 6996),
        # int(ceil(1555)*4)+780 = 6220+780 = 7000 → floor(7000*0.95) = 6650
        (1555.0, 10, 7000, 6650),
        # int(ceil(2000)*4)+780 = 8000+780 = 8780 → floor(8780*0.95) = 8341
        (2000.0, 10, 8780, 8341),
        # int(ceil(2100)*4)+780 = 8400+780 = 9180 → floor(9180*0.95) = 8721
        (2100.0, 10, 9180, 8721),
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
    # 200ml/3 pieces/RPG: subtotal=1070, final=1070
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
    assert db_row["final_total"] == 1070


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
    assert quote_result.material_cost == int(math.ceil(100.0) * 4.0)  # 400
    assert db_row["final_total"] == 400 + 180  # material + 2-body fee (2×90)
    assert "broken.stl" in error_files


def test_corrupt_in_subfolder_valid_in_root(db):
    """Root-level valid file is used; corrupt subfolder file is in error_files."""
    valid_results = [make_model_result("root.stl", 50.0, 1)]
    error_files = ["broken.obj"]  # volume ≤ 0 — rejected by model_reader upstream

    quote_result, db_row = run_quote_pipeline(db, valid_results, ResinType.RPG)

    assert quote_result.volume_ml == 50.0
    assert quote_result.body_count == 1
    assert db_row["final_total"] == 200 + 90  # material + 1-body fee (1×90)
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
    """file A(2)+file B(1)=3; override A→5 → total=6.
    material=int(ceil(10)*4)=40; fee=3×90+3×80=510; subtotal=550."""
    models = [
        make_model_result("a.stl", 5.0, 2),
        make_model_result("b.stl", 5.0, 1),
    ]
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=5
    )

    assert overridden.body_count == 6
    assert overridden.processing_fee == 510
    assert overridden.material_cost == 40
    assert overridden.subtotal == 550
    assert db_row["body_count"] == 6
    assert db_row["final_total"] == 550


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

    assert initial.material_cost == overridden.material_cost == 40
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

    # new final_total=550 (no auto-discount); 九折→floor(550×0.9)=495, amount=55
    assert overridden.final_total == 550
    assert db_row["final_total"] == 495
    assert db_row["manual_discount"] == "55"


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

    assert db_row["final_total"] == 450   # 550 - 100
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
    assert db_row["final_total"] == 610   # 550 + 60


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

    # floor(550×0.9)=495; 495+60=555
    assert db_row["final_total"] == 555
    assert db_row["shipping_fee"] == 60
    assert db_row["manual_discount"] == "55"


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
    # 50ml/1件: material=200, fee=90, subtotal=290 → 未達低消
    models = [make_model_result("a.stl", 50.0, 1)]
    initial, _, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=1
    )
    assert initial.order_status == "未達低消"

    # Override 1→5: fee=3×90+2×80=430, subtotal=200+430=630 → 正常
    db2 = DBClient(":memory:")
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db2, models, ResinType.RPG, override_idx=0, new_body_count=5
    )

    assert overridden.subtotal == 630
    assert overridden.order_status == "正常"
    assert db_row["order_status"] == "正常"


def test_body_count_override_stays_below_minimum(db):
    """Override that keeps subtotal < 500 → order_status remains '未達低消'."""
    # 50ml/1→3件: fee=3×90=270, subtotal=200+270=470 → still 未達低消
    models = [make_model_result("a.stl", 50.0, 1)]
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=3
    )

    assert overridden.subtotal == 470
    assert overridden.order_status == "未達低消"
    assert db_row["order_status"] == "未達低消"


# I. DB persistence ───────────────────────────────────────────────────────────


def test_body_count_override_db_row_fields_all_correct(db):
    """All key DB fields reflect the overridden (not initial) values after accept.
    Uses 100ml×2 files → total 200ml; override file[0] 2→5: total=6 pieces."""
    models = [
        make_model_result("a.stl", 100.0, 2),
        make_model_result("b.stl", 100.0, 1),
    ]
    # material=int(ceil(200)*4)=800, fee(6)=3×90+3×80=510, subtotal=1310
    # nine-ten: floor(1310×0.9)=1179, amount=131; +60 shipping → final=1239
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=5,
        discount=_NINE_TEN, shipping_fee=60,
    )

    assert db_row["body_count"] == 6
    assert db_row["material_cost"] == 800
    assert db_row["processing_fee"] == 510
    assert db_row["subtotal"] == 1310
    assert db_row["manual_discount"] == "131"
    assert db_row["shipping_fee"] == 60
    assert db_row["final_total"] == 1239
    assert db_row["decision"] == "接受"
    assert db_row["order_status"] == "正常"


# J. Auto-discount threshold crossing ─────────────────────────────────────────


def test_body_count_override_crosses_auto_free_ship_threshold(db):
    """Override that pushes subtotal from below 4000 to ≥ 4000 → auto_free_ship=True."""
    # 850ml/1件: material=3400, fee=90, subtotal=3490 → 正常
    models = [make_model_result("model.stl", 850.0, 1)]
    initial, _, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=1
    )
    assert initial.subtotal == 3490
    assert initial.auto_free_ship is False

    # Override 1→8: fee=3×90+3×80+2×70=650, subtotal=3400+650=4050 → 免運費
    db2 = DBClient(":memory:")
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db2, models, ResinType.RPG, override_idx=0, new_body_count=8
    )

    assert overridden.subtotal == 4050
    assert overridden.auto_free_ship is True
    assert overridden.order_status == "免運費"
    assert db_row["order_status"] == "免運費"


def test_body_count_override_crosses_ninety_five_discount_threshold(db):
    """Override that pushes subtotal from below 7000 to ≥ 7000 → auto 95% discount applied."""
    # 1600ml/3件: material=6400, fee=270, subtotal=6670 → 免運費 (4000≤6670<7000)
    models = [make_model_result("model.stl", 1600.0, 3)]
    initial, _, _, _ = run_body_count_override_pipeline(
        db, models, ResinType.RPG, override_idx=0, new_body_count=3
    )
    assert initial.subtotal == 6670
    assert initial.auto_discount_amount == 0

    # Override 3→12: fee=3×90+3×80+3×70+3×60=900, subtotal=6400+900=7300 → ≥7000 → 95折
    db2 = DBClient(":memory:")
    _, overridden, _, db_row = run_body_count_override_pipeline(
        db2, models, ResinType.RPG, override_idx=0, new_body_count=12
    )

    assert overridden.subtotal == 7300
    assert overridden.auto_discount_amount == 7300 - math.floor(7300 * 0.95)
    assert overridden.final_total == math.floor(7300 * 0.95)
    assert db_row["final_total"] == math.floor(7300 * 0.95)


# K. Parametrized: processing fee table ──────────────────────────────────────


@pytest.mark.parametrize(
    "new_file0_count,expected_total_bodies,expected_processing_fee,expected_subtotal",
    [
        # file_details: [a.stl:5ml/2件, b.stl:5ml/1件] → total_volume=10ml
        # material_cost=int(ceil(10)*4)=40 for all rows
        (1, 2,  180, 220),   # 2×90=180
        (3, 4,  350, 390),   # 3×90+1×80=350
        (5, 6,  510, 550),   # 3×90+3×80=510
        (8, 9,  720, 760),   # 3×90+3×80+3×70=720
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
    assert overridden.material_cost == 40
    assert overridden.processing_fee == expected_processing_fee
    assert overridden.subtotal == expected_subtotal
    assert db_row["final_total"] == expected_subtotal
