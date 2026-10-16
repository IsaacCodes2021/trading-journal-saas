"""Helpers for the billing tests: a clock the test moves, the fake provider, and an app with billing switched on."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auth_world import ORIGIN, SECRET, Browser
from fake_idp import FakeIdp

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.billing.fake import FakeBilling
from tradejournal.pg import billing as pg_billing
from tradejournal.pg.provision import read_env_file

ENV_FILE = Path(__file__).resolve().parents[2] / ".env.test"
WEBHOOK_SECRET = "fake-webhook-" + "secret"  # built from pieces: never a high-entropy literal in the repository
DAY = timedelta(days=1)


class Clock:
    """Starts just after the real time (sign-in, sessions and the trial given at sign-up use the real clock, so what they
    record has already started by this clock), and moves only when a test says so."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=5)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


def webhook_url() -> str:
    env = {**read_env_file(ENV_FILE), **os.environ}
    return env["TJ_TEST_WEBHOOK_URL"]


def make_fake(clock: Clock) -> FakeBilling:
    return FakeBilling(secret=WEBHOOK_SECRET, clock=clock)


def billing_app(db, app_url, login_url, fake: FakeBilling, clock: Clock, *, enabled: bool = True, **config_changes):
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=Path("/nonexistent"),
        session_secret=SECRET,
        database_url=app_url,
        login_database_url=login_url,
        webhook_database_url=webhook_url(),
        billing_enabled=enabled,
        billing_provider="fake",
        **config_changes,
    )
    idp = FakeIdp()
    app = create_app(config, database=db, idp=idp, billing_provider=fake, clock=clock)
    app.state.test_idp = idp
    return app


def open_browser(app) -> Browser:
    """A browser on the app, started (the app's startup opens its database pools). Close it with `close_browser`."""
    browser = Browser(app, app.state.test_idp)
    browser.client.__enter__()
    return browser


def close_browser(browser: Browser) -> None:
    browser.client.__exit__(None, None, None)


def checkout_for(system, fake: FakeBilling, clock: Clock, user, *, free_days: int = 0, email="p@example.test") -> str:
    """A checkout the server opened for this person, as the checkout route would record it."""
    customer = fake.find_or_create_customer(email=email, user_id=user)
    checkout = fake.create_checkout(customer_id=customer, user_id=user, free_days=free_days)
    with system.system_session("login") as conn:
        pg_billing.save_customer(conn, user, "fake", customer)
        pg_billing.record_checkout(conn, checkout.checkout_id, "fake", user, now=clock())
    return checkout.checkout_id


def apply(system, fake: FakeBilling, delivery, now: datetime) -> pg_billing.Applied:
    """What the webhook route does after the signature: store and apply, as the webhook role."""
    event = fake.verify_webhook(delivery.headers, delivery.body)
    with system.system_session("webhook") as conn:
        return pg_billing.apply_event(conn, "fake", event, now=now)


def subscribed(system, fake: FakeBilling, clock: Clock, user, *, free_days: int = 0) -> str:
    """A person who paid: their checkout completed and its first event applied. Returns the subscription id."""
    checkout = checkout_for(system, fake, clock, user, free_days=free_days)
    delivery = fake.complete_checkout(checkout)
    applied = apply(system, fake, delivery, clock())
    assert applied.outcome == "applied", applied
    return next(s.subscription_id for s in fake.subscriptions.values() if s.checkout_id == checkout)
