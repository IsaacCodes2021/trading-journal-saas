"""Domain accounts and ledger entries as the API's shapes."""

from __future__ import annotations

from ..accounts import Account
from ..ledger import LedgerEntry
from .schemas.accounts import LedgerEntryOut
from .schemas.journal import AccountOut


def account_out(a: Account) -> AccountOut:
    return AccountOut(
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


def entry_out(e: LedgerEntry) -> LedgerEntryOut:
    return LedgerEntryOut(
        id=e.id, account_id=e.account_id, kind=e.kind, amount=e.amount, entry_date=e.entry_date, note=e.note
    )
