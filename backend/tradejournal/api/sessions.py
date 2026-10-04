"""The real session store: cookies backed by the `sessions` table (replaces the in-memory fakes and the dev cookie)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from ..pg import audit
from ..pg import sessions as pg_sessions
from ..pg.system import SystemDatabase
from .ports import AuthSession, SessionInfo

CACHE_SECONDS = 60  # doc 03: every request re-checks the session, cached for a minute per instance


class PgSessionStore:
    """`ports.SessionStore` on Postgres, with a short per-instance cache so a request is not a database round trip.

    The cache is the price of that speed: a session revoked on another instance (or a user suspended) stops working
    here within `CACHE_SECONDS`, and at once on the instance that revoked it. Only live sessions are cached, so an
    unknown token is looked up every time (and costs the caller nothing it did not already have)."""

    def __init__(
        self,
        system: SystemDatabase,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._system = system
        self._monotonic = monotonic
        self._clock = clock
        self._cache: dict[str, tuple[float, AuthSession]] = {}
        self._lock = threading.Lock()

    def lookup(self, token: str | None) -> AuthSession | None:
        if not token:
            return None
        with self._lock:
            hit = self._cache.get(token)
            if hit and self._monotonic() - hit[0] < CACHE_SECONDS:
                return hit[1]
            self._cache.pop(token, None)
        with self._system.system_session("login") as conn:
            live = pg_sessions.lookup(conn, token, now=self._clock())
        if live is None:
            return None
        session = AuthSession(
            id=str(live.public_id),
            user_id=live.user_id,
            mfa_at=live.mfa_at,
            authenticated_at=live.authenticated_at,
        )
        with self._lock:
            self._cache[token] = (self._monotonic(), session)
            if len(self._cache) > 10_000:  # a bound, not a policy: drop the oldest tenth
                for key in sorted(self._cache, key=lambda k: self._cache[k][0])[:1000]:
                    del self._cache[key]
        return session

    def create(
        self,
        user_id: UUID,
        *,
        mfa_at: datetime | None = None,
        authenticated_at: datetime | None = None,
        user_agent: str | None = None,
        ip_hash: str | None = None,
    ) -> str:
        with self._system.system_session("login") as conn:
            return pg_sessions.create(
                conn,
                user_id,
                mfa_at=mfa_at,
                authenticated_at=authenticated_at,
                user_agent=user_agent,
                ip_hash=ip_hash,
                now=self._clock(),
            )

    def revoke(self, token: str) -> None:
        with self._system.system_session("login") as conn:
            pg_sessions.revoke(conn, token, now=self._clock())
        with self._lock:
            self._cache.pop(token, None)

    def revoke_all(self, user_id: UUID, *, except_token: str | None = None) -> int:
        with self._system.system_session("login") as conn:
            ended = pg_sessions.revoke_all(conn, user_id, except_token=except_token, now=self._clock())
        with self._lock:
            for token, (_, session) in list(self._cache.items()):
                if session.user_id == user_id and token != except_token:
                    del self._cache[token]
        return ended

    # ---- a person's own sessions ----------------------------------------------------------------------------------

    def list_for(self, user_id: UUID) -> list[SessionInfo]:
        with self._system.system_session("login") as conn:
            rows = pg_sessions.list_active(conn, user_id, now=self._clock())
        return [
            SessionInfo(
                id=str(row.public_id),
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
                expires_at=row.expires_at,
                mfa_at=row.mfa_at,
                user_agent=row.user_agent,
            )
            for row in rows
        ]

    def revoke_one(self, user_id: UUID, session_id: str, *, ip_hash: str | None = None) -> bool:
        try:
            public_id = UUID(session_id)
        except ValueError:
            return False
        with self._system.system_session("login") as conn:
            ended = pg_sessions.revoke_one(conn, user_id, public_id, now=self._clock())
            if ended:  # the end and its record are one transaction: neither happens alone
                audit.record(conn, "session_revoked", user_id=user_id, ip_hash=ip_hash)
        if ended:
            self._forget(lambda session: session.id == session_id)
        return ended

    def revoke_others(self, user_id: UUID, keep_id: str, *, ip_hash: str | None = None) -> int:
        try:
            keep = UUID(keep_id)
        except ValueError:
            return 0  # not a real session id (the development session): there is nothing of ours to keep or end
        with self._system.system_session("login") as conn:
            ended = pg_sessions.revoke_others(conn, user_id, keep, now=self._clock())
            audit.record(conn, "logout_everywhere", user_id=user_id, ip_hash=ip_hash, meta={"ended": ended})
        self._forget(lambda session: session.user_id == user_id and session.id != keep_id)
        return ended

    def _forget(self, matches: Callable[[AuthSession], bool]) -> None:
        """Drop cached sessions that were just ended, so this instance stops honouring them at once."""
        with self._lock:
            for token, (_, session) in list(self._cache.items()):
                if matches(session):
                    del self._cache[token]
