"""The numbers on a day's or a week's share card. The card itself is drawn on the person's own device, as the trade card
is: nothing here makes or keeps an image.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException

from ...pg import queries as pg_queries
from ..deps import ERROR_RESPONSES, ClockDep, Tenant, TenantDep
from ..filtering import resolve_filter
from ..period_card import build_period_card
from ..schemas.journal import FilterQuery, TradeFilterQuery
from ..schemas.share import CardDay, PeriodCardOut

router = APIRouter(prefix="/api/v1/share", tags=["share"], responses=ERROR_RESPONSES)


def _card(
    tenant: Tenant, clock: Callable[[], datetime], filters: TradeFilterQuery, start: date, end: date, *, week: bool
) -> PeriodCardOut:
    # The day or the week is the date range: it takes the place of the filters' own range and preset.
    query = filters.model_copy(update={"start": start, "end": end})
    with tenant.session() as conn:
        trades = pg_queries.load_trades(conn, resolve_filter(query, conn, clock()))
        number = pg_queries.trading_week_number(conn, end) if week else pg_queries.trading_day_number(conn, end)
    return build_period_card(trades, start, end, number=number)


@router.get("/day/{day}", response_model=PeriodCardOut)
def get_day_card(day: CardDay, filters: FilterQuery, tenant: TenantDep, clock: ClockDep) -> PeriodCardOut:
    """One trading day's card, with the calendar's filters: its result, trades, contracts, net points, and its best
    and worst trades."""
    return _card(tenant, clock, filters, day, day, week=False)


@router.get("/week/{start}", response_model=PeriodCardOut)
def get_week_card(start: CardDay, filters: FilterQuery, tenant: TenantDep, clock: ClockDep) -> PeriodCardOut:
    """One week's card, Sunday `start` to the Saturday after it, as the calendar's weeks run (across a month's end
    too)."""
    if start.weekday() != 6:  # Monday is 0, Sunday 6
        raise HTTPException(422, "A week starts on a Sunday.")
    return _card(tenant, clock, filters, start, start + timedelta(days=6), week=True)
