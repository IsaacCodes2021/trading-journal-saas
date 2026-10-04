"""The signed-in user's trading accounts (read side; writes arrive in a later M3 slice)."""

from __future__ import annotations

from fastapi import APIRouter

from ...pg import accounts as pg_accounts
from ..deps import ERROR_RESPONSES, TenantDep
from ..schemas.journal import AccountOut

router = APIRouter(prefix="/api/v1", tags=["accounts"], responses=ERROR_RESPONSES)


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(tenant: TenantDep, include_archived: bool = True) -> list[AccountOut]:
    """Every account, by name. Archived ones (passed, failed or closed) are included unless `include_archived=false`."""
    with tenant.session() as conn:
        rows = pg_accounts.list_accounts(conn, include_archived=include_archived)
    return [
        AccountOut(
            id=a.id,
            name=a.name,
            firm=a.firm,
            account_type=a.account_type,
            account_size=a.account_size,
            starting_balance=a.starting_balance,
            max_drawdown=a.max_drawdown,
            trailing_type=a.trailing_type,
            trail_lock_enabled=a.trail_lock_enabled,
            trail_lock_level=a.trail_lock_level,
            daily_loss_limit=a.daily_loss_limit,
            profit_target=a.profit_target,
            created_at=a.created_at,
            outcome=a.outcome,
            ended_on=a.ended_on,
            archive_note=a.archive_note,
        )
        for a in rows
    ]
