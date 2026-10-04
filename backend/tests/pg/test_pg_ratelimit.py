"""Rate limits on the routes that cost something, on the real app and a real Postgres (doc 04 T2, T5, T18)."""

from __future__ import annotations

from pathlib import Path

import pytest
from api_fakes import FakeAudit, FakeSessions
from fake_idp import FakeIdp, person
from fastapi.testclient import TestClient
from helpers import SAMPLE, fixture_bytes
from pg_helpers import ORIGIN, SECRET, make_account

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.pg_services import PgAccountService
from tradejournal.api.ports import Services
from tradejournal.api.ratelimit import RateLimiter, RateLimits, Rule
from tradejournal.auth.csrf import csrf_token
from tradejournal.pg import export as pg_export


class Clock:
    def __init__(self) -> None:
        self.now = 9000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


def tenant_app(db, system, clock, limits: RateLimits):
    audit = FakeAudit()
    sessions = FakeSessions()
    services = Services(sessions=sessions, accounts=PgAccountService(db, system), audit=audit)
    config = ApiConfig(app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=SECRET, rate_limits=limits)
    app = create_app(config, services, database=db, limiter=RateLimiter(clock=clock))
    return app, sessions, audit


def client_for(app, sessions, user_id):
    token, session = sessions.sign_in(user_id)
    headers = {"sec-fetch-site": "same-origin", "x-csrf-token": csrf_token(SECRET, session.id)}
    client = TestClient(app, base_url=ORIGIN, headers=headers, raise_server_exceptions=False)
    client.cookies.set(SESSION_COOKIE, token)
    return client


# ---- export ---------------------------------------------------------------------------------------------------------------


def test_a_person_can_export_only_so_often_and_the_refusal_says_when(db, system, alice, bob, clock, monkeypatch):
    app, sessions, audit = tenant_app(db, system, clock, RateLimits(export=Rule("export", 2, 3600)))
    ann, ben = client_for(app, sessions, alice), client_for(app, sessions, bob)
    builds = []
    real = pg_export.build
    monkeypatch.setattr(pg_export, "build", lambda *a, **k: builds.append(1) or real(*a, **k))

    assert [ann.get("/api/v1/export").status_code for _ in range(2)] == [200, 200]
    refused = ann.get("/api/v1/export")
    assert refused.status_code == 429 and refused.headers["retry-after"] == "1800"
    assert refused.json()["detail"] == "Too many requests. Try again in 30 minutes."
    assert len(builds) == 2 and len(audit.events) == 2  # the refused request built nothing and wrote no audit row

    assert ben.get("/api/v1/export").status_code == 200  # someone else's allowance is untouched
    clock.now += 1800
    assert ann.get("/api/v1/export").status_code == 200  # one place came back
    assert ann.get("/api/v1/export").status_code == 429


def test_an_anonymous_request_for_an_export_costs_the_signed_in_person_nothing(db, system, alice, clock):
    app, sessions, _ = tenant_app(db, system, clock, RateLimits(export=Rule("export", 1, 3600)))
    stranger = TestClient(app, base_url=ORIGIN)
    assert [stranger.get("/api/v1/export").status_code for _ in range(5)] == [401] * 5
    assert client_for(app, sessions, alice).get("/api/v1/export").status_code == 200


# ---- imports --------------------------------------------------------------------------------------------------------------


def test_previewing_and_importing_are_limited_separately_per_person(db, system, alice, clock):
    app, sessions, _ = tenant_app(
        db, system, clock, RateLimits(import_preview=Rule("p", 2, 3600), import_commit=Rule("c", 1, 3600))
    )
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
    ann = client_for(app, sessions, alice)
    upload = {"file": (SAMPLE, fixture_bytes(SAMPLE), "text/csv")}
    form = {"account_id": str(account.id)}

    assert [ann.post("/api/v1/imports/preview", data=form, files=upload).status_code for _ in range(2)] == [200, 200]
    refused = ann.post("/api/v1/imports/preview", data=form, files=upload)
    assert refused.status_code == 429 and refused.headers["retry-after"] == "1800"

    assert ann.post("/api/v1/imports", data=form, files=upload).status_code == 201  # its own allowance
    assert ann.post("/api/v1/imports", data=form, files=upload).status_code == 429
    with db.tenant_session(alice) as conn:  # the refused calls changed nothing
        assert conn.execute("SELECT count(*) AS n FROM imports").fetchone()["n"] == 1


