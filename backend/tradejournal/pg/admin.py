"""What the operator does with accounts, on Postgres (doc 03 sections 5 and 7, doc 07 section 6, ADR-012).

Every function takes a connection from `SystemDatabase.system_session("admin")`: a role with no grant on any tenant table
(migration 0011), so nothing here can read a trade, a note or a screenshot. The operator sees counters and account facts.

The rules about who may act on whom (not on yourself, not on another operator) are the service's, one layer up; these
functions do what they are asked and report whether it changed anything.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import psycopg

from ..entitlements import EffectivePlan, Grant, Override, effective_plan

PAGE_MAX = 100


@dataclass(frozen=True)
class Usage:
    accounts: int
    trades: int
    screenshots: int
    storage_bytes: int
    last_import_at: datetime | None


@dataclass(frozen=True)
class UserRow:
    id: UUID
    email: str
    status: str
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None
    deletion_scheduled_at: datetime | None
    plan: EffectivePlan
    usage: Usage


@dataclass(frozen=True)
class OverrideRow:
    id: int
    plan: str
    expires_at: datetime | None
    reason: str
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None
    revoke_reason: str | None


@dataclass(frozen=True)
class RedemptionRow:
    label: str
    plan: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class EventRow:
    id: int
    at: datetime
    actor: str
    action: str
    by: UUID | None
    meta: dict


@dataclass(frozen=True)
class UserDetail:
    user: UserRow
    active_sessions: int
    overrides: list[OverrideRow]  # newest first; the active one (if any) has no `revoked_at`
    redemptions: list[RedemptionRow]
    events: list[EventRow]


def _like(text: str) -> str:
    """A search word as a case-insensitive "contains" pattern, with the pattern characters in it taken literally."""
    return "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"


def plans_for(conn: psycopg.Connection, ids: list[UUID], now: datetime) -> dict[UUID, EffectivePlan]:
    """The effective plan of each person, from two queries for the whole page rather than two for each person."""
    overrides = {
        r["user_id"]: Override(r["plan"], r["expires_at"])
        for r in conn.execute(
            "SELECT user_id, plan, expires_at FROM plan_overrides WHERE user_id = ANY(%s) AND revoked_at IS NULL",
            (ids,),
        ).fetchall()
    }
    grants: dict[UUID, list[Grant]] = defaultdict(list)
    for r in conn.execute(
        "SELECT user_id, plan, starts_at, ends_at FROM grant_redemptions WHERE user_id = ANY(%s) AND ends_at > %s",
        (ids, now),
    ).fetchall():
        grants[r["user_id"]].append(Grant(r["plan"], r["starts_at"], r["ends_at"]))
    return {i: effective_plan(now, override=overrides.get(i), grants=grants.get(i, ())) for i in ids}


_USER_COLUMNS = """u.id, u.email, u.status, u.is_admin, u.created_at, u.last_login_at, u.deletion_scheduled_at,
       COALESCE(s.accounts_count, 0) AS accounts, COALESCE(s.trades_count, 0) AS trades,
       COALESCE(s.screenshots_count, 0) AS screenshots, COALESCE(s.storage_bytes, 0) AS storage_bytes, s.last_import_at"""


def _rows(conn: psycopg.Connection, rows: list[dict], now: datetime) -> list[UserRow]:
    plans = plans_for(conn, [r["id"] for r in rows], now)
    return [
        UserRow(
            id=r["id"],
            email=r["email"],
            status=r["status"],
            is_admin=r["is_admin"],
            created_at=r["created_at"],
            last_login_at=r["last_login_at"],
            deletion_scheduled_at=r["deletion_scheduled_at"],
            plan=plans[r["id"]],
            usage=Usage(r["accounts"], r["trades"], r["screenshots"], r["storage_bytes"], r["last_import_at"]),
        )
        for r in rows
    ]


def list_users(
    conn: psycopg.Connection,
    *,
    q: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> tuple[list[UserRow], int]:
    """Accounts, newest first, optionally those whose email contains `q` (or whose id is `q`) and with one status. Returns
    the page and how many accounts match in all."""
    moment = now or datetime.now(UTC)
    word = (q or "").strip()
    exact: str | None = None
    try:
        exact = str(UUID(word)) if word else None
    except ValueError:
        pass
    where = "(%(status)s::text IS NULL OR u.status = %(status)s) AND (%(pattern)s::text IS NULL OR u.email ILIKE %(pattern)s ESCAPE '\\' OR u.id::text = %(exact)s)"
    args = {"status": status, "pattern": _like(word) if word else None, "exact": exact}
    total = conn.execute(f"SELECT count(*) AS n FROM users u WHERE {where}", args).fetchone()["n"]  # noqa: S608
    rows = conn.execute(
        f"""SELECT {_USER_COLUMNS} FROM users u LEFT JOIN user_usage s ON s.user_id = u.id
            WHERE {where} ORDER BY u.created_at DESC, u.id LIMIT %(limit)s OFFSET %(offset)s""",  # noqa: S608
        {**args, "limit": max(1, min(limit, PAGE_MAX)), "offset": max(0, offset)},
    ).fetchall()
    return _rows(conn, rows, moment), total


def get_user(
    conn: psycopg.Connection, user_id: UUID, *, now: datetime | None = None, events: int = 20
) -> UserDetail | None:
    moment = now or datetime.now(UTC)
    row = conn.execute(
        f"SELECT {_USER_COLUMNS} FROM users u LEFT JOIN user_usage s ON s.user_id = u.id WHERE u.id = %s",  # noqa: S608
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    sessions = conn.execute(
        "SELECT count(*) AS n FROM sessions WHERE user_id = %s AND revoked_at IS NULL AND expires_at > %s",
        (user_id, moment),
    ).fetchone()["n"]
    overrides = [
        OverrideRow(**r)
        for r in conn.execute(
            """SELECT id, plan, expires_at, reason, created_by, created_at, revoked_at, revoked_by, revoke_reason
               FROM plan_overrides WHERE user_id = %s ORDER BY id DESC""",
            (user_id,),
        ).fetchall()
    ]
    redemptions = [
        RedemptionRow(r["label"], r["plan"], r["starts_at"], r["ends_at"])
        for r in conn.execute(
            """SELECT g.label, r.plan, r.starts_at, r.ends_at FROM grant_redemptions r
               JOIN grant_codes g ON g.id = r.grant_code_id WHERE r.user_id = %s ORDER BY r.ends_at DESC""",
            (user_id,),
        ).fetchall()
    ]
    history = [
        EventRow(r["id"], r["created_at"], r["actor"], r["action"], r["actor_user_id"], r["meta"])
        for r in conn.execute(
            """SELECT id, created_at, actor, action, actor_user_id, meta FROM audit_log
               WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s""",
            (user_id, max(1, min(events, 100))),
        ).fetchall()
    ]
    return UserDetail(_rows(conn, [row], moment)[0], sessions, overrides, redemptions, history)


def set_status(conn: psycopg.Connection, user_id: UUID, *, to: str) -> bool:
    """Suspend an active account or restore a suspended one, and nothing else (a pending deletion is cancelled, not
    suspended). True if it changed."""
    if to not in ("suspended", "active"):
        raise ValueError("the operator can only suspend or restore an account")
    frm = "active" if to == "suspended" else "suspended"
    row = conn.execute(
        "UPDATE users SET status = %s WHERE id = %s AND status = %s RETURNING id", (to, user_id, frm)
    ).fetchone()
    return row is not None


def set_override(
    conn: psycopg.Connection,
    user_id: UUID,
    *,
    plan: str,
    expires_at: datetime | None,
    reason: str,
    by: str,
    now: datetime | None = None,
) -> bool:
    """Give the person this plan whatever else they have. Replaces an active override (kept in the history, revoked with
    the reason "replaced"). Returns whether there was one to replace."""
    moment = now or datetime.now(UTC)
    replaced = conn.execute(
        """UPDATE plan_overrides SET revoked_at = %s, revoked_by = %s, revoke_reason = 'replaced by a new override'
           WHERE user_id = %s AND revoked_at IS NULL RETURNING id""",
        (moment, by, user_id),
    ).fetchone()
    conn.execute(
        "INSERT INTO plan_overrides (user_id, plan, expires_at, reason, created_by) VALUES (%s, %s, %s, %s, %s)",
        (user_id, plan, expires_at, reason, by),
    )
    return replaced is not None


def revoke_override(
    conn: psycopg.Connection, user_id: UUID, *, reason: str, by: str, now: datetime | None = None
) -> bool:
    """End the person's active override. False if they have none."""
    row = conn.execute(
        """UPDATE plan_overrides SET revoked_at = %s, revoked_by = %s, revoke_reason = %s
           WHERE user_id = %s AND revoked_at IS NULL RETURNING id""",
        (now or datetime.now(UTC), by, reason, user_id),
    ).fetchone()
    return row is not None
