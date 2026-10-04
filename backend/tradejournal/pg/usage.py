"""Per-user usage counters: what quotas and the operator back end read instead of scanning tenant tables.

``refresh`` recounts from the source tables and stores the result, so a counter can never drift from the rows it
describes. Domain functions that add or remove what is counted call it in the same transaction (accounts, imports,
undo, rebuild, screenshots). Every function takes a connection from ``Database.tenant_session``, so the counts are
the current user's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True)
class Usage:
    accounts_count: int
    trades_count: int
    screenshots_count: int
    storage_bytes: int
    last_import_at: datetime | None  # the latest import that has not been undone
    updated_at: datetime | None


def refresh(conn: psycopg.Connection) -> None:
    """Recount this user's accounts, trades, screenshots (and the bytes they take) and latest import, and store them.

    A screenshot from before sizes were recorded counts as zero bytes."""
    conn.execute(
        """INSERT INTO user_usage (accounts_count, trades_count, screenshots_count, storage_bytes, last_import_at, updated_at)
           SELECT (SELECT count(*) FROM accounts),
                  (SELECT count(*) FROM trades),
                  (SELECT count(*) FROM screenshots),
                  (SELECT coalesce(sum(size_bytes), 0) FROM screenshots),
                  (SELECT max(imported_at) FROM imports WHERE undone_at IS NULL),
                  now()
           ON CONFLICT (user_id) DO UPDATE SET
               accounts_count = EXCLUDED.accounts_count,
               trades_count = EXCLUDED.trades_count,
               screenshots_count = EXCLUDED.screenshots_count,
               storage_bytes = EXCLUDED.storage_bytes,
               last_import_at = EXCLUDED.last_import_at,
               updated_at = EXCLUDED.updated_at"""
    )


def get_usage(conn: psycopg.Connection) -> Usage:
    """The stored counters. A user whose row is missing reads as all zeros (``provision_defaults`` creates it)."""
    row = conn.execute("SELECT * FROM user_usage").fetchone()
    if row is None:
        return Usage(0, 0, 0, 0, None, None)
    return Usage(**{field: row[field] for field in Usage.__dataclass_fields__})