# ---- signing in -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def signin(db, owner, alice, login_url, app_url, clock):
    started = []

    def build(limits: RateLimits, *, header: str | None = None):
        config = ApiConfig(
            app_origin=ORIGIN,
            web_dist=Path("/nonexistent"),
            session_secret=SECRET,
            database_url=app_url,
            login_database_url=login_url,
            rate_limits=limits,
            client_ip_header=header,
        )
        idp = FakeIdp()
        client = TestClient(
            create_app(config, database=db, idp=idp, limiter=RateLimiter(clock=clock)),
            base_url=ORIGIN,
            follow_redirects=False,
        )
        client.__enter__()
        started.append(client)
        return client, idp

    yield build
    for client in started:
        client.__exit__(None, None, None)


def test_starting_a_sign_in_is_limited_per_client_address_when_the_host_names_the_header(signin):
    client, _ = signin(RateLimits(login=Rule("login", 3, 60)), header="X-Client-Address")
    one, two = {"x-client-address": "203.0.113.1"}, {"x-client-address": "203.0.113.2"}
    assert [client.get("/auth/login", headers=one).status_code for _ in range(3)] == [302, 302, 302]
    refused = client.get("/auth/login", headers=one)
    assert refused.status_code == 429 and refused.headers["retry-after"] == "20"
    assert "set-cookie" not in refused.headers  # no sign-in was started
    assert client.get("/auth/login", headers=two).status_code == 302  # another address


def test_a_header_nobody_configured_cannot_be_used_to_pick_a_bucket(signin):
    client, _ = signin(RateLimits(login=Rule("login", 3, 60)))  # no header named: the socket's address counts
    for n in range(3):
        assert client.get("/auth/login", headers={"x-client-address": f"198.51.100.{n}"}).status_code == 302
    assert client.get("/auth/login", headers={"x-client-address": "198.51.100.99"}).status_code == 429


def test_trying_invite_codes_has_a_stricter_limit_of_its_own_and_plain_sign_in_is_not_counted_in_it(signin):
    client, _ = signin(RateLimits(login=Rule("login", 100, 60), invite_attempts=Rule("invite", 2, 900)))
    assert client.get("/auth/login", params={"invite": "TJ-AAAA-AAAA"}).status_code == 302
    assert client.get("/auth/login", params={"invite": "TJ-BBBB-BBBB"}).status_code == 302
    refused = client.get("/auth/login", params={"invite": "TJ-CCCC-CCCC"})
    assert refused.status_code == 429 and refused.headers["retry-after"] == "450"
    assert client.get("/auth/login").status_code == 302  # signing in without a code is not a guess
    assert client.get("/auth/login", params={"next": "/trades"}).status_code == 302


def test_the_way_back_from_the_provider_is_limited_too_and_a_refusal_does_not_sign_anyone_in(signin):
    client, idp = signin(RateLimits(callback=Rule("callback", 2, 60)))
    for _ in range(2):
        assert client.get("/auth/callback", params={"error": "access_denied"}).status_code == 303
    refused = client.get("/auth/callback", params={"error": "access_denied"})
    assert refused.status_code == 429 and SESSION_COOKIE not in client.cookies
    assert idp.completed == []


def test_a_real_person_signing_in_normally_is_nowhere_near_the_limits(signin, alice):
    client, idp = signin(RateLimits())
    for _ in range(5):
        url = client.get("/auth/login", params={"next": "/trades"}).headers["location"]
        code = idp.sign_in(url, person(subject="idp|alice@example.test", email="alice@example.test"))
        state = url.split("state=")[1].split("&")[0]
        assert client.get("/auth/callback", params={"code": code, "state": state}).status_code == 303
    assert client.get("/api/v1/session").status_code == 200
