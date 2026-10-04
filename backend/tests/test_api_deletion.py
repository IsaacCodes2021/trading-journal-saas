"""`POST /api/v1/account/deletion`: who may ask, what must be true first, and what happens (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.auth.csrf import csrf_token
from tradejournal.auth.iphash import hash_ip

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
SAME = {"sec-fetch-site": "same-origin"}
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def world(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    app = create_app(config, services, clock=lambda: NOW)
    return TestClient(app, base_url=ORIGIN), sessions, accounts


def person(world, *, signed_in_minutes_ago=1.0, email="ann@example.test"):
    client, sessions, accounts = world
    account = accounts.add(email)
    token, session = sessions.sign_in(account.user.id, authenticated_at=NOW - timedelta(minutes=signed_in_minutes_ago))
    headers = {"cookie": f"{SESSION_COOKIE}={token}", **SAME, "x-csrf-token": csrf_token(SECRET, session.id)}
    return client, account, session, headers, token


def test_a_person_who_just_signed_in_can_schedule_the_deletion_by_typing_their_own_email(world):
    client, account, _, headers, token = person(world)
    _, sessions, accounts = world
    response = client.post("/api/v1/account/deletion", json={"email": "ann@example.test"}, headers=headers)
    assert response.status_code == 200 and response.json() == {"scheduled_for": "2026-10-11T12:00:00Z"}
    assert accounts.deletions == [(account.user.id, hash_ip(SECRET, "testclient"))]
    assert sessions.lookup(token) is None  # every session ended, this one too
    cleared = response.headers["set-cookie"].lower()
    assert (
        f"{SESSION_COOKIE.lower()}=" in cleared
        and "max-age=0" in cleared
        and "secure" in cleared
        and "httponly" in cleared
    )


def test_the_email_is_compared_without_regard_to_case_or_stray_spaces(world):
    client, account, _, headers, _ = person(world, email="Ann.Person@Example.test")
    assert (
        client.post(
            "/api/v1/account/deletion", json={"email": "  ann.person@EXAMPLE.test "}, headers=headers
        ).status_code
        == 200
    )


@pytest.mark.parametrize("typed", ["", "ann@example.tes", "bob@example.test", "ann@example.test.evil.test", "x" * 400])
def test_any_other_email_is_refused_and_nothing_is_scheduled(world, typed):
    client, account, _, headers, token = person(world)
    _, sessions, accounts = world
    response = client.post("/api/v1/account/deletion", json={"email": typed}, headers=headers)
    assert response.status_code == 422
    assert accounts.deletions == [] and sessions.lookup(token) is not None


def test_a_sign_in_older_than_ten_minutes_is_not_enough_and_says_what_to_do(world):
    client, _, _, headers, token = person(world, signed_in_minutes_ago=10.5)
    _, sessions, accounts = world
    response = client.post("/api/v1/account/deletion", json={"email": "ann@example.test"}, headers=headers)
    assert response.status_code == 403 and response.json()["detail"] == "Sign in again first, so we know it is you."
    assert accounts.deletions == [] and sessions.lookup(token) is not None


def test_the_ten_minute_edge_is_exactly_ten_minutes(world):
    inside = person(world, signed_in_minutes_ago=9.99, email="in@example.test")
    outside = person(world, signed_in_minutes_ago=10.01, email="out@example.test")
    assert (
        inside[0].post("/api/v1/account/deletion", json={"email": "in@example.test"}, headers=inside[3]).status_code
        == 200
    )
    assert (
        outside[0].post("/api/v1/account/deletion", json={"email": "out@example.test"}, headers=outside[3]).status_code
        == 403
    )


def test_an_account_that_cannot_be_scheduled_is_a_conflict_and_keeps_its_sessions(world):
    client, account, _, headers, token = person(world)
    _, sessions, accounts = world
    accounts.refuse_deletion.add(account.user.id)  # already scheduled, or suspended
    response = client.post("/api/v1/account/deletion", json={"email": "ann@example.test"}, headers=headers)
    assert response.status_code == 409 and sessions.lookup(token) is not None


def test_it_is_a_change_so_it_needs_a_session_and_the_csrf_token_and_a_body(world):
    client, account, session, headers, token = person(world)
    body = {"email": "ann@example.test"}
    assert client.post("/api/v1/account/deletion", json=body).status_code == 401
    cookie = {"cookie": headers["cookie"]}
    assert client.post("/api/v1/account/deletion", json=body, headers=cookie).status_code == 403  # no token
    assert (
        client.post("/api/v1/account/deletion", json=body, headers={**headers, "x-csrf-token": "x"}).status_code == 403
    )
    assert (
        client.post(
            "/api/v1/account/deletion", json=body, headers={**headers, "sec-fetch-site": "cross-site"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/account/deletion", json={}, headers=headers).status_code == 422
    assert world[2].deletions == []


def test_the_session_says_whether_the_person_signed_in_recently(world):
    for minutes, expected in ((0.1, True), (9.9, True), (10.1, False), (600, False)):
        client, _, _, headers, _ = person(world, signed_in_minutes_ago=minutes, email=f"u{minutes}@example.test")
        flags = client.get("/api/v1/session", headers={"cookie": headers["cookie"]}).json()["flags"]
        assert flags["recently_authenticated"] is expected, minutes
