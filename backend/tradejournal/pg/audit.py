"""The audit log: security events, append-only (doc 03 section 2, doc 04 launch gate "Audit log events written").

`meta` is for small facts about the event ("why a sign-in failed"), never for anything a person wrote: no trade content,
no notes, no file names, no tokens. A caller that cannot promise that must not put it here.
"""

from __future__ import annotations

import json
from uuid import UUID

import psycopg

ACTOR_USER = "user"
ACTOR_SYSTEM = "system"
ACTOR_ADMIN = "admin"  # an operator acting on someone else's account; `by` is the operator


def record(
    conn: psycopg.Connection,
    action: str,
    *,
    user_id: UUID | None = None,
    actor: str = ACTOR_USER,
    by: UUID | None = None,
    ip_hash: str | None = None,
    meta: dict[str, str | int | bool | None] | None = None,
) -> None:
    """Write one event. `user_id` is who it is about; `by` is who did it when that is not the same person (an operator).
    For a person's own actions `by` is left out and they are both."""
    actor_id = by if by is not None else (user_id if actor == ACTOR_USER else None)
    conn.execute(
        "INSERT INTO audit_log (user_id, actor_user_id, actor, action, ip_hash, meta) VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, actor_id, actor, action, ip_hash, json.dumps(meta or {})),
    )


def activity(conn: psycopg.Connection, user_id: UUID, limit: int = 30) -> list[dict]:
    """A person's own recent events, newest first: `{id, at, action, ended, mfa}` and nothing else. The database function
    does the filtering (only this person's rows, only the kinds a person may see, at most 100); see migration 0009."""
    return conn.execute("SELECT id, at, action, ended, mfa FROM my_activity(%s, %s)", (user_id, limit)).fetchall()
