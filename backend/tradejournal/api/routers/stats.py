"""What the dashboard and the filter bar read: statistics over the user's trades, and the choices for the filters."""

from __future__ import annotations

from fastapi import APIRouter

from ...pg import accounts as pg_accounts
from ...pg import queries as pg_queries
from ..dashboard import build_dashboard
from ..deps import ERROR_RESPONSES, ClockDep, TenantDep
from ..filtering import resolve_filter
from ..schemas.journal import DashboardOut, FilterOptionsOut, FilterQuery

router = APIRouter(prefix="/api/v1", tags=["stats"], responses=ERROR_RESPONSES)


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(filters: FilterQuery, tenant: TenantDep, clock: ClockDep) -> DashboardOut:
    """The headline numbers, equity curves, daily results, score and per-account results for the filtered trades."""
    with tenant.session() as conn:
        trades = pg_queries.load_trades(conn, resolve_filter(filters, conn, clock()))
    return build_dashboard(trades)


@router.get("/filter-options", response_model=FilterOptionsOut)
def get_filter_options(tenant: TenantDep) -> FilterOptionsOut:
    with tenant.session() as conn:
        firms = sorted({account.firm for account in pg_accounts.list_accounts(conn)})
        roots = pg_queries.symbol_roots(conn)
    return FilterOptionsOut(firms=firms, symbol_roots=roots)
