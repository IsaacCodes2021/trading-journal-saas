"""The operator's API with no database: who may call it, what it passes on, and what a refusal looks like (doc 07 section 6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from api_fakes import FakeAccounts, FakeAudit, FakeSessions
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import ADMIN_MFA_FOR, SESSION_COOKIE
from tradejournal.api.ports import AdminRefusal, Services
from tradejournal.api.schemas import PlanOut
from tradejournal.api.schemas.admin import AdminUserDetailOut, AdminUserListOut, AdminUserOut, UsageOut
from tradejournal.auth.csrf import csrf_token
from tradejournal.auth.iphash import hash_ip

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


class FakeAdmin:
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


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


READS = [("GET", "/api/admin/v1/users"), ("GET", f"/api/admin/v1/users/{TARGET}")]
WRITES = [
    ("POST", f"/api/admin/v1/users/{TARGET}/suspend", {"reason": "abuse"}),
    ("POST", f"/api/admin/v1/users/{TARGET}/unsuspend", {"reason": "sorted"}),
    ("POST", f"/api/admin/v1/users/{TARGET}/revoke-sessions", {"reason": "stolen laptop"}),
    ("POST", f"/api/admin/v1/users/{TARGET}/schedule-deletion", {"reason": "asked by email"}),
    ("POST", f"/api/admin/v1/users/{TARGET}/cancel-deletion", {"reason": "changed their mind"}),
    ("PUT", f"/api/admin/v1/users/{TARGET}/plan-override", {"plan": "pro", "reason": "press"}),
    ("POST", f"/api/admin/v1/users/{TARGET}/plan-override/revoke", {"reason": "over"}),
]


def call(world, method, path, headers, body=None):
    return world.client.request(method, path, headers=headers, json=body)


# ---- who may call it -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("method, path", READS)
def test_a_stranger_is_refused_with_401_on_every_read(world, method, path):
    assert call(world, method, path, {}).status_code == 401
    assert call(world, method, path, {"cookie": f"{SESSION_COOKIE}=nonsense"}).status_code == 401
    assert world.admin.calls == []


@pytest.mark.parametrize("method, path, body", WRITES)
def test_a_stranger_is_refused_with_401_on_every_write(world, method, path, body):
    assert call(world, method, path, SAME_ORIGIN, body).status_code == 401
    assert world.admin.calls == []


@pytest.mark.parametrize("method, path", READS)
def test_an_ordinary_signed_in_person_is_refused_with_403_on_every_read(world, method, path):
    _, headers = world.person(admin=False, mfa_ago=timedelta(seconds=1))  # even with two-step verification
    response = call(world, method, path, headers)
    assert response.status_code == 403 and response.json()["detail"] == "This is for operators only."
    assert world.admin.calls == []


@pytest.mark.parametrize("method, path, body", WRITES)
def test_an_ordinary_signed_in_person_is_refused_with_403_on_every_write(world, method, path, body):
    _, headers = world.person(admin=False, mfa_ago=timedelta(seconds=1))
    assert call(world, method, path, headers, body).status_code == 403
    assert world.admin.calls == []


@pytest.mark.parametrize("method, path", READS)
def test_an_operator_without_two_step_verification_is_refused_with_403_and_told_what_to_do(world, method, path):
    for mfa_ago in (None, ADMIN_MFA_FOR + timedelta(seconds=1), timedelta(hours=5)):
        _, headers = world.person(mfa_ago=mfa_ago, email=f"op{mfa_ago}@example.test")
        response = call(world, method, path, headers)
        assert response.status_code == 403
        assert response.json()["detail"] == "Confirm it is you with two-step verification first."
    assert world.admin.calls == []


def test_two_step_verification_counts_up_to_fifteen_minutes_and_not_a_second_more(world):
    _, just_inside = world.person(mfa_ago=ADMIN_MFA_FOR, email="a@example.test")
    _, just_outside = world.person(mfa_ago=ADMIN_MFA_FOR + timedelta(seconds=1), email="b@example.test")
    assert call(world, "GET", "/api/admin/v1/users", just_inside).status_code == 200
    assert call(world, "GET", "/api/admin/v1/users", just_outside).status_code == 403


def test_time_passing_makes_a_fresh_verification_stale_by_the_apps_clock(world):
    _, headers = world.person(mfa_ago=timedelta(minutes=1))
    assert call(world, "GET", "/api/admin/v1/users", headers).status_code == 200
    world.now = NOW + timedelta(minutes=30)
    assert call(world, "GET", "/api/admin/v1/users", headers).status_code == 403


@pytest.mark.parametrize("method, path, body", WRITES)
def test_a_write_needs_the_csrf_token_before_anything_else_even_from_an_operator(world, method, path, body):
    _, headers = world.person()
    without = {k: v for k, v in headers.items() if k != "x-csrf-token"}
    wrong = {**headers, "x-csrf-token": "forged"}
    cross_site = {**headers, "sec-fetch-site": "cross-site"}
    for bad in (without, wrong, cross_site):
        assert call(world, method, path, bad, body).status_code == 403
    assert world.admin.calls == []


def test_a_forged_write_is_refused_for_a_non_operator_too_so_the_token_is_never_the_only_thing_checked(world):
    _, headers = world.person(admin=False)
    forged = {k: v for k, v in headers.items() if k != "x-csrf-token"}
    assert call(world, "POST", WRITES[0][1], forged, WRITES[0][2]).status_code == 403


def test_a_server_with_no_operator_tools_answers_503_to_an_operator(tmp_path):
    bare = World(tmp_path, with_admin_service=False)
    _, headers = bare.person()
    response = bare.client.get("/api/admin/v1/users", headers=headers)
    assert response.status_code == 503 and "not set up" in response.json()["detail"]


def test_an_operator_whose_account_is_no_longer_active_is_signed_out(world):
    account, headers = world.person()
    world.accounts._accounts.pop(account.user.id)  # suspended or deleted since the session began
    assert call(world, "GET", "/api/admin/v1/users", headers).status_code == 401


# ---- what it passes on ----------------------------------------------------------------------------------------------------


def test_the_list_passes_the_search_the_filter_and_the_page(world):
    _, headers = world.person()
    response = call(world, "GET", "/api/admin/v1/users?q=sam&status=suspended&limit=20&offset=40", headers)
    assert response.status_code == 200
    assert world.admin.calls == [("list_users", "sam", "suspended", 20, 40, {})]
    body = response.json()
    assert body["total"] == 1 and body["limit"] == 20 and body["offset"] == 40
    assert body["users"][0]["usage"] == {
        "accounts": 2,
        "trades": 40,
        "screenshots": 3,
        "storage_bytes": 1234,
        "last_import_at": None,
    }


def test_the_list_defaults_to_the_first_fifty(world):
    _, headers = world.person()
    call(world, "GET", "/api/admin/v1/users", headers)
    assert world.admin.calls == [("list_users", None, None, 50, 0, {})]


@pytest.mark.parametrize(
    "query", ["limit=0", "limit=101", "limit=x", "offset=-1", "offset=1000001", "status=deleted", "q=" + "x" * 101]
)
def test_a_list_request_outside_its_bounds_is_refused_before_anything_is_read(world, query):
    _, headers = world.person()
    assert call(world, "GET", f"/api/admin/v1/users?{query}", headers).status_code == 422
    assert world.admin.calls == []


def test_a_person_is_looked_up_by_a_uuid_and_nothing_else(world):
    _, headers = world.person()
    for bad in ("not-a-uuid", "1", "' OR 1=1 --"):
        assert call(world, "GET", f"/api/admin/v1/users/{bad}", headers).status_code == 422
    assert world.admin.calls == []


def test_the_operator_and_the_target_and_the_address_hash_are_passed_to_a_look(world):
    operator, headers = world.person()
    response = call(world, "GET", f"/api/admin/v1/users/{TARGET}", headers)
    assert response.status_code == 200 and response.json()["active_sessions"] == 2
    [(name, actor, target, kwargs)] = world.admin.calls
    assert (name, actor, target) == ("user_detail", operator.user.id, TARGET)
    assert kwargs["ip_hash"] == hash_ip(SECRET, "testclient") and "testclient" not in str(kwargs)


@pytest.mark.parametrize("method, path, body", WRITES)
def test_every_change_passes_on_who_whom_and_the_reason_trimmed(world, method, path, body):
    operator, headers = world.person()
    body = {**body, "reason": "  " + body["reason"] + "  "}
    response = call(world, method, path, headers, body)
    assert response.status_code == 200, response.text
    [(name, actor, target, *rest)] = world.admin.calls
    assert actor == operator.user.id and target == TARGET
    assert body["reason"].strip() in rest or body["reason"].strip() in [a for a in rest if isinstance(a, str)]


def test_a_plan_override_passes_the_plan_and_when_it_ends(world):
    _, headers = world.person()
    ends = "2027-01-01T00:00:00Z"
    response = call(
        world,
        "PUT",
        f"/api/admin/v1/users/{TARGET}/plan-override",
        headers,
        {"plan": "free", "expires_at": ends, "reason": "x"},
    )
    assert response.status_code == 200
    [(_, _, _, plan, expires_at, reason, _)] = world.admin.calls
    assert (plan, expires_at, reason) == ("free", datetime(2027, 1, 1, tzinfo=UTC), "x")


def test_revoking_sessions_answers_how_many_were_ended(world):
    _, headers = world.person()
    response = call(world, "POST", f"/api/admin/v1/users/{TARGET}/revoke-sessions", headers, {"reason": "x"})
    assert response.json() == {"ended": 3}


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "\t\n"},
        {"reason": "x" * 501},
        {"reason": None},
        {"reason": 5},
        {"why": "abuse"},
    ],
)
def test_a_change_without_a_real_reason_is_refused_before_anything_happens(world, body):
    _, headers = world.person()
    for method, path, _ in WRITES:
        if method == "PUT":
            continue
        assert call(world, method, path, headers, body).status_code == 422
    assert world.admin.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"reason": "x"},
        {"plan": "gold", "reason": "x"},
        {"plan": "pro"},
        {"plan": "pro", "reason": " "},
        {"plan": None, "reason": "x"},
    ],
)
def test_an_override_needs_a_known_plan_and_a_reason(world, body):
    _, headers = world.person()
    assert call(world, "PUT", f"/api/admin/v1/users/{TARGET}/plan-override", headers, body).status_code == 422
    assert world.admin.calls == []


# ---- refusals ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 409, 422])
def test_a_refusal_from_the_service_is_the_problem_the_page_shows(world, status):
    _, headers = world.person()
    world.admin.refuse = AdminRefusal(status, "That account is not active, so it cannot be suspended.")
    response = call(world, "POST", WRITES[0][1], headers, WRITES[0][2])
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["detail"] == "That account is not active, so it cannot be suspended."


def test_nothing_about_the_operator_tools_is_cached(world):
    _, headers = world.person()
    response = call(world, "GET", "/api/admin/v1/users", headers)
    assert response.headers["cache-control"] == "no-store"
