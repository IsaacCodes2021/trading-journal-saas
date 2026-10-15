"""Roundturn Pro's price, in one place (the owner's decision of 2026-10-14, published on roundturn.app; a test holds the
two equal). The provider's catalog holds the same price; this is what the app says before anyone reaches the provider."""

from __future__ import annotations

PRICE_CENTS = 1500
CURRENCY = "USD"
INTERVAL = "month"
PRICE_TEXT = "USD 15 a month, plus applicable tax"


def amount() -> str:
    """ "15.00": the price as an exact decimal string (money crosses the API as a string)."""
    return f"{PRICE_CENTS // 100}.{PRICE_CENTS % 100:02d}"
