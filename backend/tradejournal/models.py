"""Core value types shared by the parser, grouping, and database layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

Direction = Literal["long", "short"]


@dataclass(frozen=True)
class FillPair:
    """One row of a Tradovate Performance export: a buy fill matched to a sell fill.

    A fill pair is not a trade. Several pairs combine into one round-trip trade when
    the trader scales in or out (see grouping.py).
    """

    symbol: str
    tick_size: Decimal
    price_format: str
    price_format_type: str
    buy_fill_id: int
    sell_fill_id: int
    qty: int
    buy_price: Decimal
    sell_price: Decimal
    pnl: Decimal
    bought_at: datetime  # timezone-aware, UTC
    sold_at: datetime  # timezone-aware, UTC
    duration_raw: str
    duration_seconds: int
    direction: Direction
    line: int = 0  # 1-based line in the source CSV (0 when loaded from the database)
    id: int | None = None  # database id once stored

    @property
    def entry_fill_id(self) -> int:
        return self.buy_fill_id if self.direction == "long" else self.sell_fill_id

    @property
    def exit_fill_id(self) -> int:
        return self.sell_fill_id if self.direction == "long" else self.buy_fill_id

    @property
    def entry_at(self) -> datetime:
        return self.bought_at if self.direction == "long" else self.sold_at

    @property
    def exit_at(self) -> datetime:
        return self.sold_at if self.direction == "long" else self.bought_at

    @property
    def entry_price(self) -> Decimal:
        return self.buy_price if self.direction == "long" else self.sell_price

    @property
    def exit_price(self) -> Decimal:
        return self.sell_price if self.direction == "long" else self.buy_price


@dataclass(frozen=True)
class TradeMetrics:
    """Everything derived for one round-trip trade from its fill pairs."""

    symbol: str
    symbol_root: str
    contract_month: str | None  # "YYYY-MM"
    direction: Direction
    entry_at: datetime
    exit_at: datetime
    trade_date: date  # trading day (session-based, see timeutil.trading_date)
    hold_seconds: int
    total_qty: int
    max_position: int
    fill_pair_count: int
    tick_size: Decimal
    avg_entry: Decimal
    avg_exit: Decimal
    points: Decimal  # per contract, positive = favorable
    ticks: Decimal  # per contract
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
