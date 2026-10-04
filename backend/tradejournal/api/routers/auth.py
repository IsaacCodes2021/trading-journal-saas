"""Sign in and out: browser redirects, not JSON (doc 03 section 4). Hidden from the OpenAPI document except logout.

`/auth/login` sends the browser to the identity provider; `/auth/callback` is where it comes back; `POST /auth/logout`
ends the session. The flow itself is `auth/flow.py`: these routes only read what the browser sent and set the cookies
from what comes back, and never put a reason for a failure in front of the browser (only a short code).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import Field

from ...auth.flow import ERR_FAILED, AuthFlow
from ...pg.sessions import ABSOLUTE_LIFETIME
from ..deps import (
    ERROR_RESPONSES,
    SESSION_COOKIE,
    WriteSessionDep,
    client_key,
    limited_per_client,
    take_place,
)
from ..schemas import ApiModel

LOGIN_COOKIE = "__Host-tj_login"
LOGIN_COOKIE_SECONDS = 600

public = APIRouter(include_in_schema=False)
router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)


def flow_of(request: Request) -> AuthFlow:
    flow = request.app.state.auth
    if flow is None:
        raise HTTPException(503, "Sign-in is not set up on this server yet.")
    return flow


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _cookie(response: RedirectResponse, name: str, value: str, max_age: int) -> None:
    # `__Host-`: Secure, Path=/, no Domain. HttpOnly keeps it from scripts; Lax lets the provider's redirect carry it.
    response.set_cookie(name, value, max_age=max_age, secure=True, httponly=True, samesite="lax", path="/")


def _clear(response: RedirectResponse, name: str) -> None:
    response.set_cookie(name, "", max_age=0, secure=True, httponly=True, samesite="lax", path="/")


@public.get("/auth/login", dependencies=[limited_per_client(lambda limits: limits.login)])
def login(
    request: Request, next: str | None = None, invite: str | None = None, stepup: bool = False
) -> RedirectResponse:
    """Start signing in (or up, with `invite`). `next` is a path on this site to land on afterwards."""
    if invite:  # guessing codes is slower than signing in, but it is the one thing worth a stricter limit of its own
        take_place(request, request.app.state.config.rate_limits.invite_attempts, client_key(request))
    start = flow_of(request).begin(next_url=next, invite=invite, step_up=stepup)
    response = RedirectResponse(start.redirect_url, status_code=302)
    _cookie(response, LOGIN_COOKIE, start.state_cookie, LOGIN_COOKIE_SECONDS)
    return response


@public.get("/auth/callback", dependencies=[limited_per_client(lambda limits: limits.callback)])
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Where the identity provider sends the browser back. Whatever happens, the login-state cookie is used up."""
    result = flow_of(request).complete(
        state_cookie=request.cookies.get(LOGIN_COOKIE),
        code=code,
        state=state,
        provider_error=error,
        previous_session=request.cookies.get(SESSION_COOKIE),
        user_agent=request.headers.get("user-agent"),
        client_ip=_client_ip(request),
    )
    target = result.redirect_to if result.error is None else f"/invite?error={result.error or ERR_FAILED}"
    response = RedirectResponse(target, status_code=303)  # 303: the browser follows with a GET
    _clear(response, LOGIN_COOKIE)
    if result.session_token:
        _cookie(response, SESSION_COOKIE, result.session_token, int(ABSOLUTE_LIFETIME.total_seconds()))
    return response


class LogoutOut(ApiModel):
    redirect: str = Field(description="Where to send the browser next: the provider's logout page, or this site's home")


@router.post("/logout", response_model=LogoutOut)
def logout(request: Request, session: WriteSessionDep) -> LogoutOut:
    """End this session. The page then sends the browser to `redirect`, which ends the provider's own session too."""
    token = request.cookies.get(SESSION_COOKIE, "")
    target = flow_of(request).logout(
        token=token, user_id=session.user_id, ip_hash=flow_of(request).hash_ip(_client_ip(request))
    )
    return LogoutOut(redirect=target)
