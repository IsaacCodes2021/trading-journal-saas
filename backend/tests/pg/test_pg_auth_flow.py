"""Signing in and out end to end: the real app, the real session store and a real Postgres, with a pretend provider.

Each test plays the browser: it asks `/auth/login`, is sent to the (fake) provider, "authenticates" there, and comes back to
`/auth/callback` with a code and the `state` it was given. Then it breaks one thing at a time, because every way the callback
can be wrong has to end in "not signed in" with nothing created (doc 04 authentication gates).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fake_idp import FakeIdp, person
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.routers.auth import LOGIN_COOKIE
from tradejournal.pg import invites

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
ALICE = person(subject="idp|alice@example.test", email="alice@example.test")  # the user the `alice` fixture made
NEWCOMER = person(subject="kp_newcomer", email="newcomer@example.test")


class Browser:
    def __init__(self, app, idp: FakeIdp) -> None:
        self.client = TestClient(app, base_url=ORIGIN, follow_redirects=False)
        self.idp = idp

    def start(self, **params):
        response = self.client.get("/auth/login", params=params)
        assert response.status_code == 302, response.text
        url = response.headers["location"]
        return url, parse_qs(urlsplit(url).query)["state"][0]

    def come_back(self, **params):
        return self.client.get("/auth/callback", params=params)

    def sign_in(self, identity, *, reported_nonce=None, **login_params):
        """The whole round trip: returns the callback's response."""
        url, state = self.start(**login_params)
        code = self.idp.sign_in(url, identity, reported_nonce=reported_nonce)
        return self.come_back(code=code, state=state)

    def another(self, user_agent: str) -> Browser:
        """A second device on the same server (same app, same identity provider), with its own cookies."""
        other = Browser(self.client.app, self.idp)
        other.client.headers["user-agent"] = user_agent
        return other

    @property
    def token(self) -> str | None:
        return self.client.cookies.get(SESSION_COOKIE)

    def session(self):
        return self.client.get("/api/v1/session")


@pytest.fixture
def world(db, app_url, login_url, owner, alice):
    def build(**config_changes):
        idp = FakeIdp()
        config = ApiConfig(
            app_origin=ORIGIN,
            web_dist=Path("/nonexistent"),
            session_secret=SECRET,
            database_url=app_url,
            login_database_url=login_url,
            **config_changes,
        )
        app = create_app(config, database=db, idp=idp)
        return Browser(app, idp)

    started = []

    def make(**config_changes) -> Browser:
        browser = build(**config_changes)
        browser.client.__enter__()  # runs the app's startup, which opens the sign-in database pool
        started.append(browser)
        return browser

    yield make
    for browser in started:
        browser.client.__exit__(None, None, None)


def audit_rows(owner):
    return owner.execute("SELECT action, actor, user_id, meta, ip_hash FROM audit_log ORDER BY id").fetchall()


def users_with_subject(owner, subject):
    return owner.execute("SELECT id, email, is_admin FROM users WHERE idp_subject = %s", (subject,)).fetchall()


# ---- the way in -------------------------------------------------------------------------------------------------------


def test_a_returning_person_signs_in_and_lands_where_they_were_going(world, owner, alice):
    browser = world()
    response = browser.sign_in(ALICE, next="/trades?preset=30d")
    assert response.status_code == 303 and response.headers["location"] == "/trades?preset=30d"
    assert browser.token and len(browser.token) == 43
    body = browser.session().json()
    assert body["user"]["id"] == str(alice) and body["user"]["email"] == "alice@example.test"
    assert body["csrf_token"] and body["flags"] == {"is_admin": False, "mfa_fresh": False}
    [row] = [r for r in audit_rows(owner) if r["action"] == "login"]
    assert row["user_id"] == alice and row["actor"] == "user" and row["meta"] == {"mfa": False} and row["ip_hash"]
    assert owner.execute("SELECT last_login_at FROM users WHERE id = %s", (alice,)).fetchone()["last_login_at"]


