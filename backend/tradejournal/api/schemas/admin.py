"""What the operator's API says (doc 07 section 6). Account facts, plans and counters, and nothing a person wrote: a contract
test scans every schema reachable from `/api/admin/v1` for trade, note and screenshot content."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints

from .base import ApiModel
from .session import PlanOut

Status = Literal["active", "suspended", "pending_deletion"]
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]  # why, in the operator's words; kept in the audit log


class UsageOut(ApiModel):
    accounts: int
    trades: int = Field(description="How many trades the person has (a counter; the trades themselves are never shown)")
    screenshots: int
    storage_bytes: int
    last_import_at: datetime | None


class AdminUserOut(ApiModel):
    id: UUID
    email: str
    status: Status
    is_admin: bool
    created_at: datetime
    last_login_at: datetime | None
    deletion_scheduled_at: datetime | None
    plan: PlanOut
    usage: UsageOut


class AdminUserListOut(ApiModel):
    users: list[AdminUserOut]
    total: int = Field(description="How many accounts match in all, not just on this page")
    limit: int
    offset: int


class OverrideOut(ApiModel):
    id: int
    plan: Literal["free", "pro"]
    expires_at: datetime | None
    reason: str
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None
    revoke_reason: str | None


class RedemptionOut(ApiModel):
    code_label: str
    plan: str
    starts_at: datetime
    ends_at: datetime


class EventOut(ApiModel):
    id: int
    at: datetime
    actor: str = Field(description="user, admin, system or cli:<name>")
    action: str
    by: UUID | None = Field(description="The operator, when an operator did it")
    meta: dict[str, str | int | bool | None]


class AdminUserDetailOut(ApiModel):
    user: AdminUserOut
    active_sessions: int
    overrides: list[OverrideOut] = Field(description="Every override this person has had, newest first")
    redemptions: list[RedemptionOut]
    events: list[EventOut] = Field(description="The latest events about this account, newest first")


class ReasonIn(ApiModel):
    reason: Reason


class OverrideIn(ApiModel):
    plan: Literal["free", "pro"]
    expires_at: datetime | None = Field(default=None, description="When it ends; none means until revoked")
    reason: Reason


class EndedOut(ApiModel):
    ended: int = Field(description="How many sessions were ended")
