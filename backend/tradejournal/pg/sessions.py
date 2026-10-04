"""Server-side sessions on Postgres (doc 03 section 4, doc 04 authentication gates).

The cookie holds a random token; only its SHA-256 is stored, so a copy of the table cannot be replayed as cookies. A
session ends when it is revoked, when 90 days have passed since it began (absolute), or when it has gone 14 days
without being used (idle: `last_seen_at` slides). A suspended or deleting user's sessions stop working at once, because
the lookup joins the user's status.

Every function takes a connection from `SystemDatabase.system_session("login")`: the request role has no grant on the
sessions table, so a tenant route cannot read or forge one. Times come from the caller (`now`), never the database's
clock, so a test can move time and production passes `datetime.now(UTC)`.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

IDLE_TIMEOUT = timedelta(days=14)
ABSOLUTE_LIFETIME = timedelta(days=90)
TOUCH_EVERY = timedelta(minutes=5)  # `last_seen_at` slides, but is not rewritten on every request


@dataclass(frozen=True)
class LiveSession:
    public_id: UUID  # what the CSRF token is derived from; random, and never the cookie's token
    user_id: UUID
    mfa_at: datetime | None


def new_token() -> str:
    """32 random bytes, base64url: 43 characters, unguessable."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def create(
    conn: psycopg.Connection,
    user_id: UUID,
    *,
    mfa_at: datetime | None = None,
    user_agent: str | None = None,
    ip_hash: str | None = None,
    now: datetime | None = None,
) -> str:
    """Start a session and return its token (shown once: set it as the cookie, never store it)."""
    moment = _now(now)
    token = new_token()
    conn.execute(
        """INSERT INTO sessions (user_id, token_hash, created_at, last_seen_at, expires_at, mfa_at, user_agent, ip_hash)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            user_id,
            hash_token(token),
            moment,
            moment,
            moment + ABSOLUTE_LIFETIME,
            mfa_at,
            (user_agent or "")[:300] or None,
            ip_hash,
        ),
    )
    return token


def lookup(conn: psycopg.Connection, token: str | None, *, now: datetime | None = None) -> LiveSession | None:
    """The live session for a cookie's token, or None: unknown, revoked, expired, idle too long, or its user is not
    active. Slides the idle timer (at most every few minutes)."""
    if not token or len(token) > 200:
        return None
    moment = _now(now)
    digest = hash_token(token)
    row = conn.execute(
        """SELECT s.public_id, s.user_id, s.mfa_at, s.last_seen_at
           FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s AND s.last_seen_at > %s
             AND u.status = 'active'""",
        (digest, moment, moment - IDLE_TIMEOUT),
    ).fetchone()
    if row is None:
        return None
    if moment - row["last_seen_at"] > TOUCH_EVERY:
        conn.execute("UPDATE sessions SET last_seen_at = %s WHERE token_hash = %s", (moment, digest))
    return LiveSession(public_id=row["public_id"], user_id=row["user_id"], mfa_at=row["mfa_at"])


def record_mfa(conn: psycopg.Connection, token: str, mfa_at: datetime) -> None:
    """The identity provider just confirmed MFA for this session (a step-up): remember when."""
    conn.execute(
        "UPDATE sessions SET mfa_at = %s WHERE token_hash = %s AND revoked_at IS NULL", (mfa_at, hash_token(token))
    )


def revoke(conn: psycopg.Connection, token: str, *, now: datetime | None = None) -> bool:
    """End one session. True if there was a live one to end."""
    row = conn.execute(
        "UPDATE sessions SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL RETURNING id",
        (_now(now), hash_token(token)),
    ).fetchone()
    return row is not None


def revoke_all(
    conn: psycopg.Connection, user_id: UUID, *, except_token: str | None = None, now: datetime | None = None
) -> int:
    """Log out everywhere (optionally keeping the session making the request). Returns how many were ended."""
    keep = hash_token(except_token) if except_token else b""
    rows = conn.execute(
        "UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL AND token_hash <> %s RETURNING id",
        (_now(now), user_id, keep),
    ).fetchall()
    return len(rows)


@dataclass(frozen=True)
class SessionInfo:
    """What a person may be shown about one of their own sessions. Never the token, its hash, or the address hash."""

    public_id: UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    mfa_at: datetime | None
    user_agent: str | None


def _live(moment: datetime) -> tuple[str, tuple[datetime, datetime]]:
    """The rule for "this session still works", as SQL and its two parameters (same as `lookup`, minus the user check)."""
    return "revoked_at IS NULL AND expires_at > %s AND last_seen_at > %s", (moment, moment - IDLE_TIMEOUT)


def list_active(conn: psycopg.Connection, user_id: UUID, *, now: datetime | None = None) -> list[SessionInfo]:
    """A person's working sessions, most recently used first."""
    condition, params = _live(_now(now))
    rows = conn.execute(
        f"""SELECT public_id, created_at, last_seen_at, expires_at, mfa_at, user_agent
            FROM sessions WHERE user_id = %s AND {condition}
            ORDER BY last_seen_at DESC, id DESC""",  # noqa: S608 - `condition` is a literal from this module
        (user_id, *params),
    ).fetchall()
    return [SessionInfo(**row) for row in rows]


def revoke_one(conn: psycopg.Connection, user_id: UUID, public_id: UUID, *, now: datetime | None = None) -> bool:
    """End one working session of this person, named by its public id. The user id is part of the match, so an id that
    belongs to someone else ends nothing and looks exactly like one that does not exist."""
    condition, params = _live(_now(now))
    row = conn.execute(
        f"UPDATE sessions SET revoked_at = %s WHERE public_id = %s AND user_id = %s AND {condition} RETURNING id",  # noqa: S608
        (_now(now), public_id, user_id, *params),
    ).fetchone()
    return row is not None


def revoke_others(conn: psycopg.Connection, user_id: UUID, keep: UUID, *, now: datetime | None = None) -> int:
    """Log out everywhere else: end every working session of this person except `keep`. Returns how many."""
    condition, params = _live(_now(now))
    rows = conn.execute(
        f"UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND public_id <> %s AND {condition} RETURNING id",  # noqa: S608
        (_now(now), user_id, keep, *params),
    ).fetchall()
    return len(rows)
