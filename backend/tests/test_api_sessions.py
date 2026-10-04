"""`/api/v1/sessions`: see where you are signed in, sign one place out, or sign out everywhere else (no database)."""

from __future__ import annotations

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
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"


@pytest.fixture
def world(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    client = TestClient(create_app(config, services), base_url=ORIGIN)
    return client, sessions, accounts


def cookie(token):
    return {"cookie": f"{SESSION_COOKIE}={token}"}


def writes(token, session):
    return {**cookie(token), **SAME_ORIGIN, "x-csrf-token": csrf_token(SECRET, session.id)}


def two_places(world):
    """Alice signed in on a laptop (the one making the requests) and on a phone."""
    client, sessions, accounts = world
    alice = accounts.add("alice@example.test")
    laptop_token, laptop = sessions.sign_in(alice.user.id, user_agent=CHROME)
    phone_token, phone = sessions.sign_in(alice.user.id, user_agent=PHONE)
    return client, sessions, accounts, alice, (laptop_token, laptop), (phone_token, phone)


def test_it_lists_where_you_are_signed_in_marking_this_one_and_naming_devices(world):
    client, _, _, _, (token, laptop), (_, phone) = two_places(world)
    body = client.get("/api/v1/sessions", headers=cookie(token)).json()
    assert body["ended"] == 0
    rows = {row["id"]: row for row in body["sessions"]}
    assert set(rows) == {laptop.id, phone.id}
    assert rows[laptop.id]["current"] is True and rows[phone.id]["current"] is False
    assert rows[laptop.id]["device"] == "Chrome on Windows" and rows[phone.id]["device"] == "Safari on iPhone"
    assert [row["id"] for row in body["sessions"]] == [phone.id, laptop.id]  # most recently used first
    assert set(rows[laptop.id]) == {"id", "current", "device", "signed_in_at", "last_active_at", "expires_at", "mfa"}


def test_it_never_returns_the_raw_user_agent_a_token_or_an_address(world):
    client, _, _, _, (token, _), (phone_token, _) = two_places(world)
    text = client.get("/api/v1/sessions", headers=cookie(token)).text
    for secret in (CHROME, PHONE, token, phone_token, "Mozilla"):
        assert secret not in text


def test_it_shows_only_your_own_sessions(world):
    client, sessions, accounts, _, (token, laptop), _ = two_places(world)
    bob = accounts.add("bob@example.test")
    _, bobs = sessions.sign_in(bob.user.id, user_agent=CHROME)
    ids = {row["id"] for row in client.get("/api/v1/sessions", headers=cookie(token)).json()["sessions"]}
    assert bobs.id not in ids and laptop.id in ids


def test_it_signs_one_other_place_out_and_shows_what_is_left(world):
    client, sessions, _, _, (token, laptop), (phone_token, phone) = two_places(world)
    response = client.delete(f"/api/v1/sessions/{phone.id}", headers=writes(token, laptop))
    assert response.status_code == 200
    body = response.json()
    assert body["ended"] == 1 and [row["id"] for row in body["sessions"]] == [laptop.id]
    assert sessions.lookup(phone_token) is None  # the phone is signed out
    assert sessions.lookup(token) is not None  # and this one is not


def test_it_records_a_keyed_hash_of_the_address_for_the_audit_log(world):
    client, sessions, _, _, (token, laptop), (_, phone) = two_places(world)
    client.delete(f"/api/v1/sessions/{phone.id}", headers=writes(token, laptop))
    [(kind, ip_hash)] = sessions.ended_by
    assert kind == "one" and ip_hash == hash_ip(SECRET, "testclient")


def test_it_refuses_to_end_the_session_you_are_using_and_says_what_to_do(world):
    client, sessions, _, _, (token, laptop), _ = two_places(world)
    response = client.delete(f"/api/v1/sessions/{laptop.id}", headers=writes(token, laptop))
    assert response.status_code == 422
    assert response.json()["detail"] == "That is the session you are using. Use Sign out to end it."
    assert sessions.lookup(token) is not None


def test_someone_elses_an_ended_and_an_unknown_session_all_answer_the_same(world):
    client, sessions, accounts, _, (token, laptop), (_, phone) = two_places(world)
    bob = accounts.add("bob@example.test")
    _, bobs = sessions.sign_in(bob.user.id)
    client.delete(f"/api/v1/sessions/{phone.id}", headers=writes(token, laptop))  # now ended
    answers = []
    for target in (bobs.id, phone.id, "00000000-0000-4000-8000-000000000000"):
        response = client.delete(f"/api/v1/sessions/{target}", headers=writes(token, laptop))
        answers.append((response.status_code, response.json()["detail"]))
    assert answers == [(404, "That session does not exist.")] * 3
    assert sessions.lookup(next(t for t, s in sessions._by_token.items() if s.id == bobs.id)) is not None  # untouched


def test_a_malformed_session_id_is_refused_before_anything_happens(world):
    client, sessions, _, _, (token, laptop), (phone_token, _) = two_places(world)
    for junk in ("not-a-uuid", "1", "%00", "x" * 500):
        assert client.delete(f"/api/v1/sessions/{junk}", headers=writes(token, laptop)).status_code == 422
    assert sessions.lookup(phone_token) is not None and sessions.ended_by == []


def test_it_signs_out_everywhere_else_and_keeps_this_one(world):
    client, sessions, accounts, alice, (token, laptop), (phone_token, _) = two_places(world)
    tablet_token, _ = sessions.sign_in(accounts.add("x@example.test").user.id)  # someone else's: must survive
    again_token, _ = sessions.sign_in(alice.user.id, user_agent=CHROME)
    response = client.delete("/api/v1/sessions", headers=writes(token, laptop))
    body = response.json()
    assert response.status_code == 200 and body["ended"] == 2
    assert [row["id"] for row in body["sessions"]] == [laptop.id] and body["sessions"][0]["current"] is True
    assert sessions.lookup(phone_token) is None and sessions.lookup(again_token) is None
    assert sessions.lookup(token) is not None and sessions.lookup(tablet_token) is not None


def test_signing_out_everywhere_else_with_nothing_else_says_zero(world):
    client, sessions, accounts = world
    alice = accounts.add("solo@example.test")
    token, session = sessions.sign_in(alice.user.id)
    response = client.delete("/api/v1/sessions", headers=writes(token, session))
    assert response.status_code == 200 and response.json()["ended"] == 0 and len(response.json()["sessions"]) == 1


def test_every_change_needs_a_session_and_the_csrf_token(world):
    client, _, _, _, (token, laptop), (_, phone) = two_places(world)
    for method, path in (
        ("GET", "/api/v1/sessions"),
        ("DELETE", "/api/v1/sessions"),
        ("DELETE", f"/api/v1/sessions/{phone.id}"),
    ):
        assert client.request(method, path).status_code == 401
    for path in ("/api/v1/sessions", f"/api/v1/sessions/{phone.id}"):
        assert client.delete(path, headers=cookie(token)).status_code == 403  # signed in, no token
        assert client.delete(path, headers={**writes(token, laptop), "x-csrf-token": "wrong"}).status_code == 403
        assert client.delete(path, headers={**writes(token, laptop), "sec-fetch-site": "cross-site"}).status_code == 403
    assert len(client.get("/api/v1/sessions", headers=cookie(token)).json()["sessions"]) == 2  # nothing was ended
