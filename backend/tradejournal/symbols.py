"""Futures symbol parsing: NQH4 -> root "NQ", contract month 2024-03."""

from __future__ import annotations

import re
from datetime import date

MONTH_CODES = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}  # fmt: skip

# Shortest root that leaves a month code followed by a 1-2 digit year: M2KH4 -> M2K, H, 4.
_CONTRACT_RE = re.compile(r"([A-Z0-9]+?)([FGHJKMNQUVXZ])(\d{1,2})")


def parse_symbol(symbol: str, trade_day: date) -> tuple[str, str | None]:
    """Return (root, "YYYY-MM") for a futures contract symbol.

    Single-digit years are resolved to the decade closest to the trade date. A symbol
    without a recognizable month code is its own root with no contract month.
    """
    match = _CONTRACT_RE.fullmatch(symbol.strip().upper())
    if not match:
        return symbol.strip().upper(), None
    root, month_code, year_digits = match.groups()
    year = _contract_year(year_digits, trade_day.year)
    return root, f"{year:04d}-{MONTH_CODES[month_code]:02d}"


def _contract_year(digits: str, reference_year: int) -> int:
    if len(digits) == 2:
        return 2000 + int(digits)
    year = reference_year - reference_year % 10 + int(digits)
    if year < reference_year - 1:  # trading H0 in late 2029 means March 2030
        year += 10
    elif year > reference_year + 8:
        year -= 10
    return year
