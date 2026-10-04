"""Small builders for the Postgres tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from api_fakes import FakeAudit, FakeSessions, make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.ports import Services
from tradejournal.auth.csrf import csrf_token
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


ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
NOON_UTC = datetime(2024, 3, 22, 12, 0, tzinfo=UTC)


def write_headers(session) -> dict[str, str]:
    """What the single-page app sends with a change: a same-origin request carrying the session's CSRF token."""
    return {"sec-fetch-site": "same-origin", "x-csrf-token": csrf_token(SECRET, session.id)}


def api_client_with_services(db, user_id, *, now: datetime = NOON_UTC):
    """`api_client`, and the in-memory services behind it, so a test can read what the server recorded (`.audit.events`)."""
    services, sessions, _ = make_services()
    token, session = sessions.sign_in(user_id)
    config = ApiConfig(app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=SECRET)
    app = create_app(config, services, database=db, clock=lambda: now)
    client = TestClient(app, base_url=ORIGIN, headers={"cookie": f"{SESSION_COOKIE}={token}", **write_headers(session)})
    return client, services


def api_client_with_real_accounts(db, system, user_id, *, now: datetime = NOON_UTC) -> TestClient:
    """`api_client` on an app whose accounts service is the real one, for the routes that read the person's account
    (the export, settings): the in-memory accounts of `api_client` do not know people made straight in the database."""
    sessions = FakeSessions()
    token, session = sessions.sign_in(user_id)
    services = Services(sessions=sessions, accounts=PgAccountService(db, system), audit=FakeAudit())
    config = ApiConfig(app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=SECRET)
    app = create_app(config, services, database=db, clock=lambda: now)
    return TestClient(app, base_url=ORIGIN, headers={"cookie": f"{SESSION_COOKIE}={token}", **write_headers(session)})


def api_client(db, user_id, *, now: datetime = NOON_UTC) -> TestClient:
    """A browser signed in as `user_id`, on a server whose clock says `now`."""
    return api_client_with_services(db, user_id, now=now)[0]


def add_trade(conn, account_id: int, day: date, net: str, *, nth: int = 0, root: str = "NQ", direction: str = "long"):
    """One closed trade on trading day `day`: $4.00 of fees, so gross is net + 4."""
    exit_at = datetime(day.year, day.month, day.day, 15, nth, tzinfo=UTC)
    conn.execute(
        """INSERT INTO trades (account_id, symbol, symbol_root, direction, entry_at, exit_at, trade_date,
               hold_seconds, total_qty, max_position, fill_pair_count, tick_size, avg_entry, avg_exit,
               points, ticks, gross_pnl, fees, net_pnl)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 60, 1, 1, 1, 0.25, 100, 101, 1, 4, %s, 4, %s)""",
        (
            account_id,
            f"{root}H4",
            root,
            direction,
            exit_at - timedelta(seconds=60),
            exit_at,
            day,
            Decimal(net) + 4,
            Decimal(net),
        ),
    )
