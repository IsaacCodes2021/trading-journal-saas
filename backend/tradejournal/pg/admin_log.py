"""The operator's view of the audit log, and the overview numbers (doc 07 section 6, ADR-012).

Every function takes a connection from `SystemDatabase.system_session("admin")`. The log is the operator's to read and
append to (migration 0011) and nobody's to change. What it holds is security events with small facts and a keyed hash of
each address: no trade, note or file. The address hash is not returned, since an operator has no use for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

from .admin import plans_for
from .admin_codes import InviteRow

PAGE_MAX = 100


@dataclass(frozen=True)
class AuditRow:
    id: int
    at: datetime
    actor: str
    action: str
    actor_email: str | None
    user_id: UUID | None
    user_email: str | None
    meta: dict


@dataclass(frozen=True)
class Overview:
    accounts: int
    signups_7d: int
    active_7d: int
    active: int
    suspended: int
    pending_deletion: int
    pro: int
    free: int
    open_invites: int
    open_grant_codes: int
    grants_ending_7d: int
    paying: int = 0
    on_trial: int = 0


def _like(text: str) -> str:
    return "%" + re.sub(r"([\\%_])", r"\\\1", text) + "%"


def audit_events(
    conn: psycopg.Connection,
    *,
    action: str | None = None,
    q: str | None = None,
    actor: str | None = None,
    since: datetime | None = None,
    limit: int = 50,
    before: int | None = None,
) -> list[AuditRow]:
    """Events, newest first. `action` is exact, `q` is part of the email of the person it is about (or the operator who did
    it) or an account id, `actor` is `user`, `admin`, `system` or `cli:...`, `since` is the earliest time. `before` is the id of
    the last event already seen: pass it to read the next page (an id never moves, so paging cannot skip or repeat)."""
    word = (q or "").strip()
    exact: str | None = None
    try:
        exact = str(UUID(word)) if word else None
    except ValueError:
        pass
    rows = conn.execute(
        """SELECT a.id, a.created_at AS at, a.actor, a.action, a.user_id, a.meta,
                  u.email AS user_email, o.email AS actor_email
           FROM audit_log a
           LEFT JOIN users u ON u.id = a.user_id
           LEFT JOIN users o ON o.id = a.actor_user_id
           WHERE (%(action)s::text IS NULL OR a.action = %(action)s)
             AND (%(actor)s::text IS NULL OR a.actor = %(actor)s)
             AND (%(since)s::timestamptz IS NULL OR a.created_at >= %(since)s)
             AND (%(before)s::bigint IS NULL OR a.id < %(before)s)
             AND (%(pattern)s::text IS NULL OR u.email ILIKE %(pattern)s ESCAPE '\\' OR o.email ILIKE %(pattern)s ESCAPE '\\'
                  OR a.user_id::text = %(exact)s OR a.actor_user_id::text = %(exact)s)
           ORDER BY a.id DESC LIMIT %(limit)s""",
        {
            "action": action,
            "actor": actor,
            "since": since,
            "before": before,
            "pattern": _like(word) if word else None,
            "exact": exact,
            "limit": max(1, min(limit, PAGE_MAX + 1)),  # one more than a page, so the caller can tell there is another
        },
    ).fetchall()
    return [AuditRow(**r) for r in rows]


def overview(conn: psycopg.Connection, *, now: datetime | None = None) -> Overview:
    """The numbers on the overview: accounts by status, who signed up and who signed in this week, how many are on Pro
    (computed the way every person's plan is, never stored), and how many invites and plan codes still work."""
    moment = now or datetime.now(UTC)
    week = moment - timedelta(days=7)
    counts = conn.execute(
        """SELECT count(*) AS accounts,
                  count(*) FILTER (WHERE created_at >= %s) AS signups_7d,
                  count(*) FILTER (WHERE last_login_at >= %s) AS active_7d,
                  count(*) FILTER (WHERE status = 'active') AS active,
                  count(*) FILTER (WHERE status = 'suspended') AS suspended,
                  count(*) FILTER (WHERE status = 'pending_deletion') AS pending_deletion
           FROM users""",
        (week, week),
    ).fetchone()
    ids = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
    plans = plans_for(conn, ids, moment) if ids else {}
    pro = sum(1 for plan in plans.values() if plan.plan == "pro")
    invites = conn.execute(
        """SELECT i.id, i.label, i.email, i.max_uses, i.expires_at, i.revoked_at, i.created_at, i.grant_code_id,
                  NULL::text AS grant_label,
                  (SELECT count(*) FROM invite_redemptions r WHERE r.invite_id = i.id) AS used
           FROM invites i WHERE i.revoked_at IS NULL"""
    ).fetchall()
    open_invites = sum(1 for r in invites if InviteRow(**r).status(moment) == "works")
    open_grants = conn.execute(
        """SELECT count(*) AS n FROM grant_codes g
           WHERE g.kind <> 'trial' AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > %s)
             AND (g.max_redemptions IS NULL
                  OR (SELECT count(*) FROM grant_redemptions r WHERE r.grant_code_id = g.id) < g.max_redemptions)""",
        (moment,),
    ).fetchone()["n"]
    # Free days (a code or an invite) whose last day is in the next week, for people with no override and no later grant: who
    # is about to become read-only. Counted from the grants themselves, the way the plan rule reads them.
    ending = conn.execute(
        """SELECT count(DISTINCT r.user_id) AS n FROM grant_redemptions r
           WHERE r.revoked_at IS NULL AND r.ends_at > %(now)s AND r.ends_at <= %(week)s
             AND NOT EXISTS (SELECT 1 FROM grant_redemptions o
                             WHERE o.user_id = r.user_id AND o.revoked_at IS NULL AND o.ends_at > %(week)s)
             AND NOT EXISTS (SELECT 1 FROM plan_overrides p
                             WHERE p.user_id = r.user_id AND p.revoked_at IS NULL
                               AND (p.expires_at IS NULL OR p.expires_at > %(now)s))""",
        {"now": moment, "week": moment + timedelta(days=7)},
    ).fetchone()["n"]
    paying = sum(1 for plan in plans.values() if plan.source == "subscription")
    on_trial = conn.execute(
        """SELECT count(DISTINCT r.user_id) AS n FROM grant_redemptions r JOIN grant_codes g ON g.id = r.grant_code_id
           WHERE g.kind = 'trial' AND r.revoked_at IS NULL AND r.starts_at <= %s AND r.ends_at > %s""",
        (moment, moment),
    ).fetchone()["n"]
    return Overview(
        accounts=counts["accounts"],
        signups_7d=counts["signups_7d"],
        active_7d=counts["active_7d"],
        active=counts["active"],
        suspended=counts["suspended"],
        pending_deletion=counts["pending_deletion"],
        pro=pro,
        free=len(ids) - pro,
        open_invites=open_invites,
        open_grant_codes=open_grants,
        grants_ending_7d=ending,
        paying=paying,
        on_trial=on_trial,
    )
