"""Filter selections shared by the dashboard and the trade log.

This is a single-user app on your own machine, so one in-memory selection is
shared by every page and survives navigating between them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..queries import TradeFilter

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


@dataclass
class FilterState:
    account_type: str = "all"  # "all", "funded" or "evaluation"
    show_archived: bool = False  # archived (passed/failed/closed) accounts are hidden unless asked
    firms: list[str] = field(default_factory=list)  # empty = every firm
    account_ids: list[int] = field(default_factory=list)  # empty = every account matching the above
    preset: str = "all"
    start: date | None = None
    end: date | None = None
    symbol_roots: list[str] = field(default_factory=list)
    direction: str = "all"  # "all", "long" or "short"

    def apply_preset(self, preset: str, today: date | None = None) -> None:
        self.preset = preset
        if preset != "custom":
            self.start, self.end = preset_range(preset, today or date.today())

    def to_filter(self) -> TradeFilter:
        return TradeFilter(
            account_ids=tuple(self.account_ids),
            account_type=None if self.account_type == "all" else self.account_type,
            include_archived=self.show_archived,
            firms=tuple(self.firms),
            start=self.start,
            end=self.end,
            symbol_roots=tuple(self.symbol_roots),
            direction=None if self.direction == "all" else self.direction,
        )


filters = FilterState()
