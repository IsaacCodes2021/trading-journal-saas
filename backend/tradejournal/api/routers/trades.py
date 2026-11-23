"""The trade log and one trade's page (read side; notes, tags and screenshots are written by later slices)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from psycopg import Connection

from ...pg import accounts as pg_accounts
from ...pg import copy_groups as pg_groups
from ...pg import journal as pg_journal
from ...pg import queries as pg_queries
from ...pg import store as pg_store
from ..accounts_view import account_ref
from ..cents import cents
from ..deps import ERROR_RESPONSES, ClockDep, TenantDep
from ..filtering import resolve_filter
from ..schemas import Problem
from ..schemas.copy_groups import CopyGroupRefOut
from ..schemas.journal import PathId
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
from ..trades_view import copied_on, matches, neighbors_in, one_per_trade, page_of, rows_for, sort_rows, trade_out
from .screenshots import file_url

router = APIRouter(prefix="/api/v1", tags=["trades"], responses={**ERROR_RESPONSES, 404: {"model": Problem}})


def _ordered_rows(conn: Connection, query: TradeListFilters, now) -> list[TradeOut]:
    """Every trade matching the filters and the search, in the requested order (one transaction, one snapshot). A
    copied trade is one row, on its leader, under its group's name, whichever of its accounts the filter named."""
    accounts = {a.id: a for a in pg_accounts.list_accounts(conn)}
    records, labels = one_per_trade(
        pg_queries.load_trades(conn, resolve_filter(query, conn, now)), pg_groups.list_groups(conn), accounts
    )
    rows = rows_for(records, pg_journal.tags_by_trade(conn), pg_journal.journal_summary(conn), labels)
    return sort_rows([row for row in rows if matches(row, query.q)], query.sort)


@router.get("/trades", response_model=TradesPageOut)
def list_trades(query: TradesQueryParams, tenant: TenantDep, clock: ClockDep) -> TradesPageOut:
    """One page of the trade log, with the total count and net result of everything the filters match."""
    with tenant.session() as conn:
        rows = _ordered_rows(conn, query, clock())
    return page_of(rows, limit=query.limit, offset=query.offset)


@router.get("/trades/{trade_id}", response_model=TradeDetailOut)
def get_trade(trade_id: PathId, request: Request, tenant: TenantDep) -> TradeDetailOut:
    """Everything about one trade. Another user's trade and a missing one are both a 404: they look the same. Its
    screenshots carry the address of their image, or none on a server that has nowhere to keep files."""
    has_files = request.app.state.files is not None
    with tenant.session() as conn:
        record = pg_queries.load_trade(conn, trade_id)
        if record is None:
            raise HTTPException(404, "That trade does not exist.")
        tags = pg_journal.trade_tags(conn, trade_id)
        pairs = pg_store.trade_fill_pairs(conn, trade_id)
        screenshots = pg_journal.screenshots(conn, trade_id)
        number = pg_queries.trade_number(conn, trade_id)
        group = pg_groups.group_led_by(conn, record.account_id)
        accounts = {a.id: a for a in pg_accounts.list_accounts(conn)}
        copied_to = [account_ref(accounts[a]) for a in pg_groups.copied_to(conn, trade_id)]
    label = CopyGroupRefOut(id=group.id, name=group.name) if group is not None and copied_on(group, record) else None
    row = trade_out(record, tags, has_notes=record.notes != "", screenshot_count=len(screenshots), copy_group=label)
    return TradeDetailOut(
        **row.model_dump(),
        number=number,
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
        copied_to=copied_to,
        screenshots=[
            ScreenshotOut(
                id=shot.id,
                original_name=shot.original_name,
                created_at=shot.created_at,
                src=file_url(shot.id) if has_files else None,
            )
            for shot in screenshots
        ],
    )


@router.get("/trades/{trade_id}/neighbors", response_model=NeighborsOut)
def get_trade_neighbors(
    trade_id: PathId, query: TradeListFiltersQuery, tenant: TenantDep, clock: ClockDep
) -> NeighborsOut:
    """The trades either side of this one in the list the person came from (same filters, search and order). A trade
    that is not theirs and one that does not exist are both a 404, as for the trade itself: they look the same."""
    with tenant.session() as conn:
        if pg_queries.load_trade(conn, trade_id) is None:
            raise HTTPException(404, "That trade does not exist.")
        rows = _ordered_rows(conn, query, clock())
    return neighbors_in(rows, trade_id)
