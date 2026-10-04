"""Development sign-in: every request is one configured user. Never for production.

    TJ_DATABASE_URL        the request role (app_rw)
    TJ_LOGIN_DATABASE_URL  the login role (app_login)
    TJ_DEV_USER_ID         the user to be (a row of the users table)

`ApiConfig` refuses `TJ_DEV_USER_ID` when `TJ_ENV=production`, and the app only builds this when it is set. The
cookie is a fixed word, so a front-end dev server can send it for the browser (`frontend/vite.config.ts`). Real
sign-in (M5) replaces it with sessions in Postgres behind the same `SessionStore` interface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ..pg.session import Database
from ..pg.system import SystemDatabase
from .audit import PgAuditLog
from .pg_services import PgAccountService
from .ports import AuthSession, Services, SessionInfo

DEV_SESSION_TOKEN = "dev-session"  # noqa: S105  (a fixed word for local development, refused in production by ApiConfig)


class DevSessionStore:
    def __init__(self, user_id: UUID) -> None:
        self._user_id = user_id

    def lookup(self, token: str | None) -> AuthSession | None:
        if token != DEV_SESSION_TOKEN:
            return None
        # MFA is "fresh" so the operator screens can be tried; the real value comes from the identity provider.
        now = datetime.now(UTC)
        return AuthSession(id="dev", user_id=self._user_id, mfa_at=now, authenticated_at=now)

    # The development sign-in is one fixed session: it can be listed, and there is nothing else to end.
    def list_for(self, user_id: UUID) -> list[SessionInfo]:
        now = datetime.now(UTC)
        return [SessionInfo(id="dev", created_at=now, last_seen_at=now, expires_at=now, mfa_at=now, user_agent=None)]

    def revoke_one(self, user_id: UUID, session_id: str, *, ip_hash: str | None = None) -> bool:
        return False

    def revoke_others(self, user_id: UUID, keep_id: str, *, ip_hash: str | None = None) -> int:
        return 0

    def revoke_all(self, user_id: UUID) -> int:
        return 0  # the fixed development session cannot be ended


def dev_services(user_id: UUID, database: Database, system: SystemDatabase) -> Services:
    return Services(
        sessions=DevSessionStore(user_id), accounts=PgAccountService(database, system), audit=PgAuditLog(system)
    )
