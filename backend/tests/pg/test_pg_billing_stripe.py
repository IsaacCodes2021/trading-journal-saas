"""Billing through the whole app with the Stripe connector, against a stand-in for Stripe's API (tests/stripe_world.py): the
routes, the webhook route, the database and the sweep are the real ones; only Stripe is played. The same contract as the
Paddle connector (tests/pg/test_pg_billing_paddle.py), so switching TJ_BILLING_PROVIDER from paddle to stripe changes
nothing a person sees (docs/design/stripe-plan.md)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest
from auth_world import ALICE, NEWCOMER, audit_rows
from billing_world import Clock, billing_app, close_browser, open_browser
from stripe_world import FakeStripeApi, settings

from tradejournal.pg import billing as pg_billing
from tradejournal.pg import invites, sweep


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api(clock):
    return FakeStripeApi(clock=clock)  # the real wall clock for the signature's time


@pytest.fixture
def started():
    browsers = []
    yield browsers
    for browser in browsers:
        close_browser(browser)


@pytest.fixture
def app(db, app_url, login_url, api, clock, trial):
    return billing_app(db, app_url, login_url, api.provider(), clock, billing_provider="stripe", stripe=settings())


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


def open_checkout(browser):
    response = write(browser, "POST", "/api/v1/billing/checkout")
    assert response.status_code == 200, response.text
    pay_url = response.json()["pay_url"]
    assert pay_url.startswith("/billing/stripe/pay?cs=")
    return pay_url.split("cs=")[1]


def subscribe(browser, api):
    """Press Subscribe and pay on Stripe's page; Stripe sends `customer.subscription.created` to the real route."""
    session = open_checkout(browser)
    assert post(browser, api.complete(session)).json()["outcome"] == "applied"
    return session, next(reversed(api.subscriptions))


def test_subscribing_during_the_trial_asks_stripe_for_the_free_days_left(browser, owner, api, clock):
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    clock.advance(days=2, hours=5)  # 4 days and 19 hours left
    session, sub = subscribe(browser, api)
    form = api.sessions[session]["form"]
    assert form["subscription_data[trial_period_days]"] == "5" == str(math.ceil(4 + 19 / 24))
    user = owner.execute("SELECT id FROM users WHERE email = %s", (NEWCOMER.email,)).fetchone()["id"]
    assert form["client_reference_id"] == form["subscription_data[metadata][roundturn_user_id]"] == str(user)
    assert form["customer"] in api.customers and form["managed_payments[enabled]"] == "true"
    state = billing(browser)
    assert (state["state"], state["first_charge_pending"], state["can_manage"]) == ("active", True, True)
    row = owner.execute("SELECT provider, provider_subscription_id, status FROM subscriptions").fetchone()
    assert (row["provider"], row["provider_subscription_id"], row["status"]) == ("stripe", sub, "trialing")


def test_the_payment_link_sends_the_person_to_stripe_and_back_once_paid(browser, api, alice):
    signed_in(browser)
    session = open_checkout(browser)
    there = browser.client.get(f"/billing/stripe/pay?cs={session}", follow_redirects=False)
    assert there.status_code == 303 and there.headers["location"].startswith("https://checkout.stripe.com/")
    post(browser, api.complete(session))
    back = browser.client.get(f"/billing/stripe/pay?cs={session}", follow_redirects=False)
    assert back.headers["location"] == "/settings/plan?checkout=done"
    assert billing(browser)["state"] == "active"


def test_a_second_subscribe_reuses_the_open_checkout_and_a_later_one_closes_it(browser, api, clock, alice):
    signed_in(browser)
    first = open_checkout(browser)
    assert open_checkout(browser) == first  # within half an hour: the same checkout
    clock.advance(minutes=31)
    second = open_checkout(browser)
    assert second != first and api.sessions[first]["status"] == "expired"


