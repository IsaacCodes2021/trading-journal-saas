"""Connections for request-serving code, always inside a tenant (doc 03 section 1).

    db = Database(app_url)           # connects as app_rw: not a superuser, no BYPASSRLS
    db.open()
    with db.tenant_session(user.id) as conn:
        accounts.list_accounts(conn)     # sees only this user's rows

`set_config(..., true)` is transaction-local, so the tenant cannot leak to the next request that gets the same pooled
connection, and it stays safe behind PgBouncer in transaction mode. Never use a session-level SET for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, app_url: str, *, min_size: int = 1, max_size: int = 10) -> None:
        self._pool = ConnectionPool(
            app_url,
            min_size=min_size,
            max_size=max_size,
            # UTC on every connection: see the first migration for why a local time zone is a silent bug.
            kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
            open=False,
        )

    def open(self) -> None:
        self._pool.open(wait=True)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def tenant_session(self, user_id: UUID) -> Iterator[psycopg.Connection]:
        """One transaction as one user: committed on success, rolled back if the block raises."""
        if not isinstance(user_id, UUID):
            raise TypeError("tenant_session needs the user's UUID")
        with self._pool.connection() as conn:
            conn.execute("SELECT set_config('app.user_id', %s, true)", (str(user_id),))
            yield conn
