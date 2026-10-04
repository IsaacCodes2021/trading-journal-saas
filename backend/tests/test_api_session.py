from datetime import UTC, datetime, timedelta

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


@pytest.fixture
def world(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", app_version="build-7", session_secret=SECRET)
    client = TestClient(create_app(config, services), base_url=ORIGIN)
    alice = accounts.add("alice@example.test")
    token, session = sessions.sign_in(alice.user.id)
    return client, sessions, accounts, alice, token, session


def cookie(token):
    return {"cookie": f"{SESSION_COOKIE}={token}"}


def write_headers(token, session, **extra):
    return {**cookie(token), **SAME_ORIGIN, "x-csrf-token": csrf_token(SECRET, session.id), **extra}


# ---- GET /api/v1/session -----------------------------------------------------------------------------------------


def test_session_needs_a_cookie(world):
    client = world[0]
    for headers in ({}, cookie("not-a-real-token")):
        response = client.get("/api/v1/session", headers=headers)
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"


def test_session_describes_the_signed_in_user(world):
    client, _, _, alice, token, session = world
    response = client.get("/api/v1/session", headers=cookie(token))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["user"] == {"id": str(alice.user.id), "email": "alice@example.test"}
    assert body["settings"] == {
        "timezone": "America/Los_Angeles",
        "csv_timezone": None,
        "trading_day_timezone": "America/New_York",
        "trading_day_start": "18:00",
        "theme": "dark",
    }
    assert body["plan"] == {"effective": "free", "source": "free", "expires_at": "2030-01-01T00:00:00Z"}
    assert body["entitlements"] == {"max_accounts": 3, "can_export": True}
    assert body["flags"] == {"is_admin": False, "mfa_fresh": False, "recently_authenticated": True}  # just signed in
    assert body["app_version"] == "build-7"
    assert body["csrf_token"] == csrf_token(SECRET, session.id)


def test_the_csrf_token_is_stable_within_a_session_and_differs_between_sessions(world):
    client, sessions, _, alice, token, _ = world
    other_token, _ = sessions.sign_in(alice.user.id)
    first = client.get("/api/v1/session", headers=cookie(token)).json()["csrf_token"]
    again = client.get("/api/v1/session", headers=cookie(token)).json()["csrf_token"]
    other = client.get("/api/v1/session", headers=cookie(other_token)).json()["csrf_token"]
    assert first == again != other


@pytest.mark.parametrize(
    ("age", "fresh"),
    [(timedelta(minutes=5), True), (timedelta(minutes=14), True), (timedelta(minutes=16), False), (None, False)],
)
def test_mfa_is_fresh_for_fifteen_minutes(world, age, fresh):
    client, sessions, accounts, alice, *_ = world
    token, _ = sessions.sign_in(alice.user.id, mfa_at=None if age is None else datetime.now(UTC) - age)
    flags = client.get("/api/v1/session", headers=cookie(token)).json()["flags"]
    assert flags["mfa_fresh"] is fresh


def test_admin_flag_comes_from_the_account(world):
    client, sessions, accounts, *_ = world
    boss = accounts.add("boss@example.test", is_admin=True)
    token, _ = sessions.sign_in(boss.user.id)
    assert client.get("/api/v1/session", headers=cookie(token)).json()["flags"]["is_admin"] is True


def test_a_session_whose_account_is_gone_is_signed_out(world):
    client, _, accounts, alice, token, _ = world
    accounts._accounts.pop(alice.user.id)
    assert client.get("/api/v1/session", headers=cookie(token)).status_code == 401


def test_a_revoked_session_is_signed_out(world):
    client, sessions, _, _, token, _ = world
    sessions.revoke(token)
    assert client.get("/api/v1/session", headers=cookie(token)).status_code == 401


def test_without_services_the_session_routes_say_the_server_is_not_ready(tmp_path):
    client = TestClient(create_app(ApiConfig(web_dist=tmp_path / "none")))
    response = client.get("/api/v1/session", headers=cookie("anything"))
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"


# ---- PATCH /api/v1/settings --------------------------------------------------------------------------------------


def patch(world, body, **headers):
    client, _, _, _, token, session = world
    return client.patch("/api/v1/settings", json=body, headers=headers or write_headers(token, session))


def test_settings_can_be_changed_and_the_change_sticks(world):
    response = patch(world, {"theme": "light", "trading_day_start": "17:00"})
    assert response.status_code == 200
    assert response.json() == {
        "timezone": "America/Los_Angeles",
        "csv_timezone": None,
        "trading_day_timezone": "America/New_York",
        "trading_day_start": "17:00",
        "theme": "light",
    }
    client, _, _, _, token, _ = world
    assert client.get("/api/v1/session", headers=cookie(token)).json()["settings"]["theme"] == "light"


def test_null_and_empty_patches_change_nothing(world):
    unchanged = patch(world, {}).json()
    assert patch(world, {"theme": None, "timezone": None}).json() == unchanged
    assert unchanged["theme"] == "dark"


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"timezone": "../etc/passwd"}, "timezone"),
        ({"timezone": ""}, "timezone"),
        ({"csv_timezone": "Mars/Olympus"}, "csv_timezone"),
        ({"csv_timezone": "../etc/passwd"}, "csv_timezone"),
        ({"trading_day_timezone": "nope"}, "trading_day_timezone"),
        ({"trading_day_start": "25:00"}, "trading_day_start"),
        ({"trading_day_start": "6:30"}, "trading_day_start"),
        ({"trading_day_start": "18:60"}, "trading_day_start"),
        ({"theme": "blue"}, "theme"),
        ({"tz": "UTC"}, "tz"),  # a misspelled field is refused, not ignored
    ],
)
def test_bad_settings_are_a_422_that_names_the_field(world, body, field):
    response = patch(world, body)
    assert response.status_code == 422
    assert [error["field"] for error in response.json()["errors"]] == [field]


