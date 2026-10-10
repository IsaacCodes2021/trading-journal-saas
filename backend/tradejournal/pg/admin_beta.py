"""The beta, as the operator sees it: who joined, where each person stands, and ending someone's access.

Every function takes a connection from `SystemDatabase.system_session("admin")`. The operator role can read the sign-ups and stamp
`revoked_at` and `ended_notice_sent_at` on a grant; it cannot read a code's hash or change what a grant gives.

A person's **state**: *waiting* (joined, no account yet), *active* (an account and the 30 days are running), *ended* (they ran
out), *revoked* (an operator ended it early). Their **code**: *unused*, *used*, *expired* (14 days passed) or *withdrawn*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import psycopg

PAGE_MAX = 100
State = Literal["waiting", "active", "ended", "revoked"]
STATES: tuple[State, ...] = ("waiting", "active", "ended", "revoked")

# One row per sign-up, with the invite behind it and, once there is an account, the 30-day grant it was given.
_BASE = """
    WITH base AS (
        SELECT s.id, s.first_name, s.email, s.prop_firm, s.created_at, s.codes_made, s.email_sent_at, s.email_failures,
               i.id AS invite_id, i.expires_at AS code_expires_at, i.revoked_at AS code_revoked_at,
               ir.user_id,
               g.id AS redemption_id, g.starts_at AS access_starts_at, g.ends_at AS access_ends_at,
               g.revoked_at AS access_revoked_at, g.ended_notice_sent_at
        FROM beta_signups s
        LEFT JOIN invites i ON i.id = s.invite_id
        LEFT JOIN invite_redemptions ir ON ir.invite_id = i.id
        LEFT JOIN LATERAL (
            SELECT gr.id, gr.starts_at, gr.ends_at, gr.revoked_at, gr.ended_notice_sent_at
            FROM grant_redemptions gr
            WHERE gr.user_id = ir.user_id AND gr.grant_code_id = i.grant_code_id
            ORDER BY gr.ends_at DESC LIMIT 1
        ) g ON TRUE
    ), staged AS (
        SELECT base.*,
               CASE
                   WHEN user_id IS NULL THEN 'waiting'
                   WHEN access_revoked_at IS NOT NULL THEN 'revoked'
                   WHEN access_ends_at <= %(now)s THEN 'ended'
                   ELSE 'active'
               END AS state,
               CASE
                   WHEN user_id IS NOT NULL THEN 'used'
                   WHEN code_revoked_at IS NOT NULL THEN 'withdrawn'
                   WHEN code_expires_at <= %(now)s THEN 'expired'
                   ELSE 'unused'
               END AS code
        FROM base
    )
"""


@dataclass(frozen=True)
class BetaRow:
    id: int
    first_name: str
    email: str
    prop_firm: str
    joined_at: datetime
    state: State
    code: str
    access_ends_at: datetime | None
    access_revoked_at: datetime | None
    ended_notice_sent_at: datetime | None
    email_sent_at: datetime | None
    email_failures: int
    redemption_id: int | None
    invite_id: int | None


def _row(r: dict) -> BetaRow:
    return BetaRow(
        id=r["id"],
        first_name=r["first_name"],
        email=r["email"],
        prop_firm=r["prop_firm"],
        joined_at=r["created_at"],
        state=r["state"],
        code=r["code"],
        access_ends_at=r["access_ends_at"],
        access_revoked_at=r["access_revoked_at"],
        ended_notice_sent_at=r["ended_notice_sent_at"],
        email_sent_at=r["email_sent_at"],
        email_failures=r["email_failures"],
        redemption_id=r["redemption_id"],
        invite_id=r["invite_id"],
    )


def _like(text: str) -> str:
    """The search box as a pattern: what was typed, with `%`, `_` and the escape itself taken literally."""
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_people(
    conn: psycopg.Connection,
    *,
    now: datetime,
    state: State | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[BetaRow], int]:
    """People who joined, newest first, with the total that match."""
    limit, offset = max(1, min(limit, PAGE_MAX)), max(0, offset)
    where: list[str] = []
    params: dict[str, object] = {"now": now, "limit": limit, "offset": offset}
    if state is not None:
        where.append("state = %(state)s")
        params["state"] = state
    if q and q.strip():
        where.append("(first_name ILIKE %(like)s OR email ILIKE %(like)s OR prop_firm ILIKE %(like)s)")
        params["like"] = _like(q.strip())
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(_BASE + f"SELECT count(*) AS n FROM staged {clause}", params).fetchone()["n"]  # noqa: S608
    rows = conn.execute(
        _BASE + f"SELECT * FROM staged {clause} ORDER BY created_at DESC, id DESC LIMIT %(limit)s OFFSET %(offset)s",  # noqa: S608
        params,
    ).fetchall()
    return [_row(r) for r in rows], total


def counts(conn: psycopg.Connection, *, now: datetime) -> dict[str, int]:
    """How many people are in each state (and `all`), for the filter chips."""
    rows = conn.execute(_BASE + "SELECT state, count(*) AS n FROM staged GROUP BY state", {"now": now}).fetchall()  # noqa: S608 (fixed text; every value is a parameter)
    found = {r["state"]: r["n"] for r in rows}
    result = {state: found.get(state, 0) for state in STATES}
    result["all"] = sum(result.values())
    return result


def get_person(conn: psycopg.Connection, signup_id: int, *, now: datetime) -> BetaRow | None:
    row = conn.execute(_BASE + "SELECT * FROM staged WHERE id = %(id)s", {"now": now, "id": signup_id}).fetchone()  # noqa: S608 (fixed text; every value is a parameter)
    return _row(row) if row else None


@dataclass(frozen=True)
class Revoked:
    outcome: Literal["access_ended", "code_withdrawn"]
    redemption_id: int | None


def revoke(conn: psycopg.Connection, person: BetaRow, *, now: datetime | None = None) -> Revoked | None:
    """End this person's beta access now (an account), or withdraw their code (no account yet). None when there is nothing
    left to end: they already ended, were already revoked, or their code is already used up, expired or withdrawn."""
    moment = now or datetime.now(UTC)
    if person.state == "active" and person.redemption_id is not None:
        done = conn.execute(
            "UPDATE grant_redemptions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING id",
            (moment, person.redemption_id),
        ).fetchone()
        return Revoked("access_ended", person.redemption_id) if done else None
    if person.state == "waiting" and person.code == "unused" and person.invite_id is not None:
        done = conn.execute(
            "UPDATE invites SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL RETURNING id",
            (moment, person.invite_id),
        ).fetchone()
        return Revoked("code_withdrawn", None) if done else None
    return None


def mark_notice_sent(conn: psycopg.Connection, redemption_id: int, *, now: datetime | None = None) -> bool:
    """Record that the "your access has ended" email left, once. False if it was already recorded."""
    done = conn.execute(
        "UPDATE grant_redemptions SET ended_notice_sent_at = %s WHERE id = %s AND ended_notice_sent_at IS NULL RETURNING id",
        (now or datetime.now(UTC), redemption_id),
    ).fetchone()
    return done is not None
