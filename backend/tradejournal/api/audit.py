"""The real audit log for routes that act as a person (export, deletion and others), and a person's own view of it.

A route cannot reach `pg.system` (the import-boundary test), so it asks this port, which goes through the sign-in role. That
role may only INSERT into the log, so a route cannot change what the log already holds, and it can read only through one
narrow database function that returns this person's own events and nothing else (migration 0009)."""

from __future__ import annotations

from uuid import UUID

from ..pg import audit
from ..pg.system import SystemDatabase
from .ports import ActivityEvent

# The words the API gives for what the log calls each event. Only these reach a person: the database function lists the same
# actions (migration 0009), and a test requires the two lists to match.
ACTIVITY_KINDS = {
    "signup": "sign_up",
    "login": "sign_in",
    "login_failed": "sign_in_refused",
    "logout": "sign_out",
    "session_revoked": "other_sign_out",
    "logout_everywhere": "sign_out_everywhere",
    "export": "export",
    "deletion_requested": "deletion_requested",
    "deletion_cancelled": "deletion_cancelled",
}


class PgAuditLog:
    def __init__(self, system: SystemDatabase) -> None:
        self._system = system

    def record(
        self,
        user_id: UUID,
        action: str,
        *,
        ip_hash: str | None = None,
        meta: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        with self._system.system_session("login") as conn:
            audit.record(conn, action, user_id=user_id, ip_hash=ip_hash, meta=meta)

    def recent(self, user_id: UUID, limit: int = 30) -> list[ActivityEvent]:
        with self._system.system_session("login") as conn:
            rows = audit.activity(conn, user_id, limit)
        return [
            ActivityEvent(at=r["at"], kind=ACTIVITY_KINDS[r["action"]], ended=r["ended"], mfa=r["mfa"])
            for r in rows
            if r["action"] in ACTIVITY_KINDS
        ]
