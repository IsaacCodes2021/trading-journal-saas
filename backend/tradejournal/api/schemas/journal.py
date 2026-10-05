"""Accounts, the shared filters, and the numbers the dashboard shows (doc 07 section 2).

The shapes mirror the single-page app's `lib/data/model.ts`, which was written first from the design canvas. Money is
always `Money` (a decimal string); ratios and counts are plain numbers.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import Path, Query
from pydantic import ConfigDict, Field, model_validator

from .base import ApiModel
from .money import Money

# Row ids are Postgres bigints: a bigger number in a path or a filter is a bad request, not a database error.
MAX_ID = 9_223_372_036_854_775_807
RowId = Annotated[int, Field(ge=1, le=MAX_ID)]
PathId = Annotated[int, Path(ge=1, le=MAX_ID)]

AccountType = Literal["evaluation", "funded"]
Direction = Literal["long", "short"]
Outcome = Literal["passed", "failed", "closed"]
TrailingType = Literal["eod", "intraday"]
Preset = Literal["all", "7d", "30d", "90d", "mtd", "last_month", "ytd"]


class AccountRefOut(ApiModel):
    id: int
    name: str
    firm: str
    account_type: AccountType


class AccountOut(AccountRefOut):
    """Everything stored about an account (the rules too). Mirrors `accounts.Account`."""

    account_size: Money | None
    starting_balance: Money
    max_drawdown: Money | None
    trailing_type: TrailingType | None
    trail_lock_enabled: bool
    trail_lock_level: Money | None
    daily_loss_limit: Money | None
    profit_target: Money | None
    created_at: datetime
    outcome: Outcome | None  # set when the account is archived
    ended_on: date | None
    archive_note: str


class TradeFilterQuery(ApiModel):
    """The one filter model that stats and trades share (doc 07 section 2).

    `preset` is resolved on the server, in the person's trading-day timezone, so the client never computes a date
    range and the server's own clock never decides what "today" is. An explicit `start` or `end` (trading days,
    inclusive) takes the place of the preset. Empty lists and missing values mean "no restriction".
    """

    model_config = ConfigDict(extra="forbid")  # a misspelled filter is an error, not a silently wider answer

    preset: Preset = "all"
    start: date | None = None
    end: date | None = None
    account_type: AccountType | None = None
    show_archived: bool = False  # trades of archived (passed, failed, closed) accounts are left out unless asked
    firms: list[str] = []
    account_ids: list[RowId] = []
    symbol_roots: list[str] = []
    direction: Direction | None = None

    @model_validator(mode="after")
    def _start_not_after_end(self) -> TradeFilterQuery:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


FilterQuery = Annotated[TradeFilterQuery, Query()]


class FilterOptionsOut(ApiModel):
    firms: list[str]  # the firms the person has accounts with
    symbol_roots: list[str]  # the contracts the person has traded, by root ("NQ")


class DateRangeOut(ApiModel):
    start: date
    end: date


class SummaryOut(ApiModel):
    """Mirrors `finance.Summary`, plus how many accounts the trades came from."""

    trade_count: int
    account_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    gross_pnl: Money
    fees: Money
    net_pnl: Money
    total_won: Money
    total_lost: Money  # negative
    win_rate: float | None  # 0..1
    profit_factor: float | None
    avg_win: Money | None
    avg_loss: Money | None  # negative
    win_loss_ratio: float | None
    expectancy: Money | None
    largest_win: Money | None
    largest_loss: Money | None
    trading_days: int
    winning_days: int
    losing_days: int
    max_drawdown: Money  # a positive amount


class EquityPointOut(ApiModel):
    at: str  # the trade's exit as ISO 8601 UTC, or the trading day (YYYY-MM-DD) when the series is by day
    equity: Money  # cumulative net P&L
    drawdown: Money  # equity minus its running peak: zero or negative


class DailyResultOut(ApiModel):
    day: date
    net_pnl: Money
    trade_count: int


class ScoreAxisOut(ApiModel):
    name: str
    score: float  # 0..100
    rule: str  # how the measured value maps to the score, in words


class PerformanceScoreOut(ApiModel):
    overall: float
    axes: list[ScoreAxisOut]


class AccountSummaryOut(ApiModel):
    account: AccountRefOut
    trade_count: int
    net_pnl: Money


ReportBy = Literal["symbol", "weekday", "hour", "hold", "tag", "account"]


class ReportQuery(TradeFilterQuery):
    """The shared filters, and what to break the trades down by."""

    by: ReportBy = "symbol"


ReportQueryParams = Annotated[ReportQuery, Query()]


class ReportGroupOut(ApiModel):
    key: str  # stable: a contract root, a weekday number, an hour, a hold bucket, a tag id, an account id
    label: str  # what to show: "NQ", "Monday", "09:00", "5 to 15 minutes", "A+ setup", "Apex 07"
    detail: str | None  # the tag's category, the account's firm and type; nothing for the others
    summary: SummaryOut  # the same statistics as the dashboard, over this group's trades


class ReportOut(ApiModel):
    by: ReportBy
    groups: list[ReportGroupOut]  # in the order to read them (see `api/reports.py`)
    summary: SummaryOut  # over every trade the filters matched


class DashboardOut(ApiModel):
    range: DateRangeOut | None  # the trading days the data covers; null when there are no trades
    summary: SummaryOut
    equity_by_trade: list[EquityPointOut]
    equity_by_day: list[EquityPointOut]
    daily: list[DailyResultOut]
    score: PerformanceScoreOut | None  # null when there are no trades
    accounts: list[AccountSummaryOut]  # accounts that have trades in view, best result first
