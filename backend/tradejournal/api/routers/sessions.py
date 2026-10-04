"""A person's own sign-ins: see them, end one, or end all the others ("log out everywhere", doc 03 section 4).

The store behind `services.sessions` does the ending and writes the audit row in the same transaction; these routes
only decide who may ask. Ending the session you are using is `POST /auth/logout`, which also signs out of the identity
provider, so it is refused here with a pointer to it.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from ...auth.iphash import hash_ip
from ..deps import ERROR_RESPONSES, ConfigDep, ServicesDep, SessionDep, WriteSessionDep
from ..device import describe_device
from ..ports import AuthSession, SessionInfo
from ..schemas.sessions import ActiveSessionOut, ActiveSessionsOut

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"], responses=ERROR_RESPONSES)


def _out(rows: list[SessionInfo], current: AuthSession, ended: int = 0) -> ActiveSessionsOut:
    return ActiveSessionsOut(
        sessions=[
            ActiveSessionOut(
                id=row.id,
                current=row.id == current.id,
                device=describe_device(row.user_agent),
                signed_in_at=row.created_at,
                last_active_at=row.last_seen_at,
                expires_at=row.expires_at,
                mfa=row.mfa_at is not None,
            )
            for row in rows
        ],
        ended=ended,
    )


def _ip_hash(request: Request, config: ConfigDep) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


@router.get("", response_model=ActiveSessionsOut)
def list_sessions(session: SessionDep, services: ServicesDep) -> ActiveSessionsOut:
    """Where this account is signed in now."""
    return _out(services.sessions.list_for(session.user_id), session)


@router.delete("/{session_id}", response_model=ActiveSessionsOut)
def end_session(
    session_id: UUID, request: Request, session: WriteSessionDep, services: ServicesDep, config: ConfigDep
) -> ActiveSessionsOut:
    """Sign out one other place. Someone else's id, an ended one and an unknown one all answer the same 404."""
    if str(session_id) == session.id:
        raise HTTPException(422, "That is the session you are using. Use Sign out to end it.")
    if not services.sessions.revoke_one(session.user_id, str(session_id), ip_hash=_ip_hash(request, config)):
        raise HTTPException(404, "That session does not exist.")
    return _out(services.sessions.list_for(session.user_id), session, ended=1)


@router.delete("", response_model=ActiveSessionsOut)
def end_other_sessions(
    request: Request, session: WriteSessionDep, services: ServicesDep, config: ConfigDep
) -> ActiveSessionsOut:
    """Log out everywhere else: every other working session of this account ends; this one stays."""
    ended = services.sessions.revoke_others(session.user_id, session.id, ip_hash=_ip_hash(request, config))
    return _out(services.sessions.list_for(session.user_id), session, ended=ended)