def test_cancel_and_resume_go_to_stripe_and_its_event_confirms_each(browser, api, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    assert write(browser, "POST", "/api/v1/billing/cancel").json()["state"] == "cancelling"
    assert api.requests[-1][:3] == ("POST", f"/v1/subscriptions/{sub}", {"cancel_at_period_end": "true"})
    assert post(browser, api.announce(sub)).json()["outcome"] == "applied"
    assert billing(browser)["state"] == "cancelling"  # Stripe's word, with its canceled_at already set
    assert write(browser, "POST", "/api/v1/billing/resume").json()["state"] == "active"
    assert api.requests[-1][:3] == ("POST", f"/v1/subscriptions/{sub}", {"cancel_at_period_end": "false"})
    assert post(browser, api.announce(sub)).json()["outcome"] == "applied"
    assert billing(browser)["state"] == "active"


def test_the_providers_page_is_stripes_portal_for_this_person(browser, api, alice):
    signed_in(browser)
    subscribe(browser, api)
    url = write(browser, "POST", "/api/v1/billing/portal").json()["url"]
    assert url.startswith("https://billing.stripe.com/")
    assert api.requests[-1][2]["customer"] in api.customers


def test_a_failed_payment_gives_the_grace_then_stripes_cancel_makes_it_read_only(browser, api, clock, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    clock.advance(days=30)
    assert post(browser, api.fail_payment(sub)).json()["outcome"] == "applied"
    state = billing(browser)
    assert state["state"] == "past_due"
    assert datetime.fromisoformat(state["ends_at"]) == clock() + timedelta(days=7)  # 7 days from the first failure
    clock.advance(days=14)  # Stripe's retries ran out and it cancelled (the dashboard says cancel)
    assert post(browser, api.finish(sub)).json()["outcome"] == "applied"
    assert (billing(browser)["state"], billing(browser)["reason"]) == ("read_only", "lapsed")


def test_a_cancel_that_reaches_the_period_end_ends_access_then(browser, api, clock, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    write(browser, "POST", "/api/v1/billing/cancel")
    post(browser, api.announce(sub))
    clock.advance(days=30)
    assert post(browser, api.finish(sub)).json()["outcome"] == "applied"
    assert billing(browser)["state"] == "read_only"


def test_a_refund_made_at_stripe_is_recorded_for_the_operator_and_changes_no_access(browser, owner, api, alice):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    assert post(browser, api.refund(sub)).json()["outcome"] == "refund_recorded"
    assert billing(browser)["state"] == "active"
    stored = owner.execute("SELECT type, outcome FROM billing_events ORDER BY id DESC LIMIT 1").fetchone()
    assert (stored["type"], stored["outcome"]) == ("charge.refunded", "refund_recorded")


def test_the_checkout_completed_event_is_kept_and_grants_nothing_by_itself(browser, owner, api, alice):
    signed_in(browser)
    session, _ = subscribe(browser, api)
    assert post(browser, api.checkout_completed(session)).json()["outcome"] == "received"
    assert owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 1


def test_a_stripe_subscription_that_names_someone_else_grants_nothing(browser, owner, api, alice, bob):
    signed_in(browser)
    session = open_checkout(browser)
    delivery = api.complete(session, metadata={"roundturn_user_id": str(bob)})
    assert post(browser, delivery).json()["outcome"] == "alert"
    assert owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0


def test_an_event_of_the_other_mode_is_kept_and_never_applied(browser, owner, api, alice):
    signed_in(browser)
    api.complete(open_checkout(browser))  # this server is in test mode; the event below says live
    sub = next(reversed(api.subscriptions))
    delivery = api.deliver("customer.subscription.created", api.subscriptions[sub], livemode=True)
    assert post(browser, delivery).json()["outcome"] == "unknown_type"
    assert owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0
    stored = owner.execute("SELECT payload FROM billing_events ORDER BY id DESC LIMIT 1").fetchone()["payload"]
    assert stored["ignored"] == "other_mode"


def test_asking_to_delete_stops_the_renewal_at_stripe_and_the_sweep_ends_it_once_deleted(
    app, browser, owner, system, api, clock, alice, started
):
    signed_in(browser)
    _, sub = subscribe(browser, api)
    email = browser.session().json()["user"]["email"]
    assert write(browser, "POST", "/api/v1/account/deletion", {"email": email}).status_code == 200
    assert api.subscriptions[sub]["cancel_at_period_end"] is True
    again = open_browser(app)
    started.append(again)
    assert again.sign_in(ALICE).headers["location"].startswith("/settings/privacy?restored=1")
    assert api.subscriptions[sub]["cancel_at_period_end"] is False  # coming back resumed it at Stripe
    with system.system_session("sweep") as conn:
        pg_billing.queue_cancellation_of(conn, "stripe", sub)  # what the deletion's own transaction queues
    report = sweep.run_billing(system, api.provider(webhook_secret=None), apply=True, now=clock())
    assert report.cancels_sent == 1 and api.subscriptions[sub]["status"] == "canceled"
    assert ("DELETE", f"/v1/subscriptions/{sub}") in [r[:2] for r in api.requests]
    assert report.corrected == 1  # and the nightly check, in the same run, took Stripe's word that it has ended
    assert [r["action"] for r in audit_rows(owner) if r["action"] == "billing_cancelled_after_deletion"]
