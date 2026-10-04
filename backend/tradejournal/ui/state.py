"""Filter selections shared by the dashboard and the trade log.

This is a single-user app on your own machine, so one in-memory selection is
shared by every page and survives navigating between them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..presets import PRESETS, preset_range
from ..queries import TradeFilter

__all__ = ["PRESETS", "FilterState", "filters", "preset_range"]  # the presets moved to tradejournal/presets.py


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
