"""FastAPI dependencies shared by the routers: who is calling, and is the write really from our page."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import Depends, HTTPException, Request

from ..auth.csrf import check_csrf, csrf_token
from ..pg.session import Database
from .config import ApiConfig
from .ports import AuthSession, Services
from .schemas import Problem

SESSION_COOKIE = "__Host-tj_session"
# What errors.py actually returns, so the generated client types its errors correctly. Every router declares these.
ERROR_RESPONSES: dict[int | str, dict] = {401: {"model": Problem}, 403: {"model": Problem}, 422: {"model": Problem}}


def get_config(request: Request) -> ApiConfig:
    return request.app.state.config


ConfigDep = Annotated[ApiConfig, Depends(get_config)]


def get_services(request: Request) -> Services:
    services = request.app.state.services
    if services is None:
        raise HTTPException(503, "Sign-in is not set up on this server yet.")
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


def current_session(request: Request, services: ServicesDep) -> AuthSession:
    session = services.sessions.lookup(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(401, "Not signed in.")
    return session


SessionDep = Annotated[AuthSession, Depends(current_session)]


def require_csrf(request: Request, session: SessionDep, config: ConfigDep) -> AuthSession:
    """Use on every route that changes something. Authenticates first, so an anonymous caller gets 401, not 403."""
    check_csrf(request, csrf_token(config.session_secret, session.id), config.app_origin)
    return session


WriteSessionDep = Annotated[AuthSession, Depends(require_csrf)]


@dataclass(frozen=True)
class Tenant:
    """The signed-in user's slice of the database. A handler opens one transaction as that user and nothing else:
    there is no way to name another user's id here, and row-level security would refuse it anyway."""

    database: Database
    user_id: UUID

    def session(self) -> AbstractContextManager[psycopg.Connection]:
        """One transaction as this user: committed when the block ends, rolled back if it raises."""
        return self.database.tenant_session(self.user_id)


def get_tenant(request: Request, session: SessionDep) -> Tenant:
    """Authenticates first (so an anonymous caller gets 401 even on a server with no database), then needs the
    database. A deployment without one answers 503, never an empty list that looks like "no data"."""
    database = request.app.state.database
    if database is None:
        raise HTTPException(503, "The database is not set up on this server yet.")
    return Tenant(database, session.user_id)


TenantDep = Annotated[Tenant, Depends(get_tenant)]


def get_clock(request: Request) -> Callable[[], datetime]:
    """The current instant, replaceable in tests (what "today" means for a preset must be provable)."""
    return request.app.state.clock


ClockDep = Annotated[Callable[[], datetime], Depends(get_clock)]


def utc_now() -> datetime:
    return datetime.now(UTC)
