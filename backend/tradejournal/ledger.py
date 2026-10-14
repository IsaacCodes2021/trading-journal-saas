"""Money spent on and received from prop accounts: evaluation, reset, activation and
monthly fees, and payouts. Kept apart from trading P&L, so trade statistics are
unaffected; together they answer "is the prop business itself profitable?"
(totals are computed in finance.business_totals).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from .finance import money_str, to_money
from .timeutil import to_utc_iso

COST_KINDS = {
    "evaluation_fee": "Evaluation fee",
    "reset_fee": "Reset",
    "activation_fee": "Activation fee",
    "monthly_fee": "Monthly fee",
    "other_cost": "Other cost",
}
PAYOUT = "payout"
KINDS = {**COST_KINDS, PAYOUT: "Payout"}  # stored value -> display name


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    account_id: int
    kind: str
    amount: Decimal  # always positive; is_payout says which way the money went
    entry_date: date
    note: str
    # A payout's own facts (None on other entries, and on payouts recorded before they were kept): its number in the
    # account's sequence, the gross requested, the trader's split in percent (amount = gross x split), and a fee.
    payout_number: int | None = None
    gross_amount: Decimal | None = None
    profit_split_pct: Decimal | None = None
    payout_fee: Decimal | None = None

    @property
    def is_payout(self) -> bool:
        return self.kind == PAYOUT


def default_cost_kind(account_type: str) -> str:
    """What buying an account is called: an evaluation fee, or a funded account's activation fee."""
    return "activation_fee" if account_type == "funded" else "evaluation_fee"


def add_entry(
    conn: sqlite3.Connection, account_id: int, kind: str, amount: Decimal, entry_date: date, note: str = ""
) -> LedgerEntry:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    amount = to_money(amount)
    if amount <= 0:
        raise ValueError("amount must be more than zero")
    if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
        raise LookupError(f"no account with id {account_id}")
    cursor = conn.execute(
        "INSERT INTO account_ledger (account_id, kind, amount, entry_date, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, kind, money_str(amount), entry_date.isoformat(), note.strip(), to_utc_iso(datetime.now(UTC))),
    )
    return LedgerEntry(cursor.lastrowid, account_id, kind, amount, entry_date, note.strip())


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM account_ledger WHERE id = ?", (entry_id,))


def entries(conn: sqlite3.Connection, account_id: int | None = None) -> list[LedgerEntry]:
    """Entries for one account (or all), oldest first."""
    query = "SELECT * FROM account_ledger"
    params: tuple = ()
    if account_id is not None:
        query += " WHERE account_id = ?"
        params = (account_id,)
    rows = conn.execute(query + " ORDER BY entry_date, id", params)
    return [
        LedgerEntry(
            r["id"], r["account_id"], r["kind"], Decimal(r["amount"]), date.fromisoformat(r["entry_date"]), r["note"]
        )
        for r in rows
    ]
