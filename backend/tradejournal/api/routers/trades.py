"""The trade log and one trade's page (read side; notes, tags and screenshots are written by later slices)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from psycopg import Connection

from ...pg import journal as pg_journal
from ...pg import queries as pg_queries
from ...pg import store as pg_store
from ..cents import cents
from ..deps import ERROR_RESPONSES, ClockDep, TenantDep
from ..filtering import resolve_filter
from ..schemas import Problem
from ..schemas.trades import (
    FillPairOut,
    NeighborsOut,
    ScreenshotOut,
    TradeDetailOut,
    TradeListFilters,
    TradeListFiltersQuery,
    TradeOut,
    TradesPageOut,
    TradesQueryParams,
)
from ..trades_view import matches, neighbors_in, page_of, rows_for, sort_rows, trade_out

router = APIRouter(prefix="/api/v1", tags=["trades"], responses={**ERROR_RESPONSES, 404: {"model": Problem}})


def _ordered_rows(conn: Connection, query: TradeListFilters, now) -> list[TradeOut]:
    """Every trade matching the filters and the search, in the requested order (one transaction, one snapshot)."""
    records = pg_queries.load_trades(conn, resolve_filter(query, conn, now))
    rows = rows_for(records, pg_journal.tags_by_trade(conn), pg_journal.journal_summary(conn))
    return sort_rows([row for row in rows if matches(row, query.q)], query.sort)


@router.get("/trades", response_model=TradesPageOut)
def list_trades(query: TradesQueryParams, tenant: TenantDep, clock: ClockDep) -> TradesPageOut:
    """One page of the trade log, with the total count and net result of everything the filters match."""
    with tenant.session() as conn:
        rows = _ordered_rows(conn, query, clock())
    return page_of(rows, limit=query.limit, offset=query.offset)


@router.get("/trades/{trade_id}", response_model=TradeDetailOut)
def get_trade(trade_id: int, tenant: TenantDep) -> TradeDetailOut:
    """Everything about one trade. Another user's trade and a missing one are both a 404: they look the same."""
    with tenant.session() as conn:
        record = pg_queries.load_trade(conn, trade_id)
        if record is None:
            raise HTTPException(404, "That trade does not exist.")
        tags = pg_journal.trade_tags(conn, trade_id)
        pairs = pg_store.trade_fill_pairs(conn, trade_id)
        screenshots = pg_journal.screenshots(conn, trade_id)
    row = trade_out(record, tags, has_notes=record.notes != "", screenshot_count=len(screenshots))
    return TradeDetailOut(
        **row.model_dump(),
        points=record.points,
        fill_pairs=[
            FillPairOut(
                id=pair.id or 0,
                bought_at=pair.bought_at,
                sold_at=pair.sold_at,
                qty=pair.qty,
                buy_price=pair.buy_price,
                sell_price=pair.sell_price,
                pnl=cents(pair.pnl),
            )
            for pair in pairs
        ],
        notes_html=record.notes,
        screenshots=[
            ScreenshotOut(id=shot.id, original_name=shot.original_name, created_at=shot.created_at, src=None)
            for shot in screenshots
        ],
    )


@router.get("/trades/{trade_id}/neighbors", response_model=NeighborsOut)
def get_trade_neighbors(
    trade_id: int, query: TradeListFiltersQuery, tenant: TenantDep, clock: ClockDep
) -> NeighborsOut:
    """The trades either side of this one in the list the person came from (same filters, search and order)."""
    with tenant.session() as conn:
        rows = _ordered_rows(conn, query, clock())
    return neighbors_in(rows, trade_id)