def test_writes_need_a_signed_in_user_before_anything_else(world):
    client = world[0]
    response = client.patch("/api/v1/settings", json={"theme": "light"}, headers=SAME_ORIGIN)
    assert response.status_code == 401  # not 403: an anonymous caller learns nothing about the CSRF rules


def test_writes_need_the_csrf_token(world):
    _, _, _, _, token, session = world
    headers = write_headers(token, session)
    for broken in ({"x-csrf-token": ""}, {"x-csrf-token": "wrong"}, {"sec-fetch-site": "cross-site"}):
        assert patch(world, {"theme": "light"}, **{**headers, **broken}).status_code == 403


def test_a_token_from_another_session_does_not_work(world):
    client, sessions, _, alice, token, _ = world
    _, other_session = sessions.sign_in(alice.user.id)
    headers = {**cookie(token), **SAME_ORIGIN, "x-csrf-token": csrf_token(SECRET, other_session.id)}
    assert patch(world, {"theme": "light"}, **headers).status_code == 403


def test_a_form_post_style_body_is_refused(world):
    _, _, _, _, token, session = world
    headers = write_headers(token, session, **{"content-type": "text/plain"})
    assert patch(world, {"theme": "light"}, **headers).status_code == 415
    assert world[0].get("/api/v1/session", headers=cookie(token)).json()["settings"]["theme"] == "dark"


def test_the_csv_timezone_can_be_chosen_and_cleared(world):
    assert patch(world, {"csv_timezone": "America/Chicago"}).json()["csv_timezone"] == "America/Chicago"
    assert patch(world, {"theme": "light"}).json()["csv_timezone"] == "America/Chicago"  # other changes leave it alone
    assert patch(world, {"csv_timezone": None}).json()["csv_timezone"] == "America/Chicago"  # null leaves it alone too
    assert patch(world, {"csv_timezone": ""}).json()["csv_timezone"] is None  # "" goes back to following the timezone
