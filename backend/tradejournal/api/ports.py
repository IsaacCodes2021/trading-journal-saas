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


class AccountService(Protocol):
    def load(self, user_id: UUID) -> Account | None:
        """The signed-in user's account, or None if it no longer exists."""
        ...

    def update_settings(self, user_id: UUID, changes: SettingsPatch) -> SettingsOut:
        """Apply the fields that are set (not None) and return all settings."""
        ...


@dataclass(frozen=True)
class Services:
    sessions: SessionStore
    accounts: AccountService
