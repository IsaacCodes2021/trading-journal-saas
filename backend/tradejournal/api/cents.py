"""Money leaves the API as cents."""

from __future__ import annotations

from decimal import Decimal

from .. import finance


def cents(value: Decimal) -> Decimal:
    """Rounded to cents, and always two decimals: "0.00", never "0" (a sum of nothing) or "-0.00"."""
    rounded = finance.to_money(value)
    return rounded if rounded != 0 else finance.ZERO.quantize(finance.CENT)
