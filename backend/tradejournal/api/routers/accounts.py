"""The signed-in user's trading accounts: listing, adding and changing them, their fees and their ledger."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException

from ...pg import accounts as pg_accounts
from ...pg import ledger as pg_ledger
from ...pg import queries as pg_queries
from ...pg import store as pg_store
from ...queries import TradeFilter
from ..accounts_view import account_out, entry_out
from ..deps import ERROR_RESPONSES, HEAVY, TenantDep, WriteTenantDep
from ..progress import business_totals, overviews
from ..refusals import refusals
from ..schemas import Problem
from ..schemas.accounts import (
    AccountDetailsIn,
    AccountOverviewOut,
    AccountRulesIn,
    ArchiveIn,
    BusinessTotalsOut,
    FeeIn,
    LedgerEntryOut,
    NewAccountIn,
    NewLedgerEntryIn,
    SymbolRoot,
)
from ..schemas.journal import AccountOut, PathId

router = APIRouter(
    prefix="/api/v1", tags=["accounts"], responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}}
)

_DUPLICATE = "You already have an account with that name."


def _fee_rate(rate: Decimal) -> Decimal:
    """A fee as it is usually written: at least cents ("4.00"), more only when the rate really has more ("0.625")."""
    plain = rate.normalize()
    return plain.quantize(Decimal("0.01")) if plain.as_tuple().exponent > -2 else plain  # type: ignore[operator]


# ---- accounts ---------------------------------------------------------------------------------------------------


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(tenant: TenantDep, include_archived: bool = True) -> list[AccountOut]:
    """Every account, by name. Archived ones (passed, failed or closed) are included unless `include_archived=false`."""
    with tenant.session() as conn:
        rows = pg_accounts.list_accounts(conn, include_archived=include_archived)
    return [account_out(a) for a in rows]


@router.get("/account-overviews", response_model=list[AccountOverviewOut])
def get_account_overviews(tenant: TenantDep) -> list[AccountOverviewOut]:
    """Every account with its progress against the firm's rules (closed trades only; see `api/progress.py`)."""
    with tenant.session() as conn:
        rows = pg_accounts.list_accounts(conn)
        trades = pg_queries.load_trades(conn, TradeFilter(include_archived=True))
    return overviews(rows, trades)


@router.get("/business-totals", response_model=BusinessTotalsOut)
def get_business_totals(tenant: TenantDep) -> BusinessTotalsOut:
    """Across every account, archived ones included: what the accounts cost, what they paid out, how evaluations ended."""
    with tenant.session() as conn:
        rows = pg_accounts.list_accounts(conn)
        entries = pg_ledger.entries(conn)
    return business_totals(rows, entries)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: NewAccountIn, tenant: WriteTenantDep) -> AccountOut:
    """A new account with no rules and no fees yet (set those next). The purchase date is required: it decides which
    version of the firm's rules applies."""
    with refusals(what="account", duplicate=_DUPLICATE), tenant.session() as conn:
        created = pg_accounts.create_account(
            conn,
            body.name,
            firm=body.firm,
            account_type=body.account_type,
            account_size=body.account_size,
            starting_balance=body.starting_balance,
            purchase_date=body.purchase_date,
            phase=body.phase,
        )
    return account_out(created)


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: PathId, body: AccountDetailsIn, tenant: WriteTenantDep) -> AccountOut:
    """Change an account's name, firm, type, size, starting balance, purchase date and phase. Changing the type
    reclassifies its trades."""
    with refusals(what="account", duplicate=_DUPLICATE), tenant.session() as conn:
        updated = pg_accounts.update_account(
            conn,
            account_id,
            name=body.name,
            firm=body.firm,
            account_type=body.account_type,
            account_size=body.account_size,
            starting_balance=body.starting_balance,
            purchase_date=body.purchase_date,
            phase=body.phase,
        )
    return account_out(updated)


@router.put("/accounts/{account_id}/rules", response_model=AccountOut)
def update_account_rules(account_id: PathId, body: AccountRulesIn, tenant: WriteTenantDep) -> AccountOut:
    """Set the firm's rules (profit target, drawdown, trailing, daily loss limit). A rule left null is not tracked."""
    with refusals(what="account"), tenant.session() as conn:
        updated = pg_accounts.update_rules(
            conn,
            account_id,
            profit_target=body.profit_target,
            max_drawdown=body.max_drawdown,
            trailing_type=body.trailing_type,
            trail_lock_enabled=body.trail_lock_enabled,
            trail_lock_level=body.trail_lock_level,
            daily_loss_limit=body.daily_loss_limit,
        )
    return account_out(updated)


