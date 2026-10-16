"""Billing through the whole app with the Paddle connector, against a stand-in for Paddle's API (tests/paddle_world.py):
the routes, the webhook route, the database and the sweep are the real ones; only Paddle is played. It shows the connector
keeps the same contract as the fake provider the other billing tests use (docs/design/paddle-integration.md section 7.1)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest
from auth_world import ALICE, NEWCOMER, audit_rows
from billing_world import Clock, billing_app, close_browser, open_browser
from paddle_world import FakePaddleApi, settings

from tradejournal.pg import billing as pg_billing
from tradejournal.pg import invites, sweep


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api(clock):
    return FakePaddleApi(clock=clock)  # the real wall clock: the signature's five seconds are real seconds


@pytest.fixture
def started():
    browsers = []
    yield browsers
    for browser in browsers:
        close_browser(browser)


@pytest.fixture
def app(db, app_url, login_url, api, clock, trial):
    return billing_app(db, app_url, login_url, api.provider(), clock, billing_provider="paddle", paddle=settings())


@pytest.fixture
def browser(app, started):
    browser = open_browser(app)
    started.append(browser)
    return browser


def signed_in(browser, identity=ALICE, **params):
    response = browser.sign_in(identity, **params)
    assert response.status_code == 303 and browser.token, response.headers.get("location")
    return browser


def write(browser, method, path, body=None):
    token = browser.session().json()["csrf_token"]
    return browser.client.request(
        method, path, headers={"sec-fetch-site": "same-origin", "x-csrf-token": token}, json=body
    )


def billing(browser):
    response = browser.client.get("/api/v1/billing")
    assert response.status_code == 200, response.text
    return response.json()


def post(browser, delivery):
    return browser.client.post("/webhooks/billing", content=delivery.body, headers=delivery.headers)


def subscribe(browser, api):
    """Press Subscribe; Paddle takes the payment and sends `subscription.created` to the real route."""
    response = write(browser, "POST", "/api/v1/billing/checkout")
    assert response.status_code == 200, response.text
    transaction = response.json()["pay_url"].split("_ptxn=")[1]
    assert post(browser, api.complete(transaction)).json()["outcome"] == "applied"
    return transaction, next(reversed(api.subscriptions))


def test_subscribing_during_the_trial_asks_paddle_for_a_trial_of_the_free_days_left(browser, owner, api, clock):
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    clock.advance(days=2, hours=5)  # 4 days and 19 hours left
    transaction, sub = subscribe(browser, api)
    body = api.transactions[transaction]
    assert body["items"][0]["price"]["trial_period"]["frequency"] == 5 == math.ceil(4 + 19 / 24)
    user = owner.execute("SELECT id FROM users WHERE email = %s", (NEWCOMER.email,)).fetchone()["id"]
    assert body["custom_data"] == {"roundturn_user_id": str(user)} and body["customer_id"] in api.customers
    state = billing(browser)
    assert (state["state"], state["first_charge_pending"], state["can_manage"]) == ("active", True, True)
    row = owner.execute("SELECT provider, provider_subscription_id, status FROM subscriptions").fetchone()
    assert (row["provider"], row["provider_subscription_id"], row["status"]) == ("paddle", sub, "trialing")


def test_cancel_and_resume_go_to_paddle_and_its_notification_confirms_each(browser, owner, api, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    assert write(browser, "POST", "/api/v1/billing/cancel").json()["state"] == "cancelling"
    assert api.requests[-1][1:] == (f"/subscriptions/{sub}/cancel", {"effective_from": "next_billing_period"})
    assert post(browser, api.announce(sub)).json()["outcome"] == "applied"
    assert billing(browser)["state"] == "cancelling"  # Paddle's own word: a cancel scheduled for the period's end
    assert write(browser, "POST", "/api/v1/billing/resume").json()["state"] == "active"
    assert api.requests[-1] == ("PATCH", f"/subscriptions/{sub}", {"scheduled_change": None})
    assert post(browser, api.announce(sub)).json()["outcome"] == "applied"
    assert billing(browser)["state"] == "active"


def test_the_providers_page_is_paddles_portal_for_this_person_and_subscription(browser, api, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    url = write(browser, "POST", "/api/v1/billing/portal").json()["url"]
    assert url.startswith("https://customer-portal.paddle.com/")
    assert api.requests[-1][2] == {"subscription_ids": [sub]}


def test_a_failed_payment_gives_the_grace_then_paddles_cancel_makes_it_read_only(browser, api, clock, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    clock.advance(days=30)
    assert post(browser, api.fail_payment(sub)).json()["outcome"] == "applied"
    state = billing(browser)
    assert state["state"] == "past_due"
    assert datetime.fromisoformat(state["ends_at"]) == clock() + timedelta(days=7)  # 7 days from the first failure
    clock.advance(days=14)  # Paddle's retries ran out and it cancelled (dunning set to cancel)
    assert post(browser, api.finish(sub)).json()["outcome"] == "applied"
    assert (billing(browser)["state"], billing(browser)["reason"]) == ("read_only", "lapsed")


def test_a_refund_made_at_paddle_is_recorded_and_changes_no_access(browser, owner, api, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    assert post(browser, api.refund(sub)).json()["outcome"] == "refund_recorded"
    assert billing(browser)["state"] == "active"
    assert [r["meta"] for r in audit_rows(owner) if r["action"] == "billing_refund_recorded"] == [{"action": "refund"}]


def test_a_paddle_subscription_that_names_someone_else_grants_nothing(browser, owner, api, alice, bob):
    signed_in(browser)
    response = write(browser, "POST", "/api/v1/billing/checkout")
    transaction = response.json()["pay_url"].split("_ptxn=")[1]
    delivery = api.complete(transaction, custom_data={"roundturn_user_id": str(bob)})
    assert post(browser, delivery).json()["outcome"] == "alert"
    assert owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0


def test_asking_to_delete_stops_the_renewal_at_paddle_and_the_sweep_ends_it_once_deleted(
    app, browser, owner, system, api, clock, alice, started
):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    email = browser.session().json()["user"]["email"]
    assert write(browser, "POST", "/api/v1/account/deletion", {"email": email}).status_code == 200
    assert api.subscriptions[sub]["scheduled_change"]["action"] == "cancel"
    again = open_browser(app)
    started.append(again)
    assert again.sign_in(ALICE).headers["location"].startswith("/settings/privacy?restored=1")
    assert api.subscriptions[sub]["scheduled_change"] is None  # coming back resumed it at Paddle
    with system.system_session("sweep") as conn:
        pg_billing.queue_cancellation_of(conn, "paddle", sub)  # what the deletion's own transaction queues
    report = sweep.run_billing(system, api.provider(webhook_secret=None), apply=True, now=clock())
    assert report.cancels_sent == 1 and api.subscriptions[sub]["status"] == "canceled"
    assert ("POST", f"/subscriptions/{sub}/cancel", {"effective_from": "immediately"}) in api.requests
    assert report.corrected == 1  # and the nightly check, in the same run, took Paddle's word that it has ended