def test_the_cookies_are_host_prefixed_secure_http_only_and_lax_and_nothing_is_cached(world):
    browser = world()
    start = browser.client.get("/auth/login")
    login_cookie = start.headers["set-cookie"].lower()
    assert login_cookie.startswith(f"{LOGIN_COOKIE.lower()}=") and "max-age=600" in login_cookie
    done = browser.sign_in(ALICE)
    cookies = {c.split("=", 1)[0]: c.lower() for c in done.headers.get_list("set-cookie")}
    session_cookie = cookies[SESSION_COOKIE]
    for flags in (session_cookie, cookies[LOGIN_COOKIE]):
        assert "secure" in flags and "httponly" in flags and "samesite=lax" in flags and "path=/" in flags
        assert "domain=" not in flags  # `__Host-` forbids it
    assert (
        f"max-age={90 * 24 * 3600}" in session_cookie and "max-age=0" in cookies[LOGIN_COOKIE]
    )  # the state is used up
    assert start.headers["cache-control"] == "no-store" and done.headers["cache-control"] == "no-store"


def test_a_new_person_with_an_invite_gets_an_account_with_the_defaults_and_uses_up_the_invite(world, owner, db):
    code = invites.create_invite(owner, label="Sam")
    browser = world()
    response = browser.sign_in(NEWCOMER, invite=code.lower())  # typed in lower case, as people do
    assert response.status_code == 303 and browser.token
    [created] = users_with_subject(owner, "kp_newcomer")
    assert created["email"] == "newcomer@example.test" and created["is_admin"] is False
    with db.tenant_session(created["id"]) as conn:
        assert conn.execute("SELECT count(*) AS n FROM tag_groups").fetchone()["n"] == 3  # Setup, Mistake, Emotion
        assert conn.execute("SELECT timezone FROM user_settings").fetchone()["timezone"]
    assert owner.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"] == 1
    assert [r["action"] for r in audit_rows(owner)] == ["signup"]  # a first sign-in is recorded as the sign-up, once
    assert browser.session().json()["user"]["email"] == "newcomer@example.test"
    second = world()  # a second person with the same single-use code
    refused = second.sign_in(person(subject="kp_second", email="second@example.test"), invite=code)
    assert refused.headers["location"] == "/invite?error=invite_invalid" and second.token is None
    assert users_with_subject(owner, "kp_second") == []


def test_a_new_person_without_a_valid_invite_gets_nothing_and_every_reason_looks_alike(world, owner):
    wrong_email = invites.create_invite(owner, email="someone.else@example.test")
    revoked = invites.create_invite(owner)
    owner.execute("UPDATE invites SET revoked_at = now() WHERE code_hash = %s", (invites.hash_code(revoked),))
    for label, params in {
        "no invite": {},
        "an invalid code": {"invite": "TJ-ZZZZ-ZZZZ"},
        "junk": {"invite": "x" * 300},
        "another person's invite": {"invite": wrong_email},
        "a revoked invite": {"invite": revoked},
    }.items():
        browser = world()
        response = browser.sign_in(NEWCOMER, **params)
        assert response.headers["location"] == "/invite?error=invite_invalid", label
        assert browser.token is None and users_with_subject(owner, "kp_newcomer") == [], label
    reasons = [r["meta"].get("reason") for r in audit_rows(owner) if r["action"] == "login_failed"]
    assert reasons == ["invite"] * 5  # the log says why; the page never does


def test_while_sign_up_is_open_a_new_person_needs_no_invite(world, owner):
    browser = world(signup_mode="open")
    assert browser.sign_in(NEWCOMER).status_code == 303 and browser.token
    assert len(users_with_subject(owner, "kp_newcomer")) == 1
    assert owner.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"] == 0


def test_a_sign_up_that_loses_the_race_for_the_last_use_is_undone_whole(world, owner, monkeypatch):
    code = invites.create_invite(owner)
    browser = world()
    real = invites.redeem
    monkeypatch.setattr(invites, "redeem", lambda *a, **k: False)  # used up between the check and the redemption
    response = browser.sign_in(NEWCOMER, invite=code)
    assert response.headers["location"] == "/invite?error=invite_invalid" and browser.token is None
    assert users_with_subject(owner, "kp_newcomer") == []  # not even a half-made account
    monkeypatch.setattr(invites, "redeem", real)
    assert world().sign_in(NEWCOMER, invite=code).status_code == 303  # and the invite was not spent by the failure


def test_an_email_the_provider_has_not_verified_never_signs_anyone_in(world, owner, alice):
    browser = world()
    response = browser.sign_in(person(subject=ALICE.subject, email=ALICE.email, email_verified=False))
    assert response.headers["location"] == "/invite?error=email_unverified" and browser.token is None
    code = invites.create_invite(owner)
    fresh = world().sign_in(person(subject="kp_x", email="x@example.test", email_verified=False), invite=code)
    assert fresh.headers["location"] == "/invite?error=email_unverified" and users_with_subject(owner, "kp_x") == []


