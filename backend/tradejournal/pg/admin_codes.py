"""Invites and plan codes, as the operator sees and makes them (doc 03 section 5, doc 07 section 6, ADR-012).

Every function takes a connection from `SystemDatabase.system_session("admin")`. The operator role can list codes without
their hashes, make a code (the caller computes the hash), and revoke one. It cannot change anything else about a code, and it
cannot read a hash back. A random code is shown once and never stored, so nothing here can reveal or replay it; a promo code
the operator typed (FREE30, migration 0020) is stored readable on purpose, because it is meant to be shared.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from .discounts import USED as DISCOUNT_USED

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
    code: str | None  # the promo code as typed; None for a random code, which is never kept
    kind: str
    label: str
    plan: str
    days: int | None  # None for a discount code (it gives no days)
    max_redemptions: int | None
    used: int  # people who redeemed it; for a discount, people who subscribed with it
    expires_at: datetime | None
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    # A discount code's terms and its copy at the payment provider (migration 0022); None for every other kind.
    percent_off: int | None = None
    duration: str | None = None
    duration_months: int | None = None
    provider: str | None = None
    provider_discount_id: str | None = None
    sync_status: str | None = None
    sync_error: str | None = None
    synced_at: datetime | None = None

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


# A free-days code is used when someone redeems it; a discount code when someone subscribes with it (migration 0022).
_USED = f"""(CASE WHEN g.kind = 'discount' THEN {DISCOUNT_USED}
                 ELSE (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) END)"""  # noqa: S608
_GRANT_COLUMNS = f"""g.id, g.code, g.kind, g.label, g.plan, g.days, g.max_redemptions, g.expires_at, g.created_by,
                  g.created_at, g.revoked_at, g.percent_off, g.duration, g.duration_months, g.provider,
                  g.provider_discount_id, g.sync_status, g.sync_error, g.synced_at, {_USED} AS used"""

# What "works" means for a code, in SQL (the same rule as `GrantRow.status`): not revoked, not expired, a use left. The
# operator's "Show" filter is one of these, so a page of 25 is 25 rows of the kind asked for.
_WORKS = f"""g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > %(now)s)
            AND (g.max_redemptions IS NULL OR {_USED} < g.max_redemptions)"""
GRANT_FILTERS = {
    "works": _WORKS,
    "ended": f"g.revoked_at IS NULL AND NOT ({_WORKS})",  # used up or expired
    "revoked": "g.revoked_at IS NOT NULL",
}


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
    row = conn.execute(f"SELECT {_GRANT_COLUMNS} FROM grant_codes g WHERE g.id = %s", (grant_id,)).fetchone()  # noqa: S608
    return GrantRow(**row) if row else None


def code_in_use(conn: psycopg.Connection, code: str) -> bool:
    """Whether a typed code is taken, in any case, revoked and expired codes included (a code is never reused)."""
    return conn.execute("SELECT 1 FROM grant_codes WHERE upper(code) = upper(%s)", (code,)).fetchone() is not None


def grant_exists(conn: psycopg.Connection, grant_id: int, *, now: datetime | None = None) -> bool:
    """Whether a plan code exists and can still be given (not revoked, not expired). The trial's code is not one."""
    row = conn.execute(
        "SELECT revoked_at, expires_at FROM grant_codes WHERE id = %s AND kind = 'free_days'", (grant_id,)
    ).fetchone()
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


def list_grant_codes(
    conn: psycopg.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    show: str | None = None,
    now: datetime | None = None,
) -> tuple[list[GrantRow], int]:
    """Codes, newest first. `show` narrows them to one of `GRANT_FILTERS` (works, ended, revoked); None is all of them."""
    limit, offset = _page(limit, offset)
    # The trial's code (migration 0021) is the system's, not a code anyone hands out: it is not listed.
    where = f"g.kind <> 'trial' AND {GRANT_FILTERS[show] if show else 'true'}"
    params = {"now": now or datetime.now(UTC), "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT count(*) AS n FROM grant_codes g WHERE {where}", params).fetchone()["n"]  # noqa: S608
    rows = conn.execute(
        f"""SELECT {_GRANT_COLUMNS} FROM grant_codes g WHERE {where}
            ORDER BY g.created_at DESC, g.id DESC LIMIT %(limit)s OFFSET %(offset)s""",  # noqa: S608 (fixed fragments)
        params,
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
    code: str | None = None,
) -> int:
    """`code` is a typed promo code, kept readable; None for a random one. A typed code that is taken raises
    `psycopg.errors.UniqueViolation` (the unique index settles two operators typing the same code at once)."""
    row = conn.execute(
        """INSERT INTO grant_codes (code_hash, code, label, plan, days, max_redemptions, expires_at, created_by)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (code_hash, code, label, plan, days, max_redemptions, expires_at, created_by),
    ).fetchone()
    return row["id"]


def create_discount_code(
    conn: psycopg.Connection,
    *,
    code_hash: bytes,
    code: str,
    label: str,
    percent_off: int,
    duration: str,
    duration_months: int | None,
    max_redemptions: int | None,
    expires_at: datetime | None,
    created_by: str,
) -> int:
    """A discount code, saved first and pushed to the provider after (`set_sync`): it starts `pending`. A taken code raises
    `psycopg.errors.UniqueViolation`, as for any typed code."""
    row = conn.execute(
        """INSERT INTO grant_codes (code_hash, code, kind, label, plan, days, percent_off, duration, duration_months,
                                    max_redemptions, expires_at, created_by, sync_status)
           VALUES (%s, %s, 'discount', %s, 'pro', NULL, %s, %s, %s, %s, %s, %s, 'pending') RETURNING id""",
        (code_hash, code, label, percent_off, duration, duration_months, max_redemptions, expires_at, created_by),
    ).fetchone()
    return row["id"]


def set_sync(
    conn: psycopg.Connection,
    grant_id: int,
    *,
    status: str,
    provider: str | None = None,
    provider_discount_id: str | None = None,
    error: str | None = None,
    now: datetime | None = None,
) -> None:
    """What the provider did with a discount code: `synced` (with its id the first time), `failed` (with a short reason
    code, never a provider message or a secret), or `pending`. A provider id once recorded is never lost."""
    conn.execute(
        """UPDATE grant_codes SET sync_status = %s, provider = coalesce(%s, provider),
                  provider_discount_id = coalesce(provider_discount_id, %s), sync_error = %s,
                  synced_at = CASE WHEN %s = 'synced' THEN %s ELSE synced_at END
           WHERE id = %s AND kind = 'discount'""",
        (
            status,
            provider,
            provider_discount_id,
            (error or "")[:200] or None,
            status,
            now or datetime.now(UTC),
            grant_id,
        ),
    )


def revoke_grant_code(conn: psycopg.Connection, grant_id: int, *, now: datetime | None = None) -> bool:
    """Stop a plan code being used. People who already used it keep their plan. False if there is none to revoke."""
    row = conn.execute(
        "UPDATE grant_codes SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING id",
        (now or datetime.now(UTC), grant_id),
    ).fetchone()
    return row is not None
