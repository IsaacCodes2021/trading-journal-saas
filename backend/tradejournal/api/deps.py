"""FastAPI dependencies shared by the routers: who is calling, and is the write really from our page."""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException, Request

from ..auth.csrf import check_csrf, csrf_token
from ..pg.session import Database
from .config import ApiConfig
from .ports import AuthSession, Services
from .ratelimit import RateLimits, Rule, client_address, wait_text
from .schemas import Problem

SESSION_COOKIE = "__Host-tj_session"
# What errors.py actually returns, so the generated client types its errors correctly. Every router declares these.
ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"model": Problem},
    403: {"model": Problem},
    422: {"model": Problem},
    429: {"model": Problem},
}


def get_config(request: Request) -> ApiConfig:
    return request.app.state.config


ConfigDep = Annotated[ApiConfig, Depends(get_config)]


def get_services(request: Request) -> Services:
    services = request.app.state.services
    if services is None:
        raise HTTPException(503, "Sign-in is not set up on this server yet.")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def take_place(request: Request, rule: Rule, key: str) -> None:
    """Spend one place in a rate limit, or refuse with 429 and how long to wait."""
    config: ApiConfig = request.app.state.config
    if not config.rate_limits.enabled:
        return
    wait = request.app.state.limiter.hit(rule, key)
    if wait > 0:
        raise HTTPException(
            429,
            f"Too many requests. Try again in {wait_text(wait)}.",
            headers={"Retry-After": str(max(1, math.ceil(wait)))},
        )


def client_key(request: Request) -> str:
    """Who is asking, for limits that apply before anyone is signed in (see api/ratelimit.py on the address)."""
    config: ApiConfig = request.app.state.config
    return client_address(request.headers, request.client.host if request.client else None, config.client_ip_header)


RECENT_AUTH_FOR = timedelta(minutes=10)  # how fresh a sign-in must be to delete an account


def recently_authenticated(session: AuthSession, now: datetime | None = None) -> bool:
    return (now or datetime.now(UTC)) - session.authenticated_at <= RECENT_AUTH_FOR


def current_session(request: Request, services: ServicesDep) -> AuthSession:
    session = services.sessions.lookup(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(401, "Not signed in.")
    take_place(request, request.app.state.config.rate_limits.api, str(session.user_id))
    return session


SessionDep = Annotated[AuthSession, Depends(current_session)]


def require_csrf(request: Request, session: SessionDep, config: ConfigDep) -> AuthSession:
    """Use on every route that changes something. Authenticates first, so an anonymous caller gets 401, not 403."""
    check_csrf(request, csrf_token(config.session_secret, session.id), config.app_origin)
    return session


WriteSessionDep = Annotated[AuthSession, Depends(require_csrf)]


def limited_per_person(pick: Callable[[RateLimits], Rule]):
    """A route dependency: this person may do this only so often. It runs after sign-in, so a stranger cannot use up
    anyone's allowance."""

    def dependency(request: Request, session: SessionDep) -> None:
        take_place(request, pick(request.app.state.config.rate_limits), str(session.user_id))

    return Depends(dependency)


def limited_per_client(pick: Callable[[RateLimits], Rule]):
    """A route dependency for what happens before sign-in: this client address may do this only so often."""

    def dependency(request: Request) -> None:
        take_place(request, pick(request.app.state.config.rate_limits), client_key(request))

    return Depends(dependency)


@dataclass(frozen=True)
class Tenant:
    """The signed-in user's slice of the database. A handler opens one transaction as that user and nothing else:
    there is no way to name another user's id here, and row-level security would refuse it anyway."""

    database: Database
    user_id: UUID

    def session(self, *, snapshot: bool = False) -> AbstractContextManager[psycopg.Connection]:
        """One transaction as this user: committed when the block ends, rolled back if it raises. `snapshot=True`
        is a read-only, repeatable-read transaction, for reading several tables that must agree (an export)."""
        return self.database.tenant_session(self.user_id, snapshot=snapshot)


def get_tenant(request: Request, session: SessionDep) -> Tenant:
    """Authenticates first (so an anonymous caller gets 401 even on a server with no database), then needs the
    database. A deployment without one answers 503, never an empty list that looks like "no data"."""
    database = request.app.state.database
    if database is None:
        raise HTTPException(503, "The database is not set up on this server yet.")
    return Tenant(database, session.user_id)


TenantDep = Annotated[Tenant, Depends(get_tenant)]


def get_write_tenant(request: Request, session: WriteSessionDep) -> Tenant:
    """`get_tenant` for a route that changes something: the CSRF and origin checks run first (after sign-in)."""
    return get_tenant(request, session)


WriteTenantDep = Annotated[Tenant, Depends(get_write_tenant)]


def get_clock(request: Request) -> Callable[[], datetime]:
    """The current instant, replaceable in tests (what "today" means for a preset must be provable)."""
    return request.app.state.clock


ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]


def utc_now() -> datetime:
    return datetime.now(UTC)
