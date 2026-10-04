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

# How long a request may wait, and how long it may run (doc 04, T5: "request timeouts"). Without these, one slow query or
# a full pool holds a worker thread for as long as it likes, and enough of them leave nothing for anyone else.
POOL_WAIT_SECONDS = 5.0  # waiting for a free connection; psycopg's own default is 30 seconds
STATEMENT_TIMEOUT_MS = 15_000  # one statement of an ordinary request; the heavy routes ask for more
HEAVY_STATEMENT_TIMEOUT_MS = 120_000  # an import, an undo or an export: one statement can touch every row a person has
LOCK_TIMEOUT_MS = 5_000  # waiting for a lock another transaction holds (including a per-person advisory lock)


class Database:
    def __init__(
        self,
        app_url: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        pool_wait: float = POOL_WAIT_SECONDS,
    ) -> None:
        self._pool = ConnectionPool(
            app_url,
            min_size=min_size,
            max_size=max_size,
            timeout=pool_wait,
            # UTC on every connection: see the first migration for why a local time zone is a silent bug.
            kwargs={"row_factory": dict_row, "options": "-c timezone=UTC"},
            open=False,
        )

    def open(self, *, wait: bool = True) -> None:
        """With `wait=False` this returns at once and the pool keeps trying in the background, so a web process can start
        while the database is briefly away and report it through /readyz instead of failing to boot."""
        self._pool.open(wait=wait)

    def schema_version(self, timeout: float = 2.0) -> str | None:
        """The newest migration the database has applied, for the readiness check. It needs no tenant: it reads one
        name through a function the request role may call (migration 0005), never the migrations table itself.
        Raises if no connection is available within `timeout` seconds."""
        with self._pool.connection(timeout=timeout) as conn:
            conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(int(timeout * 1000)),))
            return conn.execute("SELECT app_schema_version() AS version").fetchone()["version"]

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def tenant_session(
        self, user_id: UUID, *, snapshot: bool = False, statement_timeout_ms: int = STATEMENT_TIMEOUT_MS
    ) -> Iterator[psycopg.Connection]:
        """One transaction as one user: committed on success, rolled back if the block raises.

        `snapshot=True` is for reading many tables that must agree with each other (an export): the transaction is
        REPEATABLE READ and READ ONLY, so every query sees the database as it was when the first one ran, and nothing
        in the block can write.

        Every statement is cancelled after `statement_timeout_ms` (psycopg raises `QueryCanceled`) and a wait for a
        lock ends after five seconds (`LockNotAvailable`); a wait for a free connection ends with `PoolTimeout`. All
        three are settings of this transaction only, like the tenant, so they cannot leak to the next user of the
        connection."""
        if not isinstance(user_id, UUID):
            raise TypeError("tenant_session needs the user's UUID")
        with self._pool.connection() as conn:
            if snapshot:  # must come before any other statement of the transaction
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute(
                "SELECT set_config('app.user_id', %s, true), set_config('statement_timeout', %s, true),"
                " set_config('lock_timeout', %s, true)",
                (str(user_id), str(statement_timeout_ms), str(LOCK_TIMEOUT_MS)),
            )
            yield conn
