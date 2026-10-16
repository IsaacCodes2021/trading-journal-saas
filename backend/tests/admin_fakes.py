"""Fakes for the operator API tests: a service that records what it is asked, and a server with signed-in people."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from api_fakes import FakeAccounts, FakeAudit, FakeSessions
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.ports import AdminRefusal, Services
from tradejournal.api.schemas import PlanOut
from tradejournal.api.schemas.admin import (
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
    UsageOut,
)
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
TARGET = UUID("11111111-2222-3333-4444-555555555555")


def user(status="active", plan="free", id=TARGET) -> AdminUserOut:
    return AdminUserOut(
        id=id,
        email="sam@example.test",
        status=status,
        is_admin=False,
        created_at=NOW,
        last_login_at=None,
        deletion_scheduled_at=None,
        plan=PlanOut(effective=plan, source="override" if plan == "pro" else "free", expires_at=None),
        usage=UsageOut(accounts=2, trades=40, screenshots=3, storage_bytes=1234, last_import_at=None),
    )


def invite(id=7, status="works") -> InviteOut:
    return InviteOut(
        id=id,
        label="Sam",
        email=None,
        max_uses=1,
        used=0,
        expires_at=None,
        revoked_at=None,
        created_at=NOW,
        grant_code_id=None,
        grant_label=None,
        status=status,
    )


def grant(id=3, status="works") -> GrantCodeOut:
    return GrantCodeOut(
        id=id,
        code=None,
        kind="free_days",
        label="Beta testers",
        plan="pro",
        days=90,
        max_redemptions=None,
        used=0,
        expires_at=None,
        created_by="admin:x",
        created_at=NOW,
        revoked_at=None,
        status=status,
    )


class FakeAdminCodes:
    """The invite, plan code, audit and overview half of the operator service."""

    def list_invites(self, *, limit, offset):
        self._do("list_invites", limit, offset)
        return InviteListOut(invites=[invite()], total=1, limit=limit, offset=offset)

    def create_invite(self, actor, *, label, email, max_uses, days, grant_code_id, ip_hash=None):
        self._do("create_invite", actor, label, email, max_uses, days, grant_code_id, ip_hash=ip_hash)
        return CreatedInviteOut(invite=invite(), code="TJ-ABCD-EFGH")

    def revoke_invite(self, actor, invite_id, reason, *, ip_hash=None):
        self._do("revoke_invite", actor, invite_id, reason, ip_hash=ip_hash)
        return invite(invite_id, "revoked")

    def list_grant_codes(self, *, limit, offset, show=None):
        self._do("list_grant_codes", limit, offset, show)
        return GrantCodeListOut(grant_codes=[grant()], total=1, limit=limit, offset=offset)

    def create_grant_code(
        self, actor, *, label, plan, days, max_redemptions, expires_in_days, code=None, expires_on=None, ip_hash=None
    ):
        self._do(
            "create_grant_code", actor, label, plan, days, max_redemptions, expires_in_days, code, expires_on,
            ip_hash=ip_hash,
        )  # fmt: skip
        return CreatedGrantCodeOut(grant_code=grant(), code="GR-ABCD-EFGH")

    def revoke_grant_code(self, actor, grant_id, reason, *, ip_hash=None):
        self._do("revoke_grant_code", actor, grant_id, reason, ip_hash=ip_hash)
        return grant(grant_id, "revoked")

    def audit(self, *, action, q, actor, days, limit, before):
        self._do("audit", action, q, actor, days, limit, before)
        return AuditPageOut(events=[], next_before=None)

    def overview(self):
        self._do("overview")
        return OverviewOut(
            accounts=64,
            signups_7d=18,
            active_7d=41,
            active=60,
            suspended=2,
            pending_deletion=2,
            pro=7,
            free=57,
            open_invites=3,
            open_grant_codes=1,
            grants_ending_7d=0,
        )


class FakeAdmin(FakeAdminCodes):
    """Records what the routes ask for, and answers (or refuses) as told."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.refuse: AdminRefusal | None = None

    def _do(self, name, *args, **kwargs):
        self.calls.append((name, *args, kwargs))
        if self.refuse is not None:
            raise self.refuse

    def list_users(self, *, q, status, limit, offset):
        self._do("list_users", q, status, limit, offset)
        return AdminUserListOut(users=[user()], total=1, limit=limit, offset=offset)

    def user_detail(self, actor, target, *, ip_hash=None):
        self._do("user_detail", actor, target, ip_hash=ip_hash)
        return AdminUserDetailOut(user=user(), active_sessions=2, overrides=[], redemptions=[], events=[])

    def suspend(self, actor, target, reason, *, ip_hash=None):
        self._do("suspend", actor, target, reason, ip_hash=ip_hash)
        return user("suspended")

    def unsuspend(self, actor, target, reason, *, ip_hash=None):
        self._do("unsuspend", actor, target, reason, ip_hash=ip_hash)
        return user()

    def revoke_sessions(self, actor, target, reason, *, ip_hash=None):
        self._do("revoke_sessions", actor, target, reason, ip_hash=ip_hash)
        return 3

    def schedule_deletion(self, actor, target, reason, *, ip_hash=None):
        self._do("schedule_deletion", actor, target, reason, ip_hash=ip_hash)
        return user("pending_deletion")

    def cancel_deletion(self, actor, target, reason, *, ip_hash=None):
        self._do("cancel_deletion", actor, target, reason, ip_hash=ip_hash)
        return user()

    def set_override(self, actor, target, plan, expires_at, reason, *, ip_hash=None):
        self._do("set_override", actor, target, plan, expires_at, reason, ip_hash=ip_hash)
        return user(plan=plan)

    def revoke_override(self, actor, target, reason, *, ip_hash=None):
        self._do("revoke_override", actor, target, reason, ip_hash=ip_hash)
        return user()


class World:
    def __init__(self, tmp_path, *, with_admin_service=True):
        self.sessions, self.accounts, self.admin = FakeSessions(), FakeAccounts(), FakeAdmin()
        self.now = NOW
        services = Services(
            sessions=self.sessions,
            accounts=self.accounts,
            audit=FakeAudit(),
            admin=self.admin if with_admin_service else None,
        )
        config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
        self.client = TestClient(create_app(config, services, clock=lambda: self.now), base_url=ORIGIN)

    def person(self, *, admin=True, mfa_ago: timedelta | None = timedelta(minutes=1), email="op@example.test"):
        """Someone signed in: an operator or not, with two-step verification `mfa_ago` ago (or never)."""
        account = self.accounts.add(email, is_admin=admin)
        mfa_at = None if mfa_ago is None else NOW - mfa_ago
        token, session = self.sessions.sign_in(account.user.id, mfa_at=mfa_at)
        headers = {"cookie": f"{SESSION_COOKIE}={token}", **SAME_ORIGIN, "x-csrf-token": csrf_token(SECRET, session.id)}
        return account, headers
