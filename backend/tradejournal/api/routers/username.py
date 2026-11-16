"""Picking or changing a username, and saying "not now" to being asked for one (migration 0024).

A username is 3 to 20 letters, digits or underscores, and no two accounts share one whatever the capitals. A person can
change theirs at any time. A name someone else holds and a reserved name get the same answer. Both routes take the CSRF
token like any change but are open to a read-only account too: a username is about the account, not the journal.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..deps import ERROR_RESPONSES, ServicesDep, WriteSessionDep, limited_per_person
from ..schemas.problem import Problem
from ..schemas.username import UsernameIn, UsernameOut, UsernamePromptOut, is_reserved

router = APIRouter(
    prefix="/api/v1/account",
    tags=["account"],
    responses={**ERROR_RESPONSES, 409: {"model": Problem}},
)

TAKEN = "That username is taken. Try another."


@router.put(
    "/username",
    response_model=UsernameOut,
    dependencies=[limited_per_person(lambda limits: limits.username, after_csrf=True)],
)
def set_username(body: UsernameIn, session: WriteSessionDep, services: ServicesDep) -> UsernameOut:
    """Pick a username, or change the one you have."""
    if is_reserved(body.username) or not services.accounts.set_username(session.user_id, body.username):
        raise HTTPException(409, TAKEN)
    return UsernameOut(username=body.username)


@router.post("/username/skip", response_model=UsernamePromptOut)
def skip_username(session: WriteSessionDep, services: ServicesDep) -> UsernamePromptOut:
    """Not now: stop asking after sign-in. Settings, Preferences still lets the person pick one."""
    services.accounts.skip_username(session.user_id)
    return UsernamePromptOut(ask_username=False)
