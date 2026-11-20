"""The day and week share cards: one trading day's or one week's numbers, as the card shows them. (The trade card reads
the trade itself.) Mirrors `lib/data/model.ts`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import Path

from .base import ApiModel
from .journal import Direction, SummaryOut
from .money import Money
from .trades import FIRST_NOTE_DAY, LAST_NOTE_DAY, Price

# The same range of days the calendar and the day notes allow.
CardDay = Annotated[date, Path(ge=FIRST_NOTE_DAY, le=LAST_NOTE_DAY)]


class CardTradeOut(ApiModel):
    """One trade as a day or week card names it: the best or the worst of them."""

    id: int
    symbol: str  # the contract, "NQZ6"
    direction: Direction
    trade_day: date
    entry_at: datetime
    total_qty: int
    net_pnl: Money
    points: Price  # per contract, positive = favorable


class PeriodCardOut(ApiModel):
    """A trading day (`start` = `end`) or a week, Sunday to Saturday, with the filters applied."""

    start: date
    end: date
    # The person's own count of trading days (on a day card) or of weeks with trades (on a week card) up to and
    # including this one: the number on the card. All their trades count, whatever the filters.
    number: int
    summary: SummaryOut
    contracts: int  # the trades' quantities added up
    net_points: Price  # each trade's points per contract, added up: a size is not counted
    accounts: list[str]  # the names of the accounts the trades came from, in alphabetical order
    best: CardTradeOut | None  # by net P&L; the earlier one when two tie; null with no trades
    worst: CardTradeOut | None
    best_points: CardTradeOut | None  # by points per contract
    worst_points: CardTradeOut | None
