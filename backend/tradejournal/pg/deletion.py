"""Deleting an account (requirement F-A6; migration 0008 explains the design).

    request(...)   an active account becomes pending, with a date `GRACE` away          (the `login` role, by function)
    cancel(...)    a pending account becomes active again                               (the `login` role, by function)
    due(...)       the pending accounts whose date has passed                           (the `sweep` role)
    delete(...)    remove one that is due, and with it every row of theirs              (the `sweep` role)

`delete` repeats the "still pending and still due" test inside the DELETE itself, so a person who signs in (and so cancels)
between the sweep reading the list and acting on it is not deleted: the statement simply finds nothing. Foreign keys with
ON DELETE CASCADE remove the tenant rows, the sessions and the invite redemptions; the audit log keeps its history with the
person detached (`SET NULL`), which is why a sweep writes its audit row first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

GRACE = timedelta(days=7)


@dataclass(frozen=True)
class Due:
    user_id: UUID
    idp_subject: str  # the identity at the provider, to be removed there too
    scheduled_at: datetime


def request(conn: psycopg.Connection, user_id: UUID, *, now: datetime | None = None) -> datetime | None:
    """Schedule the deletion. Returns when it will happen, or None if the account is not active (already pending, or
    suspended: that is for support)."""
    when = (now or datetime.now(UTC)) + GRACE
    row = conn.execute("SELECT request_account_deletion(%s, %s) AS moved", (user_id, when)).fetchone()
    return when if row["moved"] else None


def cancel(conn: psycopg.Connection, user_id: UUID) -> bool:
    """Take the account back to active. False if it was not pending."""
    return bool(conn.execute("SELECT cancel_account_deletion(%s) AS moved", (user_id,)).fetchone()["moved"])


def due(conn: psycopg.Connection, *, now: datetime | None = None, limit: int = 50) -> list[Due]:
    rows = conn.execute(
        """SELECT id, idp_subject, deletion_scheduled_at FROM users
           WHERE status = 'pending_deletion' AND deletion_scheduled_at <= %s
           ORDER BY deletion_scheduled_at, id LIMIT %s""",
        (now or datetime.now(UTC), limit),
    ).fetchall()
    return [Due(r["id"], r["idp_subject"], r["deletion_scheduled_at"]) for r in rows]


def delete(conn: psycopg.Connection, user_id: UUID, *, now: datetime | None = None) -> bool:
    """Remove the account and everything under it, if it is still pending and still due. False if it is not."""
    row = conn.execute(
        "DELETE FROM users WHERE id = %s AND status = 'pending_deletion' AND deletion_scheduled_at <= %s RETURNING id",
        (user_id, now or datetime.now(UTC)),
    ).fetchone()
    return row is not None
