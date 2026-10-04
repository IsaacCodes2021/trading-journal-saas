"""Finding and creating users at sign-in (the `login` system role; doc 03 section 4).

The identity provider owns who a person is: their subject (a stable id) and a verified email. This module only mirrors
that into our `users` table so the rest of the system has an id to hang a journal on. The login role cannot change a
user's status or make an operator (column-level grants, migration 0004), whatever this code does.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from . import audit


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


def set_operator(owner_conn: psycopg.Connection, email: str, *, operator: bool) -> tuple[UUID, bool] | None:
    """Make an existing account an operator (or stop it being one), as the schema owner: the only way, because no role the
    API runs as may write `users.is_admin` (migration 0004). Returns the account's id and whether anything changed, or None
    if there is no such account. A change is written to the audit log as `cli:owner`."""
    row = owner_conn.execute(
        "SELECT id, is_admin FROM users WHERE lower(email) = lower(%s)", (email.strip(),)
    ).fetchone()
    if row is None:
        return None
    if row["is_admin"] == operator:
        return row["id"], False
    owner_conn.execute("UPDATE users SET is_admin = %s WHERE id = %s", (operator, row["id"]))
    audit.record(
        owner_conn, "operator_granted" if operator else "operator_removed", user_id=row["id"], actor="cli:owner"
    )
    return row["id"], True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Make an account an operator, or stop it being one (as the schema owner)."
    )
    parser.add_argument("command", choices=["make-operator", "remove-operator"])
    parser.add_argument("--email", required=True, help="the account's email address (it must have signed in once)")
    args = parser.parse_args(argv)
    url = os.environ.get("TJ_OWNER_URL")
    if not url:
        print("TJ_OWNER_URL is not set", file=sys.stderr)
        return 2
    with psycopg.connect(url, autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        result = set_operator(conn, args.email, operator=args.command == "make-operator")
    if result is None:
        print("There is no account with that email address. It has to sign in once first.", file=sys.stderr)
        return 1
    _, changed = result
    print(
        ("Done." if changed else "Nothing to change: it was already so.")
        + " The operator tools also need two-step verification."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
