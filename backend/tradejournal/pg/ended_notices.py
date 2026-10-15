"""Who is due the "your access has ended" email, and recording that it was sent (migration 0016).

    due(...)        the people whose time-limited access ended and who have not been told      (the `sweep` role)
    mark_sent(...)  record that the email left, so it is never sent twice                      (the `sweep` role)

Both go through two functions in the database, because the sweep role can read none of what this needs (an address, a first
name, the grants). The rules for who is due live there, in one place, and are written out in the migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

# How far back an ended grant still earns a notice. Switching the job on, or the job missing a few days, does not write to
# people whose access ended long ago.
RECENT = timedelta(days=14)
BATCH = 100


@dataclass(frozen=True)
class Notice:
    redemption_id: int  # the grant whose ending this is about (the latest of the person's ended grants)
    user_id: UUID
    email: str
    first_name: str  # from their beta sign-up when there is one, otherwise "there"
    kind: str = "free_days"  # what ended: a code or an invite's days ("free_days"), or the trial ("trial")


def due(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    limit: int = BATCH,
) -> list[Notice]:
    """Oldest ending first. `since` defaults to `RECENT` before `now`."""
    moment = now or datetime.now(UTC)
    rows = conn.execute(
        "SELECT redemption_id, user_id, email, first_name, kind FROM ended_notices_due(%s, %s, %s)",
        (moment, since or moment - RECENT, limit),
    ).fetchall()
    return [Notice(r["redemption_id"], r["user_id"], r["email"], r["first_name"], r["kind"]) for r in rows]


def mark_sent(conn: psycopg.Connection, redemption_id: int, *, now: datetime | None = None) -> bool:
    """Record the email as sent, for this grant and the person's earlier ended ones. False if it was already recorded."""
    row = conn.execute(
        "SELECT mark_ended_notice_sent(%s, %s) AS done", (redemption_id, now or datetime.now(UTC))
    ).fetchone()
    return bool(row["done"])
