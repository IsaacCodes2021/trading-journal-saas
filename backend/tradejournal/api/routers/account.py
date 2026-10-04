"""Deleting the account (requirement F-A6; the design is in migration 0008 and `pg/deletion.py`).

Asking is deliberate in three ways: the person must have authenticated at the identity provider within the last ten minutes
(the page sends them through `/auth/login?reauth=1` first, and the sign-in flow checks the provider's answer, not just that it
was asked), they must type their own email address, and the session must carry the CSRF token like any change. Then the
account is marked, every session ends (this one too), and the account is deleted a week later unless they sign in first.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ...auth.iphash import hash_ip
from ..deps import (
    ERROR_RESPONSES,
    SESSION_COOKIE,
    ClockDep,
    ConfigDep,
    ServicesDep,
    WriteSessionDep,
    recently_authenticated,
)
from ..schemas.deletion import DeletionOut, DeletionRequest
from ..schemas.problem import Problem

router = APIRouter(
    prefix="/api/v1/account",
    tags=["account"],
    responses={**ERROR_RESPONSES, 409: {"model": Problem}},
)


@router.post("/deletion", response_model=DeletionOut)
def request_deletion(
    body: DeletionRequest,
    request: Request,
    session: WriteSessionDep,
    services: ServicesDep,
    config: ConfigDep,
    clock: ClockDep,
) -> JSONResponse:
    """Schedule this account's deletion for a week from now, and sign out everywhere."""
    if not recently_authenticated(session, clock()):
        raise HTTPException(403, "Sign in again first, so we know it is you.")
    account = services.accounts.load(session.user_id)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    if body.email.strip().casefold() != account.user.email.casefold():
        raise HTTPException(422, "That is not the email address of this account.")
    ip_hash = hash_ip(config.session_secret, request.client.host if request.client else None)
    when = services.accounts.schedule_deletion(session.user_id, ip_hash=ip_hash)
    if when is None:
        raise HTTPException(409, "This account cannot be scheduled for deletion.")
    services.sessions.revoke_all(session.user_id)
    response = JSONResponse(DeletionOut(scheduled_for=when).model_dump(mode="json"))
    response.set_cookie(SESSION_COOKIE, "", max_age=0, secure=True, httponly=True, samesite="lax", path="/")
    return response
