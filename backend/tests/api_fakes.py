"""In-memory stand-ins for the session store and the account service (the real ones arrive with Postgres, M2/M5)."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from tradejournal.api.ports import Account, ActivityEvent, AuthSession, GrantResult, Services, SessionInfo
from tradejournal.api.schemas import PlanOut, SettingsOut, SettingsPatch, UserOut


class FakeSessions:
    def __init__(self) -> None:
        self._by_token: dict[str, AuthSession] = {}
        self._info: dict[str, SessionInfo] = {}  # by session id
        self.ended_by: list[tuple[str, str | None]] = []  # ("one" or "others", the ip hash given), for assertions

    def sign_in(
        self,
        user_id: UUID,
        *,
        mfa_at: datetime | None = None,
        user_agent: str | None = None,
        authenticated_at: datetime | None = None,
    ) -> tuple[str, AuthSession]:
        token = secrets.token_urlsafe(24)
        session = AuthSession(
            id=str(uuid4()),
            user_id=user_id,
            mfa_at=mfa_at,
            authenticated_at=authenticated_at or datetime.now(UTC),
        )
        self._by_token[token] = session
        now = datetime.now(UTC)
        self._info[session.id] = SessionInfo(
            id=session.id,
            created_at=now,
            last_seen_at=now + timedelta(seconds=len(self._info)),  # later sign-ins are more recent
            expires_at=now + timedelta(days=90),
            mfa_at=mfa_at,
            user_agent=user_agent,
        )
        return token, session

    def revoke(self, token: str) -> None:
        session = self._by_token.pop(token, None)
        if session:
            self._info.pop(session.id, None)

    def lookup(self, token: str | None) -> AuthSession | None:
        return self._by_token.get(token or "")

    def list_for(self, user_id: UUID) -> list[SessionInfo]:
        mine = [info for sid, info in self._info.items() if self._owner(sid) == user_id]
        return sorted(mine, key=lambda info: info.last_seen_at, reverse=True)

    def revoke_one(self, user_id: UUID, session_id: str, *, ip_hash: str | None = None) -> bool:
        if self._owner(session_id) != user_id:
            return False
        self._drop(session_id)
        self.ended_by.append(("one", ip_hash))
        return True

    def revoke_others(self, user_id: UUID, keep_id: str, *, ip_hash: str | None = None) -> int:
        doomed = [sid for sid in self._info if self._owner(sid) == user_id and sid != keep_id]
        for sid in doomed:
            self._drop(sid)
        self.ended_by.append(("others", ip_hash))
        return len(doomed)

    def revoke_all(self, user_id: UUID) -> int:
        doomed = [sid for sid in self._info if self._owner(sid) == user_id]
        for sid in doomed:
            self._drop(sid)
        self.ended_by.append(("all", None))
        return len(doomed)

    def _owner(self, session_id: str) -> UUID | None:
        return next((s.user_id for s in self._by_token.values() if s.id == session_id), None)

    def _drop(self, session_id: str) -> None:
        self._info.pop(session_id, None)
        for token in [t for t, s in self._by_token.items() if s.id == session_id]:
            del self._by_token[token]


class FakeAccounts:
    def __init__(self) -> None:
        self._accounts: dict[UUID, Account] = {}
        self.deletions: list[tuple[UUID, str | None]] = []  # what was scheduled, for assertions
        self.refuse_deletion: set[UUID] = set()  # users whose account is not active
        self.codes: dict[str, GrantResult] = {}  # what redeeming each plan code answers; any other code is refused
        self.redeemed: list[tuple[UUID, str, str | None]] = []  # every attempt: (person, code as sent, address hash)

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

    def redeem_grant(self, user_id: UUID, code: str, *, ip_hash: str | None = None) -> GrantResult:
        self.redeemed.append((user_id, code, ip_hash))
        result = self.codes.get(code, GrantResult("refused"))
        if result.status == "ok" and result.plan is not None:  # the person is on that plan from now on
            self._accounts[user_id] = replace(self._accounts[user_id], plan=result.plan)
        return result

    def schedule_deletion(self, user_id: UUID, *, ip_hash: str | None = None) -> datetime | None:
        self.deletions.append((user_id, ip_hash))
        return None if user_id in self.refuse_deletion else datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def update_settings(self, user_id: UUID, changes: SettingsPatch) -> SettingsOut:
        account = self._accounts[user_id]
        update = changes.model_dump(exclude_none=True)
        if update.get("csv_timezone") == "":
            update["csv_timezone"] = None  # "" goes back to following the person's own timezone
        settings = account.settings.model_copy(update=update)
        self._accounts[user_id] = replace(account, settings=settings)
        return settings


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.activity: dict[UUID, list[ActivityEvent]] = {}  # what each person may see, newest first
        self.asked: list[tuple[UUID, int]] = []  # (person, limit) of every `recent` call

    def record(self, user_id: UUID, action: str, *, ip_hash: str | None = None, meta: dict | None = None) -> None:
        self.events.append({"user_id": user_id, "action": action, "ip_hash": ip_hash, "meta": meta or {}})

    def recent(self, user_id: UUID, limit: int = 30) -> list[ActivityEvent]:
        self.asked.append((user_id, limit))
        return self.activity.get(user_id, [])[:limit]


def make_services() -> tuple[Services, FakeSessions, FakeAccounts]:
    sessions, accounts = FakeSessions(), FakeAccounts()
    return Services(sessions=sessions, accounts=accounts, audit=FakeAudit()), sessions, accounts
