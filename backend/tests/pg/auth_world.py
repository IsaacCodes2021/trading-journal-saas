"""A browser and a server to sign in to, for the tests that play the whole sign-in (the real app, the real session store and
a real Postgres, with a pretend identity provider). `world` is registered for every pg test in conftest.py."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fake_idp import FakeIdp, person
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE

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