def test_a_suspended_person_and_an_email_that_belongs_to_someone_else_are_turned_away(world, owner, alice, bob):
    owner.execute("UPDATE users SET status = 'suspended' WHERE id = %s", (alice,))
    browser = world()
    assert browser.sign_in(ALICE).headers["location"] == "/invite?error=account_unavailable" and browser.token is None
    code = invites.create_invite(owner)
    clash = world().sign_in(person(subject="kp_clash", email="BOB@example.test"), invite=code)  # bob's address
    assert clash.headers["location"] == "/invite?error=email_taken" and users_with_subject(owner, "kp_clash") == []
    assert owner.execute("SELECT count(*) AS n FROM invite_redemptions").fetchone()["n"] == 0


def test_signing_in_with_mfa_remembers_when_and_a_fresh_one_shows_as_fresh(world, owner, alice):
    browser = world()
    just_now = datetime.now(UTC) - timedelta(seconds=30)
    browser.sign_in(
        person(subject=ALICE.subject, email=ALICE.email, acr="mfa", amr=("email", "otp:sms"), auth_time=just_now)
    )
    assert browser.session().json()["flags"]["mfa_fresh"] is True
    [row] = [r for r in audit_rows(owner) if r["action"] == "login"]
    assert row["meta"] == {"mfa": True}
    stale = world()
    stale.sign_in(person(subject=ALICE.subject, email=ALICE.email, acr="mfa", auth_time=just_now - timedelta(hours=2)))
    assert stale.session().json()["flags"]["mfa_fresh"] is False  # MFA happened, but not recently
    plain = world()
    plain.sign_in(
        person(subject=ALICE.subject, email=ALICE.email, amr=("mfa",), auth_time=just_now)
    )  # `amr` alone is not it
    assert plain.session().json()["flags"]["mfa_fresh"] is False


# ---- the callback, broken one way at a time -----------------------------------------------------------------------------


def test_a_callback_with_the_wrong_state_or_none_is_refused_and_signs_no_one_in(world, owner, alice):
    browser = world()
    url, state = browser.start()
    code = browser.idp.sign_in(url, ALICE)
    for params in ({"code": code, "state": "not-the-state"}, {"code": code}, {"state": state}, {}):
        response = browser.come_back(**params)
        assert response.headers["location"].startswith("/invite?error="), params
        assert browser.token is None
        browser.client.cookies.set(LOGIN_COOKIE, browser.client.cookies.get(LOGIN_COOKIE) or "")
    assert browser.idp.completed == []  # the provider was never even asked to complete a suspect callback


def test_a_callback_replayed_or_without_its_cookie_or_with_an_edited_one_is_refused(world, alice):
    browser = world()
    url, state = browser.start()
    code = browser.idp.sign_in(url, ALICE)
    cookie = browser.client.cookies.get(LOGIN_COOKIE)
    assert browser.come_back(code=code, state=state).status_code == 303 and browser.token
    # Replaying it: the state cookie was used up, and the code is single use anyway.
    replay = browser.come_back(code=code, state=state)
    assert replay.headers["location"] == "/invite?error=expired"
    # With the old cookie put back, the provider still refuses the used code.
    browser.client.cookies.set(LOGIN_COOKIE, cookie)
    assert browser.come_back(code=code, state=state).headers["location"] == "/invite?error=signin_failed"
    # An edited cookie is not ours.
    forged = world()
    url, state = forged.start()
    good = forged.client.cookies.get(LOGIN_COOKIE)
    forged.client.cookies.set(LOGIN_COOKIE, good[:-2] + ("AA" if good[-2:] != "AA" else "BB"))
    assert (
        forged.come_back(code=forged.idp.sign_in(url, ALICE), state=state).headers["location"]
        == "/invite?error=expired"
    )


