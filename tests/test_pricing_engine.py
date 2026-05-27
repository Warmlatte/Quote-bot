import math
import pytest
from bot.pricing.engine import (
    ResinType,
    calculate_material_cost,
    calculate_processing_fee,
    apply_auto_discounts,
    calculate_quote,
    QuoteResult,
    DiscountInput,
    apply_manual_discount,
)


# ── 材料費 ────────────────────────────────────────────────────────────────────

class TestCalculateMaterialCost:
    @pytest.mark.parametrize("volume_ml,resin,colored,expected", [
        (100.0, ResinType.RPG,   False, 400),  # int(ceil(100.0) * 4.0)
        (10.3,  ResinType.RPG,   False,  44),  # int(ceil(10.3) * 4.0) = int(11 * 4.0)
        (100.0, ResinType.CLEAR, False, 400),  # int(ceil(100.0) * 4.0)
        (100.0, ResinType.CLEAR, True,  800),  # int(ceil(100.0) * 8.0)
        (5.1,   ResinType.CLEAR, True,   48),  # int(ceil(5.1) * 8.0) = int(6 * 8.0)
    ])
    def test_material_cost_boundary_cases(self, volume_ml, resin, colored, expected):
        assert calculate_material_cost(resin, volume_ml, colored=colored) == expected


# ── 加工費 ────────────────────────────────────────────────────────────────────

class TestCalculateProcessingFee:
    @pytest.mark.parametrize("count,expected", [
        (1,  90),    # 1×90
        (3,  270),   # 3×90
        (4,  350),   # 3×90 + 1×80
        (6,  510),   # 3×90 + 3×80
        (7,  580),   # 3×90 + 3×80 + 1×70
        (9,  720),   # 3×90 + 3×80 + 3×70
        (10, 780),   # 3×90 + 3×80 + 3×70 + 1×60
        (12, 900),   # 3×90 + 3×80 + 3×70 + 3×60
        (13, 950),   # 3×90 + 3×80 + 3×70 + 3×60 + 1×50
        (15, 1050),  # 3×90 + 3×80 + 3×70 + 3×60 + 3×50
    ])
    def test_boundary_values(self, count, expected):
        assert calculate_processing_fee(count) == expected


# ── 自動折扣 ──────────────────────────────────────────────────────────────────

class TestApplyAutoDiscounts:
    def test_below_minimum(self):
        total, free_ship, status = apply_auto_discounts(400)
        assert total == 400
        assert free_ship is False
        assert status == "未達低消"

    def test_normal_range(self):
        total, free_ship, status = apply_auto_discounts(1000)
        assert total == 1000
        assert free_ship is False
        assert status == "正常"

    def test_free_shipping_threshold(self):
        total, free_ship, status = apply_auto_discounts(4000)
        assert total == 4000
        assert free_ship is True
        assert status == "免運費"

    def test_ninety_five_percent_exact_boundary(self):
        # floor(7000 * 0.95) = 6650
        total, free_ship, status = apply_auto_discounts(7000)
        assert total == 6650
        assert free_ship is True
        assert status == "免運費"

    def test_ninety_five_percent_above_boundary(self):
        # floor(7001 * 0.95) = floor(6650.95) = 6650
        total, free_ship, status = apply_auto_discounts(7001)
        assert total == 6650
        assert free_ship is True
        assert status == "免運費"



# ── 完整計價 Pipeline ──────────────────────────────────────────────────────────

class TestCalculateQuote:
    def test_normal_order(self):
        # RPG, 100.0 ml, 3 件
        # material = int(ceil(100.0) * 4.0) = 400
        # processing = 3×90 = 270
        # subtotal = 670 → 正常 (500 ≤ 670 < 4000)
        result = calculate_quote(ResinType.RPG, colored=False, volume_ml=100.0, body_count=3)

        assert isinstance(result, QuoteResult)
        assert result.material_cost == 400
        assert result.processing_fee == 270
        assert result.subtotal == 670
        assert result.auto_discount_amount == 0
        assert result.auto_free_ship is False
        assert result.order_status == "正常"
        assert result.final_total == 670

    def test_order_triggers_ninety_five_percent(self):
        # Clear colored, 1000 ml, 10 件
        # material = int(ceil(1000.0) * 8.0) = 8000
        # processing = 3×90 + 3×80 + 3×70 + 1×60 = 780
        # subtotal = 8780 → floor(8780 * 0.95) = 8341
        result = calculate_quote(ResinType.CLEAR, colored=True, volume_ml=1000.0, body_count=10)

        assert result.material_cost == 8000
        assert result.processing_fee == 780
        assert result.subtotal == 8780
        assert result.auto_free_ship is True
        assert result.order_status == "免運費"
        assert result.final_total == math.floor(8780 * 0.95)

    def test_result_fields_present(self):
        result = calculate_quote(ResinType.RPG, colored=False, volume_ml=5.0, body_count=1)
        assert hasattr(result, "resin")
        assert hasattr(result, "colored")
        assert hasattr(result, "volume_ml")
        assert hasattr(result, "body_count")
        assert hasattr(result, "material_cost")
        assert hasattr(result, "processing_fee")
        assert hasattr(result, "subtotal")
        assert hasattr(result, "auto_discount_amount")
        assert hasattr(result, "auto_free_ship")
        assert hasattr(result, "order_status")
        assert hasattr(result, "final_total")


# ── 新手動折扣（DiscountInput）────────────────────────────────────────────────

class TestDiscountInput:
    def test_pct_mode_fields(self):
        d = DiscountInput(mode="pct", value=0.9)
        assert d.mode == "pct"
        assert d.value == 0.9

    def test_fixed_mode_fields(self):
        d = DiscountInput(mode="fixed", value=100)
        assert d.mode == "fixed"
        assert d.value == 100

    def test_none_mode_fields(self):
        d = DiscountInput(mode="none", value=0)
        assert d.mode == "none"
        assert d.value == 0


class TestApplyManualDiscount:
    @pytest.mark.parametrize("mode,value,base_total,expected_new,expected_amount", [
        ("pct",   0.9,  1000, 900,  100),
        ("pct",   0.8,  1250, 1000, 250),
        ("pct",   0.9,  1001, 900,  101),
        ("fixed", 100,  1000, 900,  100),
        ("fixed", 500,  1000, 500,  500),
        ("none",  0,    1000, 1000, 0),
    ])
    def test_boundary_cases(self, mode, value, base_total, expected_new, expected_amount):
        discount = DiscountInput(mode=mode, value=value)
        new_total, discount_amount = apply_manual_discount(base_total, discount)
        assert new_total == expected_new
        assert discount_amount == expected_amount

    def test_pct_floor_truncation(self):
        # floor(1001 * 0.9) = floor(900.9) = 900, discount = 101
        discount = DiscountInput(mode="pct", value=0.9)
        new_total, discount_amount = apply_manual_discount(1001, discount)
        assert new_total == 900
        assert discount_amount == 101

    def test_fixed_returns_tuple(self):
        discount = DiscountInput(mode="fixed", value=200)
        result = apply_manual_discount(1000, discount)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_none_returns_unchanged(self):
        discount = DiscountInput(mode="none", value=0)
        new_total, discount_amount = apply_manual_discount(999, discount)
        assert new_total == 999
        assert discount_amount == 0
