"""FastAPI dependencies shared by the routers: who is calling, and is the write really from our page."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException, Request

from ..auth.csrf import check_csrf, csrf_token
from ..filestore import FileStore
from ..pg.session import HEAVY_STATEMENT_TIMEOUT_MS, STATEMENT_TIMEOUT_MS, Database
from .config import ApiConfig
from .ports import AdminService, AuthSession, Services
from .ratelimit import RateLimits, Rule, client_address, wait_text
from .schemas import Problem
from .slots import RETRY_AFTER_SECONDS, ImportSlots

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


def require_writable(session: WriteSessionDep, services: ServicesDep) -> AuthSession:
    """`require_csrf` for a route that creates, changes or deletes the person's data. Once their time-limited access has ended
    (a beta code, a promo code) the journal is read-only: they can look at all of it and download it, and this refuses the rest.
    The routes a person needs to leave, to come back or to sign out use `WriteSessionDep` and are never refused for this."""
    if services.accounts.access(session.user_id).state == "ended":
        raise HTTPException(
            403,
            "Your beta access has ended, so your journal is read-only. You can still look at everything and download it.",
        )
    return session


WritableSessionDep = Annotated[AuthSession, Depends(require_writable)]

ADMIN_MFA_FOR = timedelta(
    minutes=15
)  # how fresh two-step verification must be for an operator to do anything (ADR-012)


def _admin(session: AuthSession, services: Services, now: datetime) -> AuthSession:
    account = services.accounts.load(session.user_id)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    if not account.is_admin:
        raise HTTPException(403, "This is for operators only.")
    if session.mfa_at is None or now - session.mfa_at > ADMIN_MFA_FOR:
        raise HTTPException(403, "Confirm it is you with two-step verification first.")
    return session


def require_admin(request: Request, session: SessionDep, services: ServicesDep) -> AuthSession:
    """An operator signed in with two-step verification in the last 15 minutes. Signed in first (401 for anyone else), then
    an operator (403), then fresh (403: the page sends them to confirm again)."""
    return _admin(session, services, request.app.state.clock())


def require_admin_write(request: Request, session: WriteSessionDep, services: ServicesDep) -> AuthSession:
    """`require_admin` for a route that changes something: the CSRF and origin checks run first (after sign-in), so a forged
    request is refused whether or not the caller is an operator."""
    return _admin(session, services, request.app.state.clock())


AdminDep = Annotated[AuthSession, Depends(require_admin)]
WriteAdminDep = Annotated[AuthSession, Depends(require_admin_write)]


def get_admin_service(services: ServicesDep) -> AdminService:
    if services.admin is None:
        raise HTTPException(503, "The operator tools are not set up on this server yet.")
    return services.admin


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


def limited_per_person(pick: Callable[[RateLimits], Rule]):
    """A route dependency: this person may do this only so often. It runs after sign-in, so a stranger cannot use up
    anyone's allowance."""

    def dependency(request: Request, session: SessionDep) -> None:
        take_place(request, pick(request.app.state.config.rate_limits), str(session.user_id))

    return Depends(dependency)


HEAVY = (
    HEAVY_STATEMENT_TIMEOUT_MS  # for `tenant.session(statement_timeout_ms=HEAVY)` in the routes that touch every row
)


def get_files(request: Request) -> FileStore:
    """Where screenshot files are kept. A server with no place to keep them answers 503 rather than losing an upload."""
    files = request.app.state.files
    if files is None:
        raise HTTPException(503, "Screenshots are not set up on this server yet.")
    return files


FilesDep = Annotated[FileStore, Depends(get_files)]


def hold_import_slot(request: Request, session: SessionDep) -> Iterator[None]:
    """A route dependency for the heavy ones (api/slots.py): the route runs only if this person has a place among the imports
    running now, and gives it back when it finishes, however it finishes. A refusal is 429 with `Retry-After`."""
    slots: ImportSlots = request.app.state.import_slots
    refusal = slots.take(session.user_id)
    if refusal is not None:
        raise HTTPException(429, refusal, headers={"Retry-After": str(RETRY_AFTER_SECONDS)})
    try:
        yield
    finally:
        slots.release(session.user_id)


ImportSlot = Depends(hold_import_slot)


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

    def session(
        self, *, snapshot: bool = False, statement_timeout_ms: int = STATEMENT_TIMEOUT_MS
    ) -> AbstractContextManager[psycopg.Connection]:
        """One transaction as this user: committed when the block ends, rolled back if it raises. `snapshot=True`
        is a read-only, repeatable-read transaction, for reading several tables that must agree (an export). A
        statement that runs longer than `statement_timeout_ms` is cancelled (HEAVY_STATEMENT_TIMEOUT_MS is for the
        routes that touch every row a person has); the answer to a timeout is a 503, see `errors.py`."""
        return self.database.tenant_session(self.user_id, snapshot=snapshot, statement_timeout_ms=statement_timeout_ms)


def get_tenant(request: Request, session: SessionDep) -> Tenant:
    """Authenticates first (so an anonymous caller gets 401 even on a server with no database), then needs the
    database. A deployment without one answers 503, never an empty list that looks like "no data"."""
    database = request.app.state.database
    if database is None:
        raise HTTPException(503, "The database is not set up on this server yet.")
    return Tenant(database, session.user_id)


TenantDep = Annotated[Tenant, Depends(get_tenant)]


def get_write_tenant(request: Request, session: WritableSessionDep) -> Tenant:
    """`get_tenant` for a route that changes something: the CSRF and origin checks run first (after sign-in), then whether
    the person's access has ended."""
    return get_tenant(request, session)


WriteTenantDep = Annotated[Tenant, Depends(get_write_tenant)]


def get_clock(request: Request) -> Callable[[], datetime]:
    """The current instant, replaceable in tests (what "today" means for a preset must be provable)."""
    return request.app.state.clock


ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]


def utc_now() -> datetime:
    return datetime.now(UTC)
