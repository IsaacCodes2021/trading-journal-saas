"""The shared filter query, turned into what the database layer takes."""

from __future__ import annotations

from datetime import datetime

import psycopg

from ..pg import settings as pg_settings
from ..presets import preset_range
from ..queries import TradeFilter
from ..timeutil import trading_date
from .schemas.journal import TradeFilterQuery


def resolve_filter(query: TradeFilterQuery, conn: psycopg.Connection, now: datetime) -> TradeFilter:
    """`now` is an aware instant. "Today" is the trading day it falls in for this person (their trading-day timezone
    and start time), never the server's own calendar day: a trade closed at 17:30 New York on the 19th still counts
    toward the 19th, and one at 18:30 toward the 20th."""
    if query.start is not None or query.end is not None:
        start, end = query.start, query.end
    else:
        start, end = preset_range(query.preset, trading_date(now, pg_settings.trading_session(conn)))
    return TradeFilter(
        account_ids=tuple(query.account_ids),
        account_type=query.account_type,
        include_archived=query.show_archived,
        firms=tuple(query.firms),
        start=start,
        end=end,
        symbol_roots=tuple(query.symbol_roots),
        direction=query.direction,
    )
