"""What the API needs from the outside world, as interfaces.

Sessions live in Postgres and sign-in goes through the identity provider (M2 and M5, after spikes S1b and S2). The
routers depend only on these two protocols, so they can be built and tested now, and the real implementations slot in
later without touching a route. Tests use the in-memory versions in `tests/api_fakes.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .schemas import PlanOut, SettingsOut, SettingsPatch, UserOut

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


@dataclass(frozen=True)
class Services:
    sessions: SessionStore
    accounts: AccountService
    audit: AuditLog