@router.post("/accounts/{account_id}/archive", response_model=AccountOut)
def archive_account(account_id: PathId, body: ArchiveIn, tenant: WriteTenantDep) -> AccountOut:
    """Mark an account as finished (an evaluation passes, fails or is closed; a funded account fails or is closed).
    Its trades stay; views leave them out unless asked."""
    with refusals(what="account"), tenant.session() as conn:
        archived = pg_accounts.archive_account(
            conn, account_id, outcome=body.outcome, ended_on=body.ended_on, note=body.note
        )
    return account_out(archived)


@router.post("/accounts/{account_id}/restore", response_model=AccountOut)
def restore_account(account_id: PathId, tenant: WriteTenantDep) -> AccountOut:
    with refusals(what="account"), tenant.session() as conn:
        restored = pg_accounts.restore_account(conn, account_id)
    return account_out(restored)


# ---- fees --------------------------------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/fees", response_model=dict[str, str])
def get_fees(account_id: PathId, tenant: TenantDep) -> dict[str, str]:
    """Dollars per contract, round trip, by contract root ("NQ"); `*` is the rate for any other contract."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)
        schedule = pg_accounts.fee_schedule(conn, account_id)
    return {root: format(_fee_rate(rate), "f") for root, rate in sorted(schedule.items())}


@router.put("/accounts/{account_id}/fees/{symbol_root}", response_model=dict[str, str])
def set_fee(account_id: PathId, symbol_root: SymbolRoot, body: FeeIn, tenant: WriteTenantDep) -> dict[str, str]:
    """Set the rate for a contract root. The account's stored trades are recalculated in the same change."""
    with refusals(what="account"), tenant.session(statement_timeout_ms=HEAVY) as conn:
        pg_accounts.get_account(conn, account_id)
        pg_accounts.set_fee(conn, account_id, symbol_root, body.rate)
        pg_store.recalculate_trades(conn, account_id)
        schedule = pg_accounts.fee_schedule(conn, account_id)
    return {root: format(_fee_rate(rate), "f") for root, rate in sorted(schedule.items())}


@router.delete("/accounts/{account_id}/fees/{symbol_root}", response_model=dict[str, str])
def remove_fee(account_id: PathId, symbol_root: SymbolRoot, tenant: WriteTenantDep) -> dict[str, str]:
    """Remove a contract's own rate (it falls back to `*`). Stored trades are recalculated in the same change."""
    with refusals(what="account"), tenant.session(statement_timeout_ms=HEAVY) as conn:
        pg_accounts.get_account(conn, account_id)
        pg_accounts.remove_fee(conn, account_id, symbol_root)
        pg_store.recalculate_trades(conn, account_id)
        schedule = pg_accounts.fee_schedule(conn, account_id)
    return {root: format(_fee_rate(rate), "f") for root, rate in sorted(schedule.items())}


# ---- ledger ------------------------------------------------------------------------------------------------------


@router.get("/accounts/{account_id}/ledger", response_model=list[LedgerEntryOut])
def get_ledger(account_id: PathId, tenant: TenantDep) -> list[LedgerEntryOut]:
    """The account's costs and payouts, oldest first."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)
        entries = pg_ledger.entries(conn, account_id)
    return [entry_out(e) for e in entries]


@router.post("/accounts/{account_id}/ledger", response_model=LedgerEntryOut, status_code=201)
def add_ledger_entry(account_id: PathId, body: NewLedgerEntryIn, tenant: WriteTenantDep) -> LedgerEntryOut:
    """Record money spent on the account (a fee) or received from it (a payout). The amount is always positive."""
    with refusals(what="account"), tenant.session() as conn:
        entry = pg_ledger.add_entry(conn, account_id, body.kind, body.amount, body.entry_date, body.note)
    return entry_out(entry)


@router.delete("/accounts/{account_id}/ledger/{entry_id}", response_model=list[LedgerEntryOut])
def delete_ledger_entry(account_id: PathId, entry_id: PathId, tenant: WriteTenantDep) -> list[LedgerEntryOut]:
    """Delete one entry and answer with what is left of the account's ledger."""
    with refusals(what="account"), tenant.session() as conn:
        pg_accounts.get_account(conn, account_id)
        if entry_id not in {e.id for e in pg_ledger.entries(conn, account_id)}:
            raise HTTPException(404, "That ledger entry does not exist.")
        pg_ledger.delete_entry(conn, entry_id)
        remaining = pg_ledger.entries(conn, account_id)
    return [entry_out(e) for e in remaining]