def test_a_token_for_another_nonce_or_a_pkce_verifier_that_does_not_match_or_a_provider_error_is_refused(
    world, owner, alice
):
    browser = world()
    assert (
        browser.sign_in(ALICE, reported_nonce="someone-elses-nonce").headers["location"]
        == "/invite?error=signin_failed"
    )
    assert browser.token is None
    refused = world()
    url, state = refused.start()
    refused.idp.attempts[state].challenge = "a-different-challenge"  # the code was issued for another verifier
    code = refused.idp.sign_in(url, ALICE)
    assert refused.come_back(code=code, state=state).headers["location"] == "/invite?error=signin_failed"
    cancelled = world()
    cancelled.start()
    assert cancelled.come_back(error="access_denied", state="x").headers["location"] == "/invite?error=signin_failed"
    down = world()
    url, state = down.start()
    down.idp.fail_with = "network"
    assert (
        down.come_back(code=down.idp.sign_in(url, ALICE), state=state).headers["location"]
        == "/invite?error=signin_failed"
    )
    reasons = [r["meta"]["reason"] for r in audit_rows(owner) if r["action"] == "login_failed"]
    assert reasons == ["nonce", "token_exchange", "provider_error", "network"]


@pytest.mark.parametrize(
    "bad",
    [
        "//evil.example/x",
        "https://evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "/auth/login",
        "relative",
        "/\r\nSet-Cookie: x=1",
    ],
)
def test_a_next_address_that_leaves_the_site_goes_to_the_home_page_instead(world, alice, bad):
    browser = world()
    assert browser.sign_in(ALICE, next=bad).headers["location"] == "/"


def test_nothing_secret_ever_reaches_the_audit_log_or_the_address_bar(world, owner, alice):
    browser = world()
    url, state = browser.start()
    code = browser.idp.sign_in(url, ALICE)
    ok = browser.come_back(code=code, state=state)
    token = browser.token
    bad = world()
    url2, state2 = bad.start()
    code2 = bad.idp.sign_in(url2, ALICE, reported_nonce="x")
    failed = bad.come_back(code=code2, state=state2)
    dump = str(audit_rows(owner))
    for secret in (code, state, code2, state2, token or "", SECRET.decode()):
        assert secret not in dump
    for response in (ok, failed):
        assert code not in response.headers["location"] and state not in response.headers["location"]
    assert token not in ok.headers["location"]


# ---- the session after sign-in ------------------------------------------------------------------------------------------


def test_signing_in_again_ends_the_old_session_so_a_token_never_outlives_its_sign_in(world, alice):
    browser = world()
    browser.sign_in(ALICE)
    old = browser.token
    browser.sign_in(ALICE)  # the same browser, still carrying its old session cookie
    assert browser.token != old
    stale = TestClient(browser.client.app, base_url=ORIGIN, follow_redirects=False)
    stale.cookies.set(SESSION_COOKIE, old)
    assert stale.get("/api/v1/session").status_code == 401  # the old token is dead, even with the minute's cache
    assert browser.session().status_code == 200


def test_signing_out_ends_the_session_checks_the_csrf_token_and_says_where_to_go_next(world, owner, alice):
    browser = world()
    browser.sign_in(ALICE)
    session = browser.session().json()
    write = {"sec-fetch-site": "same-origin", "x-csrf-token": session["csrf_token"]}
    assert browser.client.post("/auth/logout").status_code == 403  # no token: refused, and still signed in
    assert browser.client.post("/auth/logout", headers={**write, "x-csrf-token": "wrong"}).status_code == 403
    cross_site = {**write, "sec-fetch-site": "cross-site", "origin": "https://evil.example"}
    assert (
        browser.client.post("/auth/logout", headers=cross_site).status_code == 403
    )  # a page elsewhere cannot sign you out
    assert browser.session().status_code == 200
    out = browser.client.post("/auth/logout", headers=write)
    assert out.status_code == 200 and out.json() == {"redirect": f"https://idp.test/logout?redirect={ORIGIN}/"}
    assert browser.session().status_code == 401  # at once, not after the cache's minute
    assert [r["action"] for r in audit_rows(owner)].count("logout") == 1


def test_the_csrf_token_belongs_to_one_session_and_a_different_one_is_not_accepted(world, alice):
    first, second = world(), world()
    first.sign_in(ALICE)
    second.sign_in(ALICE)
    mine = first.session().json()["csrf_token"]
    theirs = second.session().json()["csrf_token"]
    assert mine != theirs
    refused = second.client.post("/auth/logout", headers={"sec-fetch-site": "same-origin", "x-csrf-token": mine})
    assert refused.status_code == 403 and second.session().status_code == 200


# ---- when sign-in is not set up ------------------------------------------------------------------------------------------


