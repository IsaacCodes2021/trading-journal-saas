"""What the API needs from the outside world, as interfaces.

Sessions live in Postgres and sign-in goes through the identity provider (M2 and M5, after spikes S1b and S2). The
routers depend only on these two protocols, so they can be built and tested now, and the real implementations slot in
later without touching a route. Tests use the in-memory versions in `tests/api_fakes.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from .schemas import PlanOut, SettingsOut, SettingsPatch, UserOut
from .schemas.admin import (
    AdminUserDetailOut,
    AdminUserListOut,
    AdminUserOut,
    AuditPageOut,
    CreatedGrantCodeOut,
    CreatedInviteOut,
    GrantCodeListOut,
    GrantCodeOut,
    InviteListOut,
    InviteOut,
    OverviewOut,
)

# What /readyz asks: (ready, {check name: a short state such as "ok" or "unreachable"}). The states are public, so they
# are words and never error text, hosts or addresses.
ReadinessCheck = Callable[[], tuple[bool, dict[str, str]]]


@dataclass(frozen=True)
class AuthSession:
    id: str  # opaque and stable for the life of the session; the CSRF token is derived from it
    user_id: UUID
    mfa_at: datetime | None  # when the identity provider last reported MFA, if ever
    authenticated_at: datetime  # when the person last authenticated at the provider (a fresh sign-in, not an old one)


@dataclass(frozen=True)
class SessionInfo:
    """One of a person's working sessions, as far as they may see it (never the token or what it is stored as)."""

    id: str  # the same opaque id as AuthSession.id
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    mfa_at: datetime | None
    user_agent: str | None


@dataclass(frozen=True)
class Account:
    user: UserOut
    settings: SettingsOut
    plan: PlanOut
    entitlements: dict[str, int | bool]
    is_admin: bool


@dataclass(frozen=True)
class GrantResult:
    """What redeeming a plan code did. A code that did not work is one answer, whatever the reason (a guess teaches nothing)."""

    status: Literal["ok", "refused", "active"]
    plan: PlanOut | None = None  # the person's plan afterwards (status "ok")
    ends_at: datetime | None = None  # when the plan they already have from a code ends (status "active")


class SessionStore(Protocol):
    def lookup(self, token: str | None) -> AuthSession | None:
        """The live session for a cookie value, or None (unknown, expired, revoked, or the user is suspended)."""
        ...

    def list_for(self, user_id: UUID) -> list[SessionInfo]:
        """The person's working sessions, most recently used first."""
        ...

    def revoke_one(self, user_id: UUID, session_id: str, *, ip_hash: str | None = None) -> bool:
        """End one of this person's sessions. False if there is no such working session of theirs (someone else's looks
        the same). Takes effect at once on this instance and within a minute on others."""
        ...

    def revoke_others(self, user_id: UUID, keep_id: str, *, ip_hash: str | None = None) -> int:
        """Log out everywhere else: end all of this person's working sessions but `keep_id`. Returns how many."""
        ...

    def revoke_all(self, user_id: UUID) -> int:
        """End every session of this person, this one too, at once on this instance. Returns how many."""
        ...


class AccountService(Protocol):
    def load(self, user_id: UUID) -> Account | None:
        """The signed-in user's account, or None if it no longer exists."""
        ...

    def update_settings(self, user_id: UUID, changes: SettingsPatch) -> SettingsOut:
        """Apply the fields that are set (not None) and return all settings."""
        ...

    def schedule_deletion(self, user_id: UUID, *, ip_hash: str | None = None) -> datetime | None:
        """Mark the account for deletion a grace period from now and record it. Returns when it will be deleted, or None
        if the account is not active (already scheduled, or suspended). Ending the sessions is the caller's job."""
        ...

    def redeem_grant(self, user_id: UUID, code: str, *, ip_hash: str | None = None) -> GrantResult:
        """Use a plan code for this person and record it. One at a time: a person with a plan from a code that has not
        ended gets "active" (when it ends), and a code that does not work gets "refused"."""
        ...


@dataclass(frozen=True)
class ActivityEvent:
    """Something that happened to a person's own account, as far as they may see it (never an address or the log's details)."""

    at: datetime
    kind: str  # a word from `api.audit.ACTIVITY_KINDS`
    ended: int | None  # for "signed out everywhere else": how many places
    mfa: bool | None  # for a sign-in: whether it used two-step verification


class AuditLog(Protocol):
    def record(
        self,
        user_id: UUID,
        action: str,
        *,
        ip_hash: str | None = None,
        meta: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        """Write one security event about this person to the append-only log. `meta` is for small facts (a count, a
        size), never for anything a person wrote."""
        ...

    def recent(self, user_id: UUID, limit: int = 30) -> list[ActivityEvent]:
        """This person's own recent events, newest first (at most 100)."""
        ...


class AdminRefusal(Exception):
    """The operator asked for something that cannot or must not be done; `detail` is a sentence safe to show them."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class AdminService(Protocol):
    """What an operator does with accounts (doc 07 section 6). Everything takes the operator's id (`actor`) and the target's;
    every change, and every look at one person's detail, is written to the audit log by the implementation. Nothing here
    returns anything a person wrote: counters, plans and account facts only. A refusal raises `AdminRefusal`."""

    def list_users(self, *, q: str | None, status: str | None, limit: int, offset: int) -> AdminUserListOut: ...

    def user_detail(self, actor: UUID, target: UUID, *, ip_hash: str | None = None) -> AdminUserDetailOut: ...

    def suspend(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut: ...

    def unsuspend(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> AdminUserOut: ...

    def revoke_sessions(self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None) -> int: ...

    def schedule_deletion(
        self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None
    ) -> AdminUserOut: ...

    def cancel_deletion(
        self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None
    ) -> AdminUserOut: ...

    def set_override(
        self,
        actor: UUID,
        target: UUID,
        plan: str,
        expires_at: datetime | None,
        reason: str,
        *,
        ip_hash: str | None = None,
    ) -> AdminUserOut: ...

    def revoke_override(
        self, actor: UUID, target: UUID, reason: str, *, ip_hash: str | None = None
    ) -> AdminUserOut: ...

    def list_invites(self, *, limit: int, offset: int) -> InviteListOut: ...

    def create_invite(
        self,
        actor: UUID,
        *,
        label: str,
        email: str | None,
        max_uses: int,
        days: int | None,
        grant_code_id: int | None,
        ip_hash: str | None = None,
    ) -> CreatedInviteOut: ...

    def revoke_invite(self, actor: UUID, invite_id: int, reason: str, *, ip_hash: str | None = None) -> InviteOut: ...

    def list_grant_codes(self, *, limit: int, offset: int) -> GrantCodeListOut: ...

    def create_grant_code(
        self,
        actor: UUID,
        *,
        label: str,
        plan: str,
        days: int,
        max_redemptions: int | None,
        expires_in_days: int | None,
        ip_hash: str | None = None,
    ) -> CreatedGrantCodeOut: ...

    def revoke_grant_code(
        self, actor: UUID, grant_id: int, reason: str, *, ip_hash: str | None = None
    ) -> GrantCodeOut: ...

    def audit(
        self,
        *,
        action: str | None,
        q: str | None,
        actor: str | None,
        days: int | None,
        limit: int,
        before: int | None,
    ) -> AuditPageOut: ...

    def overview(self) -> OverviewOut: ...


@dataclass(frozen=True)
class Services:
    sessions: SessionStore
    accounts: AccountService
    audit: AuditLog
    admin: AdminService | None = None  # the operator's tools; routes answer 503 where there are none
