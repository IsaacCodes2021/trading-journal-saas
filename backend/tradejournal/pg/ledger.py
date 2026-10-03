"""Costs and payouts of prop accounts, on Postgres.

The port of ``tradejournal/ledger.py`` (milestone M2). The kinds, the ``LedgerEntry`` record and the default cost kind
are shared with the SQLite module; only the code that talks to the database is rewritten. Every function takes a
connection from ``Database.tenant_session``, so row-level security limits it to one user's rows.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import psycopg

from ..finance import to_money
from ..ledger import (
    COST_KINDS,  # noqa: F401  (re-exported for callers and tests)
    KINDS,
    PAYOUT,  # noqa: F401  (re-exported)
    LedgerEntry,
    default_cost_kind,  # noqa: F401  (pure; re-exported)
)


def add_entry(
    conn: psycopg.Connection, account_id: int, kind: str, amount: Decimal, entry_date: date, note: str = ""
) -> LedgerEntry:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    amount = to_money(amount)
    if amount <= 0:
        raise ValueError("amount must be more than zero")
    if conn.execute("SELECT 1 FROM accounts WHERE id = %s", (account_id,)).fetchone() is None:
        # Someone else's account looks exactly like a missing one: row-level security hides it.
        raise LookupError(f"no account with id {account_id}")
    row = conn.execute(
        "INSERT INTO account_ledger (account_id, kind, amount, entry_date, note) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        (account_id, kind, amount, entry_date, note.strip()),
    ).fetchone()
    return _entry_from_row(row)


def delete_entry(conn: psycopg.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM account_ledger WHERE id = %s", (entry_id,))


def entries(conn: psycopg.Connection, account_id: int | None = None) -> list[LedgerEntry]:
    """Entries for one account (or all), oldest first."""
    if account_id is None:
        rows = conn.execute("SELECT * FROM account_ledger ORDER BY entry_date, id")
    else:
        rows = conn.execute("SELECT * FROM account_ledger WHERE account_id = %s ORDER BY entry_date, id", (account_id,))
    return [_entry_from_row(r) for r in rows]


def _entry_from_row(row: dict) -> LedgerEntry:
    return LedgerEntry(row["id"], row["account_id"], row["kind"], row["amount"], row["entry_date"], row["note"])
