"""The SPA's first call, and the user's own preferences."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException

from ...auth.csrf import csrf_token
from ..deps import (
    ERROR_RESPONSES,
    ClockDep,
    ConfigDep,
    ServicesDep,
    SessionDep,
    WriteSessionDep,
    recently_authenticated,
)
from ..schemas import FlagsOut, SessionOut, SettingsOut, SettingsPatch

MFA_FRESH_FOR = timedelta(minutes=15)  # doc 03 section 4: admin routes ask for MFA no older than this

router = APIRouter(prefix="/api/v1", tags=["session"], responses=ERROR_RESPONSES)


@router.get("/session", response_model=SessionOut)
def get_session(session: SessionDep, services: ServicesDep, config: ConfigDep, clock: ClockDep) -> SessionOut:
    account = services.accounts.load(session.user_id)
    if account is None:
        raise HTTPException(401, "Not signed in.")
    now = clock()  # the app's clock, so what "recently" means can be proved with a clock the test moves
    mfa_fresh = session.mfa_at is not None and now - session.mfa_at <= MFA_FRESH_FOR
    return SessionOut(
        user=account.user,
        settings=account.settings,
        plan=account.plan,
        entitlements=account.entitlements,
        flags=FlagsOut(
            is_admin=account.is_admin, mfa_fresh=mfa_fresh, recently_authenticated=recently_authenticated(session, now)
        ),
        csrf_token=csrf_token(config.session_secret, session.id),
        app_version=config.app_version,
    )


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(changes: SettingsPatch, session: WriteSessionDep, services: ServicesDep) -> SettingsOut:
    return services.accounts.update_settings(session.user_id, changes)
