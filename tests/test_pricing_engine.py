import math
import pytest
from bot.pricing.engine import (
    ResinType,
    calculate_material_cost,
    calculate_processing_fee,
    apply_auto_discounts,
    apply_manual_discounts,
    calculate_quote,
    QuoteResult,
)


# ── 材料費 ────────────────────────────────────────────────────────────────────

class TestCalculateMaterialCost:
    def test_rpg_integer_volume(self):
        assert calculate_material_cost(ResinType.RPG, 10.0, colored=False) == 35

    def test_rpg_decimal_volume_ceil(self):
        # ceil(10.1) = 11, int(11 * 3.5) = int(38.5) = 38
        assert calculate_material_cost(ResinType.RPG, 10.1, colored=False) == 38

    def test_clear_not_colored(self):
        assert calculate_material_cost(ResinType.CLEAR, 10.0, colored=False) == 35

    def test_clear_colored(self):
        # ceil(10.0) * 7.0 = 70
        assert calculate_material_cost(ResinType.CLEAR, 10.0, colored=True) == 70

    def test_clear_colored_decimal_volume(self):
        # ceil(10.1) = 11, int(11 * 7.0) = 77
        assert calculate_material_cost(ResinType.CLEAR, 10.1, colored=True) == 77


# ── 加工費 ────────────────────────────────────────────────────────────────────

class TestCalculateProcessingFee:
    @pytest.mark.parametrize("count,expected", [
        (1,  80),    # 1×80
        (2,  160),   # 2×80
        (3,  230),   # 2×80 + 1×70
        (5,  370),   # 2×80 + 3×70
        (8,  550),   # 2×80 + 3×70 + 3×60
        (10, 650),   # 2×80 + 3×70 + 3×60 + 2×50
        (12, 740),   # 2×80 + 3×70 + 3×60 + 3×50 + 1×40
        (15, 860),   # 2×80 + 3×70 + 3×60 + 3×50 + 4×40
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


# ── 手動折扣 ──────────────────────────────────────────────────────────────────

class TestApplyManualDiscounts:
    def test_nine_ten_discount(self):
        # floor(1000 * 0.9) = 900
        total, free_ship = apply_manual_discounts(1000, nine_ten=True, free_ship=False, already_free=False)
        assert total == 900
        assert free_ship is False

    def test_free_ship_only(self):
        total, free_ship = apply_manual_discounts(1000, nine_ten=False, free_ship=True, already_free=False)
        assert total == 1000
        assert free_ship is True

    def test_both_discounts_combined(self):
        # floor(1000 * 0.9) = 900, free_ship = True
        total, free_ship = apply_manual_discounts(1000, nine_ten=True, free_ship=True, already_free=False)
        assert total == 900
        assert free_ship is True

    def test_no_discount(self):
        total, free_ship = apply_manual_discounts(1000, nine_ten=False, free_ship=False, already_free=False)
        assert total == 1000
        assert free_ship is False

    def test_already_free_preserved(self):
        total, free_ship = apply_manual_discounts(1000, nine_ten=False, free_ship=False, already_free=True)
        assert total == 1000
        assert free_ship is True

    def test_nine_ten_floor_truncates(self):
        # floor(1001 * 0.9) = floor(900.9) = 900
        total, _ = apply_manual_discounts(1001, nine_ten=True, free_ship=False, already_free=False)
        assert total == 900


# ── 完整計價 Pipeline ──────────────────────────────────────────────────────────

class TestCalculateQuote:
    def test_normal_order(self):
        # RPG, 100.0 ml, 3 件
        # material = int(100 * 3.5) = 350
        # processing = 230
        # subtotal = 580 → 正常 (500 ≤ 580 < 4000)
        result = calculate_quote(ResinType.RPG, colored=False, volume_ml=100.0, body_count=3)

        assert isinstance(result, QuoteResult)
        assert result.material_cost == 350
        assert result.processing_fee == 230
        assert result.subtotal == 580
        assert result.auto_discount_amount == 0
        assert result.auto_free_ship is False
        assert result.order_status == "正常"
        assert result.final_total == 580

    def test_order_triggers_ninety_five_percent(self):
        # Clear colored, 500 ml, 10 件
        # material = int(500 * 7.0) = 3500
        # processing = 650
        # subtotal = 4150 → 免運費, no 95%
        # Let's use 1000 ml to trigger 7000
        # material = int(1000 * 7.0) = 7000
        # processing = 650
        # subtotal = 7650 → floor(7650 * 0.95) = floor(7267.5) = 7267
        result = calculate_quote(ResinType.CLEAR, colored=True, volume_ml=1000.0, body_count=10)

        assert result.material_cost == 7000
        assert result.processing_fee == 650
        assert result.subtotal == 7650
        assert result.auto_free_ship is True
        assert result.order_status == "免運費"
        assert result.final_total == math.floor(7650 * 0.95)

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
