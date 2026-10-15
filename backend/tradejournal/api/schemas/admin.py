"""What the operator's API says (doc 07 section 6). Account facts, plans and counters, and nothing a person wrote: a contract
test scans every schema reachable from `/api/admin/v1` for trade, note and screenshot content."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

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


class AdminSubscriptionOut(ApiModel):
    """The person's subscription as the provider last told us (view only: refunds and card problems are handled at the
    provider; the operator's lever here is still the plan override)."""

    provider: str
    status: Literal["trialing", "active", "past_due", "paused", "canceled"]
    current_period_end: datetime | None
    cancel_at_period_end: bool
    cancel_requested_by: Literal["person", "deletion"] | None
    past_due_since: datetime | None
    ended_at: datetime | None
    provider_customer_id: str | None = Field(description="To find the person in the provider's dashboard")
    provider_subscription_id: str


class AdminBillingEventOut(ApiModel):
    """One of the provider's events about this person: what and when, never what it carried."""

    type: str
    received_at: datetime
    outcome: str


class AdminUserDetailOut(ApiModel):
    user: AdminUserOut
    active_sessions: int
    overrides: list[OverrideOut] = Field(description="Every override this person has had, newest first")
    redemptions: list[RedemptionOut]
    events: list[EventOut] = Field(description="The latest events about this account, newest first")
    subscription: AdminSubscriptionOut | None = Field(default=None, description="None when they never subscribed")
    billing_events: list[AdminBillingEventOut] = Field(
        default_factory=list, description="The provider's latest 10 events about this person, newest first"
    )


