"""Invites and plan codes, as the operator sees and makes them (doc 03 section 5, doc 07 section 6, ADR-012).

Every function takes a connection from `SystemDatabase.system_session("admin")`. The operator role can list codes without
their hashes, make a code (the caller computes the hash; the code itself is shown once and never stored), and revoke one. It
cannot change anything else about a code, and it cannot read a hash back, so nothing here can reveal or replay a code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

PAGE_MAX = 100


@dataclass(frozen=True)
class InviteRow:
    id: int
    label: str
    email: str | None
    max_uses: int
    used: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    grant_code_id: int | None
    grant_label: str | None

    def status(self, now: datetime) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        if self.used >= self.max_uses:
            return "used_up"
        return "works"


@dataclass(frozen=True)
class GrantRow:
    id: int
    label: str
    plan: str
    days: int
    max_redemptions: int | None
    used: int
    expires_at: datetime | None
    created_by: str
    created_at: datetime
    revoked_at: datetime | None

    def status(self, now: datetime) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= now:
            return "expired"
        if self.max_redemptions is not None and self.used >= self.max_redemptions:
            return "used_up"
        return "works"


def _page(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(limit, PAGE_MAX)), max(0, offset)


def list_invites(conn: psycopg.Connection, *, limit: int = 50, offset: int = 0) -> tuple[list[InviteRow], int]:
    """Invites, newest first, with how many times each was used and the plan code it carries, if any."""
    limit, offset = _page(limit, offset)
    total = conn.execute("SELECT count(*) AS n FROM invites").fetchone()["n"]
    rows = conn.execute(
        """SELECT i.id, i.label, i.email, i.max_uses, i.expires_at, i.revoked_at, i.created_at, i.grant_code_id,
                  g.label AS grant_label,
                  (SELECT count(*) FROM invite_redemptions r WHERE r.invite_id = i.id) AS used
           FROM invites i LEFT JOIN grant_codes g ON g.id = i.grant_code_id
           ORDER BY i.created_at DESC, i.id DESC LIMIT %s OFFSET %s""",
        (limit, offset),
    ).fetchall()
    return [InviteRow(**r) for r in rows], total


def get_invite(conn: psycopg.Connection, invite_id: int) -> InviteRow | None:
    row = conn.execute(
        """SELECT i.id, i.label, i.email, i.max_uses, i.expires_at, i.revoked_at, i.created_at, i.grant_code_id,
                  g.label AS grant_label,
                  (SELECT count(*) FROM invite_redemptions r WHERE r.invite_id = i.id) AS used
           FROM invites i LEFT JOIN grant_codes g ON g.id = i.grant_code_id WHERE i.id = %s""",
        (invite_id,),
    ).fetchone()
    return InviteRow(**row) if row else None


def get_grant_code(conn: psycopg.Connection, grant_id: int) -> GrantRow | None:
    row = conn.execute(
        """SELECT g.id, g.label, g.plan, g.days, g.max_redemptions, g.expires_at, g.created_by, g.created_at, g.revoked_at,
                  (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) AS used
           FROM grant_codes g WHERE g.id = %s""",
        (grant_id,),
    ).fetchone()
    return GrantRow(**row) if row else None


def grant_exists(conn: psycopg.Connection, grant_id: int, *, now: datetime | None = None) -> bool:
    """Whether a plan code exists and can still be given (not revoked, not expired)."""
    row = conn.execute("SELECT revoked_at, expires_at FROM grant_codes WHERE id = %s", (grant_id,)).fetchone()
    moment = now or datetime.now(UTC)
    return row is not None and row["revoked_at"] is None and (row["expires_at"] is None or row["expires_at"] > moment)


def create_invite(
    conn: psycopg.Connection,
    *,
    code_hash: bytes,
    label: str,
    email: str | None,
    max_uses: int,
    expires_at: datetime | None,
    grant_code_id: int | None,
) -> int:
    row = conn.execute(
        """INSERT INTO invites (code_hash, label, email, max_uses, expires_at, grant_code_id)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (code_hash, label, email, max_uses, expires_at, grant_code_id),
    ).fetchone()
    return row["id"]


def revoke_invite(conn: psycopg.Connection, invite_id: int, *, now: datetime | None = None) -> bool:
    """Stop an invite being used. False if there is no such invite or it was already revoked."""
    row = conn.execute(
        "UPDATE invites SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING id",
        (now or datetime.now(UTC), invite_id),
    ).fetchone()
    return row is not None


def list_grant_codes(conn: psycopg.Connection, *, limit: int = 50, offset: int = 0) -> tuple[list[GrantRow], int]:
    limit, offset = _page(limit, offset)
    total = conn.execute("SELECT count(*) AS n FROM grant_codes").fetchone()["n"]
    rows = conn.execute(
        """SELECT g.id, g.label, g.plan, g.days, g.max_redemptions, g.expires_at, g.created_by, g.created_at, g.revoked_at,
                  (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) AS used
           FROM grant_codes g ORDER BY g.created_at DESC, g.id DESC LIMIT %s OFFSET %s""",
        (limit, offset),
    ).fetchall()
    return [GrantRow(**r) for r in rows], total


def create_grant_code(
    conn: psycopg.Connection,
    *,
    code_hash: bytes,
    label: str,
    plan: str,
    days: int,
    max_redemptions: int | None,
    expires_at: datetime | None,
    created_by: str,
) -> int:
    row = conn.execute(
        """INSERT INTO grant_codes (code_hash, label, plan, days, max_redemptions, expires_at, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (code_hash, label, plan, days, max_redemptions, expires_at, created_by),
    ).fetchone()
    return row["id"]


def revoke_grant_code(conn: psycopg.Connection, grant_id: int, *, now: datetime | None = None) -> bool:
    """Stop a plan code being used. People who already used it keep their plan. False if there is none to revoke."""
    row = conn.execute(
        "UPDATE grant_codes SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING id",
        (now or datetime.now(UTC), grant_id),
    ).fetchone()
    return row is not None
