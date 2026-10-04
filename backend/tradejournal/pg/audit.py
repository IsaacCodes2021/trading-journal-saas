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


def record(
    conn: psycopg.Connection,
    action: str,
    *,
    user_id: UUID | None = None,
    actor: str = ACTOR_USER,
    ip_hash: str | None = None,
    meta: dict[str, str | int | bool | None] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_log (user_id, actor_user_id, actor, action, ip_hash, meta) VALUES (%s, %s, %s, %s, %s, %s)",
        (user_id, user_id if actor == ACTOR_USER else None, actor, action, ip_hash, json.dumps(meta or {})),
    )
