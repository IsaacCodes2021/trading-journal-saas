"""What the dashboard and the filter bar read: statistics over the user's trades, and the choices for the filters."""

from __future__ import annotations

from fastapi import APIRouter

from ...pg import accounts as pg_accounts
from ...pg import journal as pg_journal
from ...pg import queries as pg_queries
from ...pg import settings as pg_settings
from ...timeutil import zone
from ..dashboard import build_dashboard
from ..deps import ERROR_RESPONSES, ClockDep, TenantDep
from ..filtering import resolve_filter
from ..reports import build_report
from ..schemas.journal import DashboardOut, FilterOptionsOut, FilterQuery, ReportOut, ReportQueryParams

router = APIRouter(prefix="/api/v1", tags=["stats"], responses=ERROR_RESPONSES)


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(filters: FilterQuery, tenant: TenantDep, clock: ClockDep) -> DashboardOut:
    """The headline numbers, equity curves, daily results, score and per-account results for the filtered trades."""
    with tenant.session() as conn:
        trades = pg_queries.load_trades(conn, resolve_filter(filters, conn, clock()))
    return build_dashboard(trades)


@router.get("/reports", response_model=ReportOut)
def get_report(query: ReportQueryParams, tenant: TenantDep, clock: ClockDep) -> ReportOut:
    """The filtered trades broken down by contract, weekday, time of day, hold time, tag or account, with the dashboard's
    statistics for each group. The date range and the other filters are the dashboard's."""
    with tenant.session() as conn:
        trades = pg_queries.load_trades(conn, resolve_filter(query, conn, clock()))
        tags = pg_journal.tags_by_trade(conn) if query.by == "tag" else {}
        group_names = {g.id: g.name for g in pg_journal.tag_groups(conn)} if query.by == "tag" else {}
        timezone = pg_settings.get_setting(conn, "timezone")
    return build_report(query.by, trades, tags, group_names, zone(timezone))


@router.get("/filter-options", response_model=FilterOptionsOut)
def get_filter_options(tenant: TenantDep) -> FilterOptionsOut:
    with tenant.session() as conn:
        firms = sorted({account.firm for account in pg_accounts.list_accounts(conn)})
        roots = pg_queries.symbol_roots(conn)
    return FilterOptionsOut(firms=firms, symbol_roots=roots)