def test_without_a_provider_the_sign_in_routes_say_so_and_nobody_can_get_in(db, app_url, login_url):
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=Path("/nonexistent"),
        session_secret=SECRET,
        database_url=app_url,
        login_database_url=login_url,
    )
    with TestClient(create_app(config, database=db), base_url=ORIGIN, follow_redirects=False) as client:
        assert client.get("/auth/login").status_code == 503
        assert client.get("/auth/callback", params={"code": "x", "state": "y"}).status_code == 503
        assert client.get("/api/v1/session").status_code == 401  # real sessions are in place; there are just none


# ---- seeing and ending your own sign-ins --------------------------------------------------------------------------------

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SAFARI_PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"


def write_headers(browser: Browser) -> dict[str, str]:
    return {"sec-fetch-site": "same-origin", "x-csrf-token": browser.session().json()["csrf_token"]}


def test_a_person_sees_each_place_they_are_signed_in_and_signing_one_out_stops_it_at_once(world, owner, alice):
    laptop = world()
    laptop.client.headers["user-agent"] = CHROME
    laptop.sign_in(ALICE)
    phone = laptop.another(SAFARI_PHONE)
    phone.sign_in(ALICE)
    assert phone.session().status_code == 200  # the server has now cached the phone's session

    listed = laptop.client.get("/api/v1/sessions").json()["sessions"]
    assert [(row["device"], row["current"]) for row in listed] == [
        ("Safari on iPhone", False),
        ("Chrome on Windows", True),
    ]
    assert all(row["mfa"] is False and row["expires_at"] for row in listed)
    phone_id = listed[0]["id"]

    refused = laptop.client.delete(f"/api/v1/sessions/{phone_id}")  # a write without the token
    assert refused.status_code == 403 and phone.session().status_code == 200
    ended = laptop.client.delete(f"/api/v1/sessions/{phone_id}", headers=write_headers(laptop))
    assert ended.status_code == 200 and ended.json()["ended"] == 1
    assert [row["device"] for row in ended.json()["sessions"]] == ["Chrome on Windows"]
    assert phone.session().status_code == 401  # at once, though the phone's session was cached
    assert laptop.session().status_code == 200

    [event] = [r for r in audit_rows(owner) if r["action"] == "session_revoked"]
    assert event["user_id"] == alice and event["actor"] == "user" and event["ip_hash"]
    assert CHROME not in str(audit_rows(owner)) and phone_id not in str(audit_rows(owner))


def test_signing_out_everywhere_else_ends_every_other_device_and_keeps_this_one(world, owner, alice):
    laptop = world()
    laptop.sign_in(ALICE)
    others = [laptop.another(CHROME), laptop.another(SAFARI_PHONE)]
    for device in others:
        device.sign_in(ALICE)
        assert device.session().status_code == 200
    mine = laptop.client.get("/api/v1/sessions").json()
    assert len(mine["sessions"]) == 3

    result = laptop.client.delete("/api/v1/sessions", headers=write_headers(laptop)).json()
    assert result["ended"] == 2 and [row["current"] for row in result["sessions"]] == [True]
    assert [device.session().status_code for device in others] == [401, 401]
    assert laptop.session().status_code == 200
    [event] = [r for r in audit_rows(owner) if r["action"] == "logout_everywhere"]
    assert event["meta"] == {"ended": 2} and event["user_id"] == alice


def test_the_session_you_are_using_cannot_be_ended_that_way_and_a_stranger_cannot_end_yours(world, owner, alice, bob):
    laptop = world()
    laptop.sign_in(ALICE)
    mine = laptop.client.get("/api/v1/sessions").json()["sessions"][0]["id"]
    refused = laptop.client.delete(f"/api/v1/sessions/{mine}", headers=write_headers(laptop))
    assert refused.status_code == 422 and "Use Sign out" in refused.json()["detail"]
    assert laptop.session().status_code == 200

    stranger = laptop.another(CHROME)
    stranger.sign_in(person(subject="idp|bob@example.test", email="bob@example.test"))
    assert [row["current"] for row in stranger.client.get("/api/v1/sessions").json()["sessions"]] == [True]  # only his
    answer = stranger.client.delete(f"/api/v1/sessions/{mine}", headers=write_headers(stranger))
    assert answer.status_code == 404 and laptop.session().status_code == 200
    stranger.client.delete("/api/v1/sessions", headers=write_headers(stranger))  # his "everywhere else" is only his
    assert laptop.session().status_code == 200
