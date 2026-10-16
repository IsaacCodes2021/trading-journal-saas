"""The operator's invites, plan codes, audit log and overview (doc 07 section 6, ADR-012).

Same rules as the account tools: an operator with two-step verification confirmed in the last 15 minutes, CSRF checked first on
every write, a reason for every revoke, and an audit row for everything made or revoked. A new code is returned once, in the
answer that makes it, and never again: only its hash is stored, and the operator's database role cannot read a hash back.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request

from ...auth.iphash import hash_ip
from ..deps import ERROR_RESPONSES, AdminDep, AdminServiceDep, ConfigDep, WriteAdminDep
from ..emails import access_ended_email
from ..mailer import MailError
from ..schemas import Problem
from ..schemas.admin import (
    AuditPageOut,
    BetaListOut,
    BetaRemovedOut,
    BetaRevokeOut,
    CreatedGrantCodeOut,
    CreatedInviteOut,
    GrantCodeIn,
    GrantCodeListOut,
    GrantCodeOut,
    InviteIn,
    InviteListOut,
    InviteOut,
    OverviewOut,
    ReasonIn,
)

logger = logging.getLogger("tj.admin")

router = APIRouter(
    prefix="/api/admin/v1",
    tags=["admin"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}},
)

Limit = Annotated[int, Query(ge=1, le=100)]
RowId = Annotated[
    int, Path(ge=1, le=2**63 - 1)
]  # a database id: a larger number is not an id, and the database would refuse it
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def _ip(request: Request, config: ConfigDep) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


@router.get("/invites", response_model=InviteListOut)
def list_invites(_: AdminDep, admin: AdminServiceDep, limit: Limit = 50, offset: Offset = 0) -> InviteListOut:
    """Invites, newest first: how many times each was used, and whether it still works."""
    return admin.list_invites(limit=limit, offset=offset)


@router.post("/invites", response_model=CreatedInviteOut)
def create_invite(
    body: InviteIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> CreatedInviteOut:
    """Make an invite. The code is in this answer and nowhere else, ever."""
    return admin.create_invite(
        operator.user_id,
        label=body.label,
        email=body.email,
        max_uses=body.max_uses,
        days=body.days,
        grant_code_id=body.grant_code_id,
        ip_hash=_ip(request, config),
    )


@router.post("/invites/{invite_id}/revoke", response_model=InviteOut)
def revoke_invite(
    invite_id: RowId,
    body: ReasonIn,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
) -> InviteOut:
    """Stop an invite being used. People who already signed up with it keep their accounts."""
    return admin.revoke_invite(operator.user_id, invite_id, body.reason, ip_hash=_ip(request, config))


@router.get("/grant-codes", response_model=GrantCodeListOut)
def list_grant_codes(
    _: AdminDep,
    admin: AdminServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
    show: Annotated[
        Literal["works", "ended", "revoked"] | None,
        Query(description="Only codes that work, that are used up or expired (ended), or that were revoked"),
    ] = None,
) -> GrantCodeListOut:
    """Promo and plan codes, newest first: how many people used each, and whether it still works."""
    return admin.list_grant_codes(limit=limit, offset=offset, show=show)


@router.post("/grant-codes", response_model=CreatedGrantCodeOut)
def create_grant_code(
    body: GrantCodeIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> CreatedGrantCodeOut:
    """Make a promo code (typed, kept readable) or a random plan code (in this answer and nowhere else, ever)."""
    return admin.create_grant_code(
        operator.user_id,
        label=body.label,
        plan=body.plan,
        days=body.days,
        max_redemptions=body.max_redemptions,
        expires_in_days=body.expires_in_days,
        code=body.code,
        expires_on=body.expires_on,
        ip_hash=_ip(request, config),
    )


@router.post("/grant-codes/{grant_id}/revoke", response_model=GrantCodeOut)
def revoke_grant_code(
    grant_id: RowId,
    body: ReasonIn,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
) -> GrantCodeOut:
    """Stop a plan code being used. People who already used it keep their plan."""
    return admin.revoke_grant_code(operator.user_id, grant_id, body.reason, ip_hash=_ip(request, config))


@router.get("/beta", response_model=BetaListOut)
def list_beta(
    _: AdminDep,
    admin: AdminServiceDep,
    state: Annotated[
        Literal["waiting", "active", "ended", "revoked"] | None, Query(description="Only people in this state")
    ] = None,
    q: Annotated[str | None, Query(max_length=100, description="Part of a name, address or prop firm")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> BetaListOut:
    """Everyone who joined the beta, newest first: their code, their 30 days, and whether the ending email has left."""
    return admin.list_beta(state=state, q=q, limit=limit, offset=offset)


@router.post("/beta/{signup_id}/revoke", response_model=BetaRevokeOut)
def revoke_beta(
    signup_id: RowId,
    body: ReasonIn,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
) -> BetaRevokeOut:
    """End a person's beta access now, and email them that their access has ended. For someone who has not made an
    account yet, withdraw their code instead (nothing to email). The reason is kept in the audit log and never sent."""
    done = admin.revoke_beta(operator.user_id, signup_id, body.reason, ip_hash=_ip(request, config))
    if done.notice_to is None or done.redemption_id is None:
        return BetaRevokeOut(person=done.person, notice="not_needed")
    mailer = request.app.state.mailer
    sent = False
    if mailer is not None:
        try:
            mailer.send(access_ended_email(to=done.notice_to, first_name=done.notice_name))
            sent = True
        except MailError as problem:
            logger.warning("access-ended email not sent: %s", problem.reason)  # the reason code only
    if sent:
        admin.beta_notice_sent(done.redemption_id)
    return BetaRevokeOut(person=done.person, notice="sent" if sent else "pending")


@router.post("/beta/{signup_id}/remove", response_model=BetaRemovedOut)
def remove_beta(
    signup_id: RowId,
    body: ReasonIn,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
) -> BetaRemovedOut:
    """Remove a sign-up that has no account, and every code tied to its address: for a removal request by email. Someone
    with an account is refused (409): deleting the account removes the sign-up with it. The reason is kept in the audit
    log with the sign-up's number, never the address."""
    return admin.remove_beta(operator.user_id, signup_id, body.reason, ip_hash=_ip(request, config))


@router.get("/audit", response_model=AuditPageOut)
def audit(
    _: AdminDep,
    admin: AdminServiceDep,
    action: Annotated[str | None, Query(max_length=64, description="Exactly this kind of event")] = None,
    q: Annotated[str | None, Query(max_length=100, description="Part of an email address, or an account id")] = None,
    actor: Annotated[str | None, Query(max_length=64, description="user, admin, system or cli:<name>")] = None,
    days: Annotated[int | None, Query(ge=1, le=3650, description="Only the last this many days")] = None,
    limit: Limit = 50,
    before: Annotated[
        int | None, Query(ge=1, le=2**63 - 1, description="The last event id already seen, to read the next page")
    ] = None,
) -> AuditPageOut:
    """The audit log, newest first. An event that names an account shows its email; nothing here is a person's own data."""
    return admin.audit(action=action, q=q, actor=actor, days=days, limit=limit, before=before)


@router.get("/metrics/overview", response_model=OverviewOut)
def overview(_: AdminDep, admin: AdminServiceDep) -> OverviewOut:
    """Accounts by status, who signed up and signed in this week, how many are on Pro, and the invites and codes that work."""
    return admin.overview()
