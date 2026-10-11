"""Date-range presets ("last 30 days") and what they mean on a given day.

Used by the API. A preset is resolved against "today" *in the
person's trading-day timezone*, which the caller works out: this module never reads the server's clock.
"""

from __future__ import annotations

from datetime import date, timedelta

PRESETS = {
    "all": "All time",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "mtd": "This month",
    "last_month": "Last month",
    "ytd": "Year to date",
    "custom": "Custom",
}


def preset_range(preset: str, today: date) -> tuple[date | None, date | None]:
    """Inclusive (start, end) trading days for a preset; (None, None) means unbounded."""
    if preset in ("7d", "30d", "90d"):
        return today - timedelta(days=int(preset[:-1]) - 1), today
    if preset == "mtd":
        return today.replace(day=1), today
    if preset == "last_month":
        last_day = today.replace(day=1) - timedelta(days=1)
        return last_day.replace(day=1), last_day
    if preset == "ytd":
        return today.replace(month=1, day=1), today
    return None, None
