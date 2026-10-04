"""A person's own recent security activity: sign-ins, sign-outs, an export, a deletion request (doc 04, the audit viewer).

It reads the audit log through one narrow database function that returns this person's own rows, only the kinds listed in
`api.audit.ACTIVITY_KINDS`, and never an address or the log's details. Nothing here changes anything."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from ..deps import ERROR_RESPONSES, ServicesDep, SessionDep
from ..schemas.activity import ActivityListOut, ActivityOut

router = APIRouter(prefix="/api/v1/activity", tags=["activity"], responses=ERROR_RESPONSES)


@router.get("", response_model=ActivityListOut)
def recent_activity(
    session: SessionDep,
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=100, description="How many events, newest first")] = 30,
) -> ActivityListOut:
    """What has happened to this account lately."""
    return ActivityListOut(
        events=[
            ActivityOut(at=event.at, kind=event.kind, ended=event.ended, mfa=event.mfa)  # type: ignore[arg-type]
            for event in services.audit.recent(session.user_id, limit)
        ]
    )