class ReasonIn(ApiModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled field is an error, not ignored

    reason: Reason


class OverrideIn(ApiModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled field is an error, not ignored

    plan: Literal["free", "pro"]
    expires_at: datetime | None = Field(default=None, description="When it ends; none means until revoked")
    reason: Reason


class EndedOut(ApiModel):
    ended: int = Field(description="How many sessions were ended")


# ---- invites and plan codes ------------------------------------------------------------------------------------------------

CodeStatus = Literal["works", "used_up", "expired", "revoked"]
Label = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]


class InviteOut(ApiModel):
    id: int
    label: str
    email: str | None = Field(description="If set, only this address may use it")
    max_uses: int
    used: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    grant_code_id: int | None = Field(description="The plan code whose plan a person gets when they sign up with it")
    grant_label: str | None
    status: CodeStatus


class InviteListOut(ApiModel):
    invites: list[InviteOut]
    total: int
    limit: int
    offset: int


class InviteIn(ApiModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled field is an error, not ignored

    label: Label = ""
    email: (
        Annotated[
            str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
        ]
        | None
    ) = None
    max_uses: int = Field(default=1, ge=1, le=1000)
    days: int | None = Field(
        default=None, ge=1, le=365, description="It stops working after this many days; none means it does not expire"
    )
    grant_code_id: int | None = Field(default=None, ge=1, le=2**63 - 1)


class CreatedInviteOut(ApiModel):
    invite: InviteOut
    code: str = Field(description="The code itself. It is shown this once and only its hash is kept")


GrantKind = Literal["free_days"]


class GrantCodeOut(ApiModel):
    id: int
    code: str | None = Field(
        description="The promo code as it was typed (FREE30). None for a random code, which was shown once and never kept"
    )
    kind: GrantKind = Field(description="What it gives: free_days is days of Pro")
    label: str = Field(description="The operator's note to themselves; never shown to the people who use the code")
    plan: Literal["pro"]
    days: int
    max_redemptions: int | None
    used: int
    expires_at: datetime | None
    created_by: str
    created_at: datetime
    revoked_at: datetime | None
    status: CodeStatus


class GrantCodeListOut(ApiModel):
    grant_codes: list[GrantCodeOut]
    total: int
    limit: int
    offset: int


class GrantCodeIn(ApiModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled field is an error, not ignored

    code: str | None = Field(
        default=None,
        max_length=40,
        description="A promo code to type, 3 to 20 letters and digits (FREE30); case does not matter. Kept readable. "
        "Leave it out for a random code, shown once",
    )
    kind: GrantKind = "free_days"
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = Field(
        default="",
        description="A note to yourself. Needed for a random code, so it can be told apart; optional otherwise",
    )
    plan: Literal["pro"] = "pro"
    days: int = Field(ge=1, le=30, description="How long the plan lasts once it is redeemed (30 at most)")
    max_redemptions: int | None = Field(
        default=None, ge=1, le=1_000_000, description="How many people may use it; none means any number"
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="The code stops working after this many days"
    )
    expires_on: date | None = Field(
        default=None, description="Or: the last day it works (it stops at the end of that day, UTC)"
    )

    @field_validator("code")
    @classmethod
    def _shape(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        code = value.strip().upper()
        if not (3 <= len(code) <= 20 and code.isascii() and code.isalnum()):
            raise ValueError("a promo code is 3 to 20 letters and digits, with no spaces or dashes, such as FREE30")
        return code

    @model_validator(mode="after")
    def _consistent(self) -> GrantCodeIn:
        if self.code is None and not self.label:
            raise ValueError("give a random code a note, so you can tell it apart later")
        if self.expires_in_days is not None and self.expires_on is not None:
            raise ValueError("give either expires_in_days or expires_on, not both")
        return self


class CreatedGrantCodeOut(ApiModel):
    grant_code: GrantCodeOut
    code: str = Field(description="The code itself. It is shown this once and only its hash is kept")


# ---- the audit log and the overview -----------------------------------------------------------------------------------------


class AuditEventOut(ApiModel):
    id: int
    at: datetime
    actor: str = Field(description="user, admin, system or cli:<name>")
    action: str
    actor_email: str | None = Field(description="The operator, when an operator did it")
    user_id: UUID | None
    user_email: str | None = Field(description="Who it is about; none once that account is deleted")
    meta: dict[str, str | int | bool | None]


class AuditPageOut(ApiModel):
    events: list[AuditEventOut] = Field(description="Newest first")
    next_before: int | None = Field(
        description="Pass this as `before` to read the next page; none when there is no more"
    )


class OverviewOut(ApiModel):
    accounts: int
    signups_7d: int
    active_7d: int = Field(description="Accounts that signed in during the last 7 days")
    active: int
    suspended: int
    pending_deletion: int
    pro: int
    free: int
    open_invites: int = Field(description="Invites that still work")
    open_grant_codes: int = Field(description="Plan codes that still work")
    grants_ending_7d: int = Field(
        description="People whose free days from a code or an invite end in the next 7 days, then read-only"
    )
    paying: int = Field(default=0, description="People with a subscription that gives Pro now (billing)")
    on_trial: int = Field(default=0, description="People whose 7-day trial is running (billing)")


# ---- the beta: who joined and where each person stands -----------------------------------------------------------------


BetaState = Literal["waiting", "active", "ended", "revoked"]


class BetaPersonOut(ApiModel):
    id: int
    first_name: str
    email: str
    prop_firm: str
    joined_at: datetime
    state: BetaState = Field(
        description="waiting: joined, no account yet; active: the 30 days are running; ended: they ran out; revoked: an "
        "operator ended it"
    )
    code: Literal["unused", "used", "expired", "withdrawn"] = Field(description="What became of their personal code")
    access_ends_at: datetime | None = Field(description="When the 30 days end (or ended); none until they sign up")
    revoked_at: datetime | None
    notice_sent_at: datetime | None = Field(description="When the 'your access has ended' email left, if it has")
    code_email_sent_at: datetime | None = Field(description="When the email with their code left, if it has")
    code_email_failures: int


class BetaListOut(ApiModel):
    people: list[BetaPersonOut]
    total: int = Field(description="How many match the filters")
    limit: int
    offset: int
    counts: dict[str, int] = Field(description="How many people are in each state, and `all`")


class BetaRemovedOut(ApiModel):
    removed: int = Field(description="The sign-up that was removed, with every code tied to its address")


class BetaRevokeOut(ApiModel):
    person: BetaPersonOut
    notice: Literal["sent", "pending", "not_needed"] = Field(
        description="sent: the 'access ended' email left; pending: it did not (it is tried again); not_needed: they had no "
        "account, so only their code was withdrawn"
    )
