"""Small builders for the Postgres tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg

from tradejournal.pg import accounts


def record_statements(conn) -> list[str]:
    """Start recording what a connection runs: one entry per ``execute`` or ``executemany`` call.

    A batched ``executemany`` is one entry however many rows it carries, which is the point: the entries stand for
    round trips, the cost that matters over a network.
    """
    log: list[str] = []

    class Recording(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            log.append(str(query).split()[0].upper())
            return super().execute(query, params, **kwargs)

        def executemany(self, query, params_seq, **kwargs):
            log.append(str(query).split()[0].upper() + " (batch)")
            return super().executemany(query, params_seq, **kwargs)

    conn.cursor_factory = Recording
    return log


def make_account(conn, name: str = "Apex 07", **kwargs):
    kwargs.setdefault("firm", "Apex")
    kwargs.setdefault("account_size", Decimal("50000"))
    kwargs.setdefault("starting_balance", Decimal("50000"))
    return accounts.create_account(conn, name, **kwargs)


def make_trades(conn, account_id: int, count: int = 8, *, day: date = date(2024, 1, 5)) -> list[int]:
    """Insert `count` simple trades, five minutes apart, and return their ids in entry order.

    The SQLite tests got their trades by importing a real export; this slice of the port has no importer yet, and
    the journal only needs rows to attach notes, tags and screenshots to.
    """
    start = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
    ids = []
    for n in range(count):
        entry = start + timedelta(minutes=5 * n)
        row = conn.execute(
            """INSERT INTO trades (account_id, symbol, symbol_root, direction, entry_at, exit_at, trade_date,
                   hold_seconds, total_qty, max_position, fill_pair_count, tick_size, avg_entry, avg_exit,
                   points, ticks, gross_pnl, fees, net_pnl)
               VALUES (%s, 'NQH4', 'NQ', 'long', %s, %s, %s, 60, 1, 1, 1, 0.25, 16450.25, 16451.25, 1, 4, 20, 4, 16)
               RETURNING id""",
            (account_id, entry, entry + timedelta(seconds=60), day),
        ).fetchone()
        ids.append(row["id"])
    return ids
