"""Finding and creating users at sign-in (the `login` system role; doc 03 section 4).

The identity provider owns who a person is: their subject (a stable id) and a verified email. This module only mirrors
that into our `users` table so the rest of the system has an id to hang a journal on. The login role cannot change a
user's status or make an operator (column-level grants, migration 0004), whatever this code does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class User:
    id: UUID
    email: str
    status: str  # 'active', 'suspended' or 'pending_deletion'


class EmailTaken(Exception):
    """Another account already has this email under a different sign-in identity."""


def find_by_subject(conn: psycopg.Connection, subject: str) -> User | None:
    row = conn.execute("SELECT id, email, status FROM users WHERE idp_subject = %s", (subject,)).fetchone()
    return User(row["id"], row["email"], row["status"]) if row else None


def create(conn: psycopg.Connection, subject: str, email: str, *, now: datetime | None = None) -> User:
    """A new account: active, not an operator (the column defaults, which this role cannot override). The caller has
    already required a verified email. Raises EmailTaken if the address belongs to someone else."""
    moment = now or datetime.now(UTC)
    try:
        with conn.transaction():  # a savepoint: a clash must not poison the caller's transaction
            row = conn.execute(
                "INSERT INTO users (idp_subject, email, email_verified_at) VALUES (%s, %s, %s) RETURNING id",
                (subject, email, moment),
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise EmailTaken(email) from None
    # The login role may set last_login_at on an existing row but not insert it (column grants, migration 0004).
    conn.execute("UPDATE users SET last_login_at = %s WHERE id = %s", (moment, row["id"]))
    return User(row["id"], email, "active")


def record_login(conn: psycopg.Connection, user_id: UUID, email: str, *, now: datetime | None = None) -> None:
    """Keep the email the identity provider reports (a person can change it there) and note the sign-in."""
    moment = now or datetime.now(UTC)
    try:
        with conn.transaction():
            conn.execute(
                "UPDATE users SET email = %s, email_verified_at = COALESCE(email_verified_at, %s), last_login_at = %s WHERE id = %s",
                (email, moment, moment, user_id),
            )
    except psycopg.errors.UniqueViolation:
        raise EmailTaken(email) from None
