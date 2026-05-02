import math
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ResinType(str, Enum):
    RPG = "rpg"
    CLEAR = "clear"


_RESIN_COEFFICIENT: dict[tuple[ResinType, bool], float] = {
    (ResinType.RPG, False): 3.5,
    (ResinType.RPG, True): 3.5,
    (ResinType.CLEAR, False): 3.5,
    (ResinType.CLEAR, True): 7.0,
}

# (tier_size, unit_price) — last tier_size is float('inf')
_PROCESSING_TIERS: list[tuple[float, int]] = [
    (2, 80),
    (3, 70),
    (3, 60),
    (3, 50),
    (float("inf"), 40),
]


@dataclass(frozen=True)
class QuoteResult:
    resin: ResinType
    colored: bool
    volume_ml: float
    body_count: int
    material_cost: int
    processing_fee: int
    subtotal: int
    auto_discount_amount: int
    auto_free_ship: bool
    order_status: str
    final_total: int


def calculate_material_cost(resin: ResinType, volume_ml: float, colored: bool) -> int:
    coefficient = _RESIN_COEFFICIENT[(resin, colored)]
    return math.ceil(volume_ml * coefficient)


def calculate_processing_fee(body_count: int) -> int:
    remaining = body_count
    fee = 0
    for tier_size, unit_price in _PROCESSING_TIERS:
        if remaining <= 0:
            break
        units = min(remaining, tier_size)
        fee += int(units) * unit_price
        remaining -= units
    return fee


def apply_auto_discounts(subtotal: int) -> tuple[int, bool, str]:
    if subtotal >= 7000:
        return math.floor(subtotal * 0.95), True, "免運費"
    if subtotal >= 4000:
        return subtotal, True, "免運費"
    if subtotal < 500:
        return subtotal, False, "未達低消"
    return subtotal, False, "正常"


@dataclass(frozen=True)
class DiscountInput:
    mode: Literal["pct", "fixed", "none"]
    value: float


def apply_manual_discount(base_total: int, discount: DiscountInput) -> tuple[int, int]:
    if discount.mode == "pct":
        new_total = math.floor(base_total * discount.value)
        return new_total, base_total - new_total
    if discount.mode == "fixed":
        amount = int(discount.value)
        return base_total - amount, amount
    return base_total, 0



def calculate_quote(
    resin: ResinType,
    colored: bool,
    volume_ml: float,
    body_count: int,
) -> QuoteResult:
    material_cost = calculate_material_cost(resin, volume_ml, colored)
    processing_fee = calculate_processing_fee(body_count)
    subtotal = material_cost + processing_fee
    discounted_total, auto_free_ship, order_status = apply_auto_discounts(subtotal)
    auto_discount_amount = subtotal - discounted_total

    return QuoteResult(
        resin=resin,
        colored=colored,
        volume_ml=volume_ml,
        body_count=body_count,
        material_cost=material_cost,
        processing_fee=processing_fee,
        subtotal=subtotal,
        auto_discount_amount=auto_discount_amount,
        auto_free_ship=auto_free_ship,
        order_status=order_status,
        final_total=discounted_total,
    )
