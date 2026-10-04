"""The operator's account tools: find a person, see their plan and counters, suspend or restore them, take their sessions,
schedule or cancel a deletion, give or take a plan (doc 07 section 6, ADR-012).

Every route needs an operator who confirmed two-step verification in the last 15 minutes (`require_admin`), and every route
that changes something needs the CSRF token too. The database role behind it has no grant on any tenant table, and nothing
here returns anything a person wrote. Each change, and each look at one person's detail, is an audit row about them.
Every change needs a reason (kept in the log); none of them can be done to yourself or to another operator.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request

from ...auth.iphash import hash_ip
from ..deps import ERROR_RESPONSES, AdminDep, AdminServiceDep, ConfigDep, WriteAdminDep
from ..schemas import Problem
from ..schemas.admin import (
    AdminUserDetailOut,
    AdminUserListOut,
    AdminUserOut,
    EndedOut,
    OverrideIn,
    ReasonIn,
)

router = APIRouter(
    prefix="/api/admin/v1",
    tags=["admin"],
    responses={**ERROR_RESPONSES, 404: {"model": Problem}, 409: {"model": Problem}},
)


def _ip(request: Request, config: ConfigDep) -> str | None:
    return hash_ip(config.session_secret, request.client.host if request.client else None)


@router.get("/users", response_model=AdminUserListOut)
def list_users(
    _: AdminDep,
    admin: AdminServiceDep,
    q: Annotated[str | None, Query(max_length=100, description="Part of an email address, or an account id")] = None,
    status: Literal["active", "suspended", "pending_deletion"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> AdminUserListOut:
    """Accounts, newest first. Counters and plans, never anything a person wrote."""
    return admin.list_users(q=q, status=status, limit=limit, offset=offset)


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
def user_detail(
    user_id: UUID, request: Request, operator: AdminDep, admin: AdminServiceDep, config: ConfigDep
) -> AdminUserDetailOut:
    """One account in full (still counters and facts only). Looking is itself written to the audit log."""
    return admin.user_detail(operator.user_id, user_id, ip_hash=_ip(request, config))


@router.post("/users/{user_id}/suspend", response_model=AdminUserOut)
def suspend(
    user_id: UUID, body: ReasonIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> AdminUserOut:
    """Sign the person out everywhere and refuse their sign-ins until restored. Their data is untouched."""
    return admin.suspend(operator.user_id, user_id, body.reason, ip_hash=_ip(request, config))


@router.post("/users/{user_id}/unsuspend", response_model=AdminUserOut)
def unsuspend(
    user_id: UUID, body: ReasonIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> AdminUserOut:
    return admin.unsuspend(operator.user_id, user_id, body.reason, ip_hash=_ip(request, config))


@router.post("/users/{user_id}/revoke-sessions", response_model=EndedOut)
def revoke_sessions(
    user_id: UUID, body: ReasonIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> EndedOut:
    """Sign the person out everywhere, without suspending them."""
    return EndedOut(ended=admin.revoke_sessions(operator.user_id, user_id, body.reason, ip_hash=_ip(request, config)))


@router.post("/users/{user_id}/schedule-deletion", response_model=AdminUserOut)
def schedule_deletion(
    user_id: UUID, body: ReasonIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> AdminUserOut:
    """Mark the account for deletion a week away, as if the person had asked. They can cancel it by signing in."""
    return admin.schedule_deletion(operator.user_id, user_id, body.reason, ip_hash=_ip(request, config))


@router.post("/users/{user_id}/cancel-deletion", response_model=AdminUserOut)
def cancel_deletion(
    user_id: UUID, body: ReasonIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> AdminUserOut:
    return admin.cancel_deletion(operator.user_id, user_id, body.reason, ip_hash=_ip(request, config))


@router.put("/users/{user_id}/plan-override", response_model=AdminUserOut)
def set_plan_override(
    user_id: UUID,
    body: OverrideIn,
    request: Request,
    operator: WriteAdminDep,
    admin: AdminServiceDep,
    config: ConfigDep,
) -> AdminUserOut:
    """Put the person on this plan whatever else they have, even a lower one. Replaces an override already in force."""
    return admin.set_override(
        operator.user_id, user_id, body.plan, body.expires_at, body.reason, ip_hash=_ip(request, config)
    )


@router.post("/users/{user_id}/plan-override/revoke", response_model=AdminUserOut)
def revoke_plan_override(
    user_id: UUID, body: ReasonIn, request: Request, operator: WriteAdminDep, admin: AdminServiceDep, config: ConfigDep
) -> AdminUserOut:
    """End the override in force. (A POST rather than a DELETE, so the reason travels in a body.)"""
    return admin.revoke_override(operator.user_id, user_id, body.reason, ip_hash=_ip(request, config))
