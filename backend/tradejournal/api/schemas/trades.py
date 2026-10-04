"""The trade log, one trade's page, and the tag catalog (doc 07 section 2). Mirrors `lib/data/model.ts`."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import Query
from pydantic import ConfigDict, Field

from .base import ApiModel
from .journal import AccountRefOut, Direction, RowId, TradeFilterQuery
from .money import Money

# A trade's prices are exact decimals like money (a string in JSON, never a float), but they are not amounts of money.
Price = Money

TradeSort = Literal[
    "date", "-date",
    "account", "-account",
    "symbol", "-symbol",
    "direction", "-direction",
    "qty", "-qty",
    "hold", "-hold",
    "net_pnl", "-net_pnl",
]  # fmt: skip


class TradeListFilters(TradeFilterQuery):
    """The shared filters plus a search and an order. Navigating between trades reuses this model, so the arrows on a
    trade's page follow the list the person came from."""

    q: Annotated[str, Field(max_length=100)] = ""  # matches the contract, its root, the account name or a tag name
    sort: TradeSort = "-date"  # "date" is oldest first (by entry time), "-date" newest first


class TradesQuery(TradeListFilters):
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


TradeListFiltersQuery = Annotated[TradeListFilters, Query()]
TradesQueryParams = Annotated[TradesQuery, Query()]


class TagOut(ApiModel):
    id: int
    group_id: int
    name: str
    color: str  # "#rrggbb"


class CatalogTagOut(TagOut):
    trade_count: int  # how many trades carry it


class TagGroupOut(ApiModel):
    id: int
    name: str  # "Setup", "Mistake", "Emotion"


class ColorPresetOut(ApiModel):
    color: str  # "#rrggbb"
    name: str  # "Blue"


class TagCatalogOut(ApiModel):
    groups: list[TagGroupOut]
    tags: list[CatalogTagOut]
    presets: list[ColorPresetOut]  # the colours offered for a tag
    custom_colors: list[str]  # colours already on the person's tags that are not presets, oldest first, to reuse


class TradeOut(ApiModel):
    id: int
    account: AccountRefOut
    symbol: str  # the contract, "NQZ6"
    symbol_root: str  # "NQ"
    direction: Direction
    entry_at: datetime
    exit_at: datetime
    trade_day: date  # the trading day, computed on the server from the person's trading-day settings
    hold_seconds: int
    total_qty: int
    avg_entry: Price
    avg_exit: Price
    gross_pnl: Money
    fees: Money
    net_pnl: Money
    tags: list[TagOut]
    has_notes: bool
    screenshot_count: int


class TradesPageOut(ApiModel):
    items: list[TradeOut]
    total: int  # trades matching every filter, across all pages
    net_pnl: Money  # their net result: the count and the net follow every filter


class FillPairOut(ApiModel):
    """One row of the broker's export: a buy fill matched to a sell fill. Several of them make one trade."""

    id: int
    bought_at: datetime
    sold_at: datetime
    qty: int
    buy_price: Price
    sell_price: Price
    pnl: Money  # this pair's gross result, before fees (fees are charged per trade)


class ScreenshotOut(ApiModel):
    id: int
    original_name: str
    created_at: datetime
    src: str | None  # same-origin URL of the image; null on a server with nowhere to keep files


class TradeDetailOut(TradeOut):
    points: Price  # per contract, positive = favorable
    fill_pairs: list[FillPairOut]
    notes_html: str  # sanitized again on render, whatever the server did
    screenshots: list[ScreenshotOut]


class NeighborsOut(ApiModel):
    """The trades either side of one in the list the person came from; null at either end."""

    previous: int | None
    next: int | None


class NotesIn(ApiModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    html: Annotated[str, Field(max_length=100_000)]  # the editor's HTML; the server trims it, an empty note is ""


class NotesOut(ApiModel):
    notes_html: str  # what was stored


class NewTagIn(ApiModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    group_id: RowId
    name: Annotated[str, Field(min_length=1, max_length=200)]  # trimmed and checked again by the server (its own limit)


class TagGroupIn(ApiModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    name: Annotated[str, Field(min_length=1, max_length=200)]  # trimmed and checked again by the server


class TagEditIn(ApiModel):
    """Rename, recolour or move a tag to another category: the whole tag, as it should now be."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    name: Annotated[str, Field(min_length=1, max_length=200)]
    group_id: RowId
    color: Annotated[str, Field(min_length=1, max_length=20)]  # "#rrggbb" or "#rgb"


class TagMergeIn(ApiModel):
    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    into_id: RowId  # the tag that stays; every trade carrying this one carries that one instead
