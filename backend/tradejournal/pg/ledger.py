"""Costs and payouts of prop accounts, on Postgres.

The port of ``tradejournal/ledger.py`` (milestone M2). The kinds, the ``LedgerEntry`` record and the default cost kind
are shared with the SQLite module; only the code that talks to the database is rewritten. Every function takes a
connection from ``Database.tenant_session``, so row-level security limits it to one user's rows.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import psycopg

from ..finance import CENT, to_money
from ..ledger import (
    COST_KINDS,  # noqa: F401  (re-exported for callers and tests)
    KINDS,
    PAYOUT,
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
        "INSERT INTO account_ledger (account_id, kind, amount, entry_date, note) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (account_id, kind, amount, entry_date, note.strip()),
    ).fetchone()
    if kind == PAYOUT:
        renumber_payouts(conn, account_id)
    return _get(conn, row["id"])


def net_of(gross: Decimal, split_pct: Decimal) -> Decimal:
    """What the trader receives from a gross payout at their split, to the cent (half up, as the database checks it)."""
    return (gross * split_pct / 100).quantize(CENT, rounding=ROUND_HALF_UP)


def add_payout(
    conn: psycopg.Connection,
    account_id: int,
    *,
    entry_date: date,
    gross: Decimal,
    split_pct: Decimal,
    fee: Decimal = Decimal(0),
    note: str = "",
) -> LedgerEntry:
    """Record a payout: the gross the firm paid out of the account and the trader's split; the net (gross x split) is the
    ledger amount, so business totals count what was received. A fee kept from it counts as a cost."""
    gross = to_money(gross)
    if gross <= 0:
        raise ValueError("the gross amount must be more than zero")
    split_pct = split_pct.quantize(CENT, rounding=ROUND_HALF_UP)  # as the column keeps it, so the net agrees with it
    if not 0 < split_pct <= 100:
        raise ValueError("the split must be more than 0% and at most 100%")
    fee = to_money(fee)
    if fee < 0:
        raise ValueError("the fee cannot be negative")
    net = net_of(gross, split_pct)
    if net <= 0:
        raise ValueError("the net amount (gross x split) must be at least one cent")
    if conn.execute("SELECT 1 FROM accounts WHERE id = %s", (account_id,)).fetchone() is None:
        raise LookupError(f"no account with id {account_id}")
    row = conn.execute(
        """INSERT INTO account_ledger (account_id, kind, amount, entry_date, note, gross_amount, profit_split_pct, payout_fee)
           VALUES (%s, 'payout', %s, %s, %s, %s, %s, %s) RETURNING id""",
        (account_id, net, entry_date, note.strip(), gross, split_pct, fee),
    ).fetchone()
    renumber_payouts(conn, account_id)
    return _get(conn, row["id"])


def renumber_payouts(conn: psycopg.Connection, account_id: int) -> None:
    """Number an account's payouts 1, 2, 3... by date (then by when they were recorded), so a payout entered late, or one
    removed, leaves the sequence right. Payouts recorded before the numbers were kept get theirs here too."""
    conn.execute(
        """UPDATE account_ledger l SET payout_number = n.number
           FROM (SELECT id, row_number() OVER (ORDER BY entry_date, id) AS number
                 FROM account_ledger WHERE account_id = %s AND kind = 'payout') AS n
           WHERE l.id = n.id AND l.payout_number IS DISTINCT FROM n.number""",
        (account_id,),
    )


def payouts(conn: psycopg.Connection, account_id: int) -> list[LedgerEntry]:
    """An account's payouts, oldest first."""
    rows = conn.execute(
        "SELECT * FROM account_ledger WHERE account_id = %s AND kind = 'payout' ORDER BY entry_date, id", (account_id,)
    )
    return [_entry_from_row(r) for r in rows]


def delete_entry(conn: psycopg.Connection, entry_id: int) -> None:
    row = conn.execute("DELETE FROM account_ledger WHERE id = %s RETURNING account_id, kind", (entry_id,)).fetchone()
    if row is not None and row["kind"] == PAYOUT:
        renumber_payouts(conn, row["account_id"])


def entries(conn: psycopg.Connection, account_id: int | None = None) -> list[LedgerEntry]:
    """Entries for one account (or all), oldest first."""
    if account_id is None:
        rows = conn.execute("SELECT * FROM account_ledger ORDER BY entry_date, id")
    else:
        rows = conn.execute("SELECT * FROM account_ledger WHERE account_id = %s ORDER BY entry_date, id", (account_id,))
    return [_entry_from_row(r) for r in rows]


def _get(conn: psycopg.Connection, entry_id: int) -> LedgerEntry:
    return _entry_from_row(conn.execute("SELECT * FROM account_ledger WHERE id = %s", (entry_id,)).fetchone())


def _entry_from_row(row: dict) -> LedgerEntry:
    return LedgerEntry(
        row["id"],
        row["account_id"],
        row["kind"],
        row["amount"],
        row["entry_date"],
        row["note"],
        payout_number=row["payout_number"],
        gross_amount=row["gross_amount"],
        profit_split_pct=row["profit_split_pct"],
        payout_fee=row["payout_fee"],
    )
