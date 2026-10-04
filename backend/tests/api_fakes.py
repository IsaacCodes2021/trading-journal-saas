"""In-memory stand-ins for the session store and the account service (the real ones arrive with Postgres, M2/M5)."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from tradejournal.api.ports import Account, AuthSession, Services
from tradejournal.api.schemas import PlanOut, SettingsOut, SettingsPatch, UserOut


class FakeSessions:
    def __init__(self) -> None:
        self._by_token: dict[str, AuthSession] = {}

    def sign_in(self, user_id: UUID, *, mfa_at: datetime | None = None) -> tuple[str, AuthSession]:
        token = secrets.token_urlsafe(24)
        session = AuthSession(id=secrets.token_hex(8), user_id=user_id, mfa_at=mfa_at)
        self._by_token[token] = session
        return token, session

    def revoke(self, token: str) -> None:
        self._by_token.pop(token, None)

    def lookup(self, token: str | None) -> AuthSession | None:
        return self._by_token.get(token or "")


class FakeAccounts:
    def __init__(self) -> None:
        self._accounts: dict[UUID, Account] = {}

    def add(self, email: str, *, is_admin: bool = False) -> Account:
        account = Account(
            user=UserOut(id=uuid4(), email=email),
            settings=SettingsOut(
                timezone="America/Los_Angeles",
                csv_timezone=None,
                trading_day_timezone="America/New_York",
                trading_day_start="18:00",
                theme="dark",
            ),
            plan=PlanOut(effective="free", source="free", expires_at=datetime(2030, 1, 1, tzinfo=UTC)),
            entitlements={"max_accounts": 3, "can_export": True},
            is_admin=is_admin,
        )
        self._accounts[account.user.id] = account
        return account

    def load(self, user_id: UUID) -> Account | None:
        return self._accounts.get(user_id)

    def update_settings(self, user_id: UUID, changes: SettingsPatch) -> SettingsOut:
        account = self._accounts[user_id]
        update = changes.model_dump(exclude_none=True)
        if update.get("csv_timezone") == "":
            update["csv_timezone"] = None  # "" goes back to following the person's own timezone
        settings = account.settings.model_copy(update=update)
        self._accounts[user_id] = replace(account, settings=settings)
        return settings


def make_services() -> tuple[Services, FakeSessions, FakeAccounts]:
    sessions, accounts = FakeSessions(), FakeAccounts()
    return Services(sessions=sessions, accounts=accounts), sessions, accounts
