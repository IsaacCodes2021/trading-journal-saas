"""Display formatting. Values arrive as Decimal; these only turn them into text."""

from __future__ import annotations

from datetime import datetime, tzinfo
from decimal import Decimal

from ..finance import to_money

MISSING = "—"


def money(value: Decimal | None) -> str:
    if value is None:
        return MISSING
    rounded = to_money(value)
    return f"{'-' if rounded < 0 else ''}${abs(rounded):,.2f}"


def percent(fraction: Decimal | None) -> str:
    return MISSING if fraction is None else f"{fraction * 100:.1f}%"


def ratio(value: Decimal | None) -> str:
    return MISSING if value is None else f"{value:,.2f}"


def price(value: Decimal) -> str:
    """At least two decimals, more only when the value has them (weighted averages)."""
    text = f"{value:,f}"
    whole, _, decimals = text.partition(".")
    decimals = decimals.rstrip("0").ljust(2, "0")
    return f"{whole}.{decimals}"


def hold(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s" if minutes else f"{secs}s"


def local(moment: datetime, zone: tzinfo, pattern: str = "%Y-%m-%d %H:%M:%S") -> str:
    return moment.astimezone(zone).strftime(pattern)


def pnl_class(value: Decimal | None) -> str:
    if value is None or value == 0:
        return ""
    return "tj-pos" if value > 0 else "tj-neg"
