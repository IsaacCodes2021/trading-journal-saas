"""FastAPI dependencies shared by the routers: who is calling, and is the write really from our page."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ..auth.csrf import check_csrf, csrf_token
from .config import ApiConfig
from .ports import AuthSession, Services

SESSION_COOKIE = "__Host-tj_session"


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
