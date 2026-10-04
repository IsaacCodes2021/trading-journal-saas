"""The real audit log for routes that act as a person (export now; deletion and others later).

A route cannot reach `pg.system` (the import-boundary test), so it asks this port, which writes through the sign-in
role: one INSERT-only role, so a route cannot read or change what the log already holds."""

from __future__ import annotations

from uuid import UUID

from ..pg import audit
from ..pg.system import SystemDatabase


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
