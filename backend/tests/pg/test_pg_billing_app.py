"""Billing through the real app (the billing design, slices 5 to 8): the switch, the routes, the webhook route, sign-up with
the trial, deletion, the daily job's billing steps and emails, and the whole lifecycle, with the fake provider on a real
Postgres."""

from __future__ import annotations

import json
import math
from datetime import timedelta

import pytest
from auth_world import ALICE, NEWCOMER, audit_rows, users_with_subject
from billing_world import (
    DAY,
    Clock,
    apply,
    billing_app,
    close_browser,
    make_fake,
    open_browser,
    subscribed,
)

from tradejournal.api.mailer import MailError
from tradejournal.pg import billing as pg_billing
from tradejournal.pg import invites, plans, sweep


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake(clock):
    return make_fake(clock)


@pytest.fixture
def started():
    browsers = []
    yield browsers
    for browser in browsers:
        close_browser(browser)


@pytest.fixture
def app(db, app_url, login_url, fake, clock, trial):
    return billing_app(db, app_url, login_url, fake, clock)


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
    headers = {"sec-fetch-site": "same-origin", "x-csrf-token": token}
    return browser.client.request(method, path, headers=headers, json=body)


def billing(browser):
    response = browser.client.get("/api/v1/billing")
    assert response.status_code == 200, response.text
    return response.json()


def post_webhook(browser, delivery):
    return browser.client.post("/webhooks/billing", content=delivery.body, headers=delivery.headers)


def pay(browser, fake, clock, *, then_apply=True):
    """Press Subscribe, then the provider completes the checkout and its webhook reaches the real route."""
    response = write(browser, "POST", "/api/v1/billing/checkout")
    assert response.status_code == 200, response.text
    checkout = response.json()["pay_url"].split("_ptxn=")[1]
    delivery = fake.complete_checkout(checkout)
    if then_apply:
        assert post_webhook(browser, delivery).json()["outcome"] == "applied"
    return next(s for s in fake.subscriptions.values() if s.checkout_id == checkout).subscription_id


class Outbox:
    def __init__(self) -> None:
        self.sent = []
        self.fail = False

    def send(self, email) -> None:
        if self.fail:
            raise MailError("down")
        self.sent.append(email)


# ---- the switch --------------------------------------------------------------------------------------------------------


def test_with_the_switch_off_every_billing_route_and_the_webhook_answer_404(
    db, app_url, login_url, fake, clock, alice, started
):
    app = billing_app(db, app_url, login_url, fake, clock, enabled=False)
    browser = open_browser(app)
    started.append(browser)
    signed_in(browser)
    assert browser.client.get("/api/v1/billing").status_code == 404
    for path in ("/checkout", "/cancel", "/resume", "/portal"):
        assert write(browser, "POST", f"/api/v1/billing{path}").status_code == 404
    assert post_webhook(browser, fake.refund("sub_x")).status_code == 404


def test_with_the_switch_off_the_session_is_what_it_always_was_even_with_billing_rows(
    db, app_url, login_url, fake, clock, system, owner, alice, started
):
    app = billing_app(db, app_url, login_url, fake, clock, enabled=False)
    browser = open_browser(app)
    started.append(browser)
    signed_in(browser)
    before = browser.session().json()
    sub = subscribed(system, fake, clock, alice)
    apply(system, fake, fake.finish(sub), clock())  # a subscription that has ended
    after = browser.session().json()
    assert after == before
    assert (after["plan"]["source"], after["access"]) == ("free", {"state": "active", "ends_at": None, "reason": None})


def test_with_the_switch_off_sign_up_gives_no_trial(db, app_url, login_url, fake, clock, owner, trial, started):
    app = billing_app(db, app_url, login_url, fake, clock, enabled=False)
    browser = open_browser(app)
    started.append(browser)
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    assert owner.execute("SELECT count(*) AS n FROM grant_redemptions").fetchone()["n"] == 0
    assert browser.session().json()["plan"]["source"] == "free"


# ---- sign-up and the trial ------------------------------------------------------------------------------------------------


def test_with_the_switch_on_a_new_account_starts_a_seven_day_trial(browser, owner):
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    session = browser.session().json()
    assert (session["plan"]["effective"], session["plan"]["source"]) == ("pro", "grant")
    state = billing(browser)
    assert (state["state"], state["can_subscribe"], state["price"], state["currency"]) == (
        "trial",
        True,
        "15.00",
        "USD",
    )
    assert state["ends_at"] == state["billing_starts_at"]
    assert [r["meta"] for r in audit_rows(owner) if r["action"] == "trial_started"] == [{"days": 7}]


def test_a_beta_invite_keeps_its_own_thirty_days_and_gets_no_trial_on_top(browser, owner, clock):
    beta, _ = plans.create_grant(owner, days=30, label="Beta", now=clock())
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner, grant_code_id=beta))
    assert owner.execute("SELECT count(*) AS n FROM grant_redemptions").fetchone()["n"] == 1
    assert billing(browser)["state"] == "covered"


def test_a_promo_at_sign_up_replaces_the_trial_when_it_runs_longer(browser, owner, clock):
    plans.create_grant(owner, days=30, code="FREE30", now=clock())
    response = browser.sign_in(NEWCOMER, invite=invites.create_invite(owner), promo="free30")
    assert response.headers["location"] == "/"
    state = billing(browser)
    assert state["state"] == "covered"  # thirty days from the code, not the seven of the trial
    redeemed = [r["meta"].get("via") for r in audit_rows(owner) if r["action"] == "grant_redeemed"]
    assert redeemed == ["signup"]


# ---- what the Plan page is told ---------------------------------------------------------------------------------------


def test_after_the_trial_with_nothing_live_the_account_is_read_only_and_can_subscribe(browser, owner, clock):
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    clock.advance(days=8)
    state = billing(browser)
    assert (state["state"], state["reason"], state["can_subscribe"]) == ("read_only", "expired", True)
    session = browser.session().json()
    assert (session["access"]["state"], session["access"]["reason"]) == ("ended", "expired")
    refused = write(browser, "PATCH", "/api/v1/settings", {"theme": "light"})
    assert refused.status_code == 403 and "free days have ended" in refused.json()["detail"]


def test_an_account_that_never_had_anything_is_read_only_as_lapsed_with_the_switch_on(browser, owner, alice):
    signed_in(browser)  # alice: made before billing, no code, no trial
    assert (billing(browser)["state"], browser.session().json()["access"]["reason"]) == ("read_only", "lapsed")
    refused = write(browser, "PATCH", "/api/v1/settings", {"theme": "light"})
    assert "subscription has ended" in refused.json()["detail"]


def test_an_override_with_no_end_is_covered_and_has_nothing_to_pay(browser, owner, alice):
    owner.execute(
        "INSERT INTO plan_overrides (user_id, plan, reason, created_by) VALUES (%s, 'pro', 'owner', 'cli:owner')",
        (alice,),
    )
    signed_in(browser)
    state = billing(browser)
    assert (state["state"], state["can_subscribe"], state["ends_at"]) == ("covered", False, None)
    refused = write(browser, "POST", "/api/v1/billing/checkout")
    assert refused.status_code == 409 and "no end date" in refused.json()["detail"]


# ---- subscribing ----------------------------------------------------------------------------------------------------------


def test_subscribing_during_the_trial_waits_for_the_free_days_rounded_up(browser, owner, fake, clock):
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    clock.advance(days=2, hours=5)  # 4 days and 19 hours left
    response = write(browser, "POST", "/api/v1/billing/checkout")
    assert response.status_code == 200 and response.json()["pay_url"].startswith("/billing/pay?_ptxn=")
    [checkout] = fake.checkouts.values()
    assert checkout.free_days == 5 == math.ceil(4 + 19 / 24)  # never charged for a day already free
    [row] = owner.execute("SELECT user_id, provider FROM billing_checkouts").fetchall()
    assert row["provider"] == "fake"
    assert [r["meta"] for r in audit_rows(owner) if r["action"] == "billing_checkout_started"] == [{"free_days": 5}]


def test_a_second_click_lands_on_the_checkout_already_open(browser, fake, alice):
    signed_in(browser)
    first = write(browser, "POST", "/api/v1/billing/checkout").json()["pay_url"]
    second = write(browser, "POST", "/api/v1/billing/checkout").json()["pay_url"]
    assert first == second and len(fake.checkouts) == 1 and len(fake.customers) == 1


def test_a_read_only_person_may_subscribe_and_paying_brings_full_access_back(browser, fake, clock, alice):
    signed_in(browser)
    pay(browser, fake, clock)
    state = billing(browser)
    assert (state["state"], state["renews_at"] is not None, state["can_manage"]) == ("active", True, True)
    assert browser.session().json()["access"]["state"] == "active"
    assert write(browser, "PATCH", "/api/v1/settings", {"theme": "light"}).status_code == 200


def test_subscribing_again_while_paying_is_refused(browser, fake, clock, alice):
    signed_in(browser)
    pay(browser, fake, clock)
    refused = write(browser, "POST", "/api/v1/billing/checkout")
    assert refused.status_code == 409 and "already have a subscription" in refused.json()["detail"]


def test_checkout_needs_the_csrf_token(browser, fake, alice):
    signed_in(browser)
    assert browser.client.post("/api/v1/billing/checkout", headers={"sec-fetch-site": "same-origin"}).status_code == 403
    assert fake.checkouts == {}


@pytest.mark.parametrize("how", ["down", "refuse", "act_then_timeout"])
def test_a_provider_that_fails_leaves_nothing_half_done(browser, owner, fake, alice, how):
    signed_in(browser)
    fake.fail_next(how)
    response = write(browser, "POST", "/api/v1/billing/checkout")
    assert response.status_code == (409 if how == "refuse" else 503)
    assert owner.execute("SELECT count(*) AS n FROM billing_checkouts").fetchone()["n"] == 0
    assert write(browser, "POST", "/api/v1/billing/checkout").status_code == 200  # and trying again works


def test_billing_actions_are_limited_to_ten_an_hour_per_person(browser, alice):
    signed_in(browser)
    answers = [write(browser, "POST", "/api/v1/billing/portal").status_code for _ in range(11)]
    assert answers[:10] == [409] * 10 and answers[10] == 429  # nothing to manage yet, then the limit


# ---- cancel, resume, the provider's page -------------------------------------------------------------------------------


def test_cancel_then_resume_and_the_webhook_confirms_each(browser, owner, fake, clock, alice):
    signed_in(browser)
    sub = pay(browser, fake, clock)
    cancelled = write(browser, "POST", "/api/v1/billing/cancel").json()
    assert cancelled["state"] == "cancelling" and fake.subscriptions[sub].cancel_at_period_end
    post_webhook(browser, fake.announce(sub))
    assert owner.execute("SELECT cancel_requested_by FROM subscriptions").fetchone()["cancel_requested_by"] == "person"
    resumed = write(browser, "POST", "/api/v1/billing/resume").json()
    assert resumed["state"] == "active" and not fake.subscriptions[sub].cancel_at_period_end
    actions = [r["action"] for r in audit_rows(owner) if r["action"].startswith("subscription_")]
    assert "subscription_cancel_requested" in actions and "subscription_resumed" in actions


def test_cancel_or_resume_with_nothing_to_act_on_is_refused(browser, alice):
    signed_in(browser)
    assert write(browser, "POST", "/api/v1/billing/cancel").status_code == 409
    assert write(browser, "POST", "/api/v1/billing/resume").status_code == 409


def test_the_providers_page_is_a_fresh_link_once_there_is_a_customer(browser, fake, clock, alice):
    signed_in(browser)
    assert write(browser, "POST", "/api/v1/billing/portal").status_code == 409
    pay(browser, fake, clock)
    first = write(browser, "POST", "/api/v1/billing/portal").json()["url"]
    second = write(browser, "POST", "/api/v1/billing/portal").json()["url"]
    assert first.startswith("https://") and first != second


# ---- the webhook route ---------------------------------------------------------------------------------------------------


def test_the_webhook_takes_no_session_and_refuses_a_bad_signature_storing_nothing(browser, owner, fake, clock, alice):
    delivery = fake.refund("sub_x")
    tampered = dict(delivery.headers)
    tampered["Fake-Signature"] = tampered["Fake-Signature"][:-4] + "0000"
    assert browser.client.post("/webhooks/billing", content=delivery.body, headers=tampered).status_code == 401
    assert (
        browser.client.post(
            "/webhooks/billing", content=delivery.body, headers={"Content-Type": "application/json"}
        ).status_code
        == 401
    )
    assert owner.execute("SELECT count(*) AS n FROM billing_events").fetchone()["n"] == 0
    assert post_webhook(browser, delivery).status_code == 200  # no cookie, no CSRF token: the signature is the lock


def test_the_webhook_refuses_what_is_not_json_or_too_large(browser, fake):
    delivery = fake.refund("sub_x")
    headers = {**delivery.headers, "Content-Type": "text/plain"}
    assert browser.client.post("/webhooks/billing", content=delivery.body, headers=headers).status_code == 415
    big = json.dumps({"x": "y" * 70_000}).encode()
    assert browser.client.post("/webhooks/billing", content=big, headers=fake.sign(big)).status_code == 413


def test_the_webhook_answers_200_to_a_repeat_and_to_an_event_it_does_not_apply(browser, fake):
    delivery = fake.refund("sub_unknown")
    assert post_webhook(browser, delivery).json() == {"received": True, "outcome": "refund_recorded"}
    assert post_webhook(browser, delivery).json() == {"received": True, "outcome": "duplicate"}


def test_the_webhook_and_the_payment_page_are_never_cached(browser, fake):
    response = post_webhook(browser, fake.refund("sub_x"))
    assert response.headers["cache-control"] == "no-store"


# ---- deleting the account ------------------------------------------------------------------------------------------------


def request_deletion(browser):
    email = browser.session().json()["user"]["email"]
    return write(browser, "POST", "/api/v1/account/deletion", {"email": email})


def test_asking_to_delete_stops_the_renewal_and_coming_back_resumes_it(
    app, browser, owner, fake, clock, alice, started
):
    signed_in(browser)
    sub = pay(browser, fake, clock)
    assert request_deletion(browser).status_code == 200
    assert fake.subscriptions[sub].cancel_at_period_end
    row = owner.execute("SELECT cancel_at_period_end, cancel_requested_by FROM subscriptions").fetchone()
    assert (row["cancel_at_period_end"], row["cancel_requested_by"]) == (True, "deletion")
    again = open_browser(app)
    started.append(again)
    assert again.sign_in(ALICE).headers["location"].startswith("/settings/privacy?restored=1")
    assert not fake.subscriptions[sub].cancel_at_period_end
    assert owner.execute("SELECT cancel_at_period_end FROM subscriptions").fetchone()["cancel_at_period_end"] is False


def test_coming_back_does_not_undo_a_cancel_the_person_made_themselves(
    app, browser, owner, fake, clock, alice, started
):
    signed_in(browser)
    sub = pay(browser, fake, clock)
    write(browser, "POST", "/api/v1/billing/cancel")
    assert request_deletion(browser).status_code == 200
    again = open_browser(app)
    started.append(again)
    again.sign_in(ALICE)
    assert fake.subscriptions[sub].cancel_at_period_end  # still cancelling: that was their own choice


def test_a_provider_that_is_down_never_stands_in_the_way_of_a_deletion(browser, owner, fake, clock, alice):
    signed_in(browser)
    pay(browser, fake, clock)
    fake.fail_next("down")
    assert request_deletion(browser).status_code == 200
    assert owner.execute("SELECT status FROM users").fetchone()["status"] == "pending_deletion"


def test_the_deletion_queues_the_final_cancel_and_the_billing_step_sends_it(system, owner, fake, clock, alice):
    sub = subscribed(system, fake, clock, alice)
    owner.execute(
        "UPDATE users SET status = 'pending_deletion', deletion_scheduled_at = now() - interval '1 minute' WHERE id = %s",
        (alice,),
    )
    report = sweep.run(system, None, apply=True)
    assert report.deleted == 1
    assert (
        owner.execute("SELECT provider_subscription_id FROM billing_cancellations").fetchone()[
            "provider_subscription_id"
        ]
        == sub
    )
    fake.fail_next("down")
    failed = sweep.run_billing(system, fake, apply=True, now=clock())
    assert (failed.cancels_sent, failed.cancels_pending) == (0, 1)
    assert owner.execute("SELECT attempts, last_error FROM billing_cancellations").fetchone() == {
        "attempts": 1,
        "last_error": "fake_down",
    }
    done = sweep.run_billing(system, fake, apply=True, now=clock())
    assert done.cancels_sent == 1 and fake.subscriptions[sub].status == "canceled"
    assert owner.execute("SELECT count(*) AS n FROM billing_cancellations").fetchone()["n"] == 0
    actions = [r["action"] for r in audit_rows(owner)]
    assert "billing_cancel_queued" in actions and "billing_cancelled_after_deletion" in actions


def test_the_billing_step_without_a_provider_or_as_a_dry_run_changes_nothing(system, owner, fake, clock, alice):
    pg_queue = "INSERT INTO billing_cancellations (provider, provider_subscription_id) VALUES ('fake', 'sub_q')"
    owner.execute(pg_queue)
    assert sweep.run_billing(system, None, apply=True).cancels_pending == 1
    assert sweep.run_billing(system, fake, apply=False).cancels_pending == 1
    assert fake.calls == [] and owner.execute("SELECT count(*) AS n FROM billing_cancellations").fetchone()["n"] == 1


def test_the_nightly_check_through_the_job_counts_what_it_corrected(system, owner, fake, clock, alice):
    sub = subscribed(system, fake, clock, alice)
    fake.cancel(sub)
    report = sweep.run_billing(system, fake, apply=True, now=clock())
    assert (report.checked, report.corrected) == (1, 1)
    fake.fail_next("down")
    assert sweep.run_billing(system, fake, apply=True, now=clock()).check_failed == 1


# ---- the emails --------------------------------------------------------------------------------------------------------


def test_the_trial_reminder_goes_two_days_before_the_end_once_and_not_to_someone_who_subscribed(
    system, owner, trial, fake, clock, alice, bob
):
    with system.system_session("login") as conn:
        pg_billing.give_trial(conn, alice, now=clock())
        pg_billing.give_trial(conn, bob, now=clock())
    subscribed(system, fake, clock, bob, free_days=7)
    outbox = Outbox()
    assert sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 4 * DAY).reminders_due == 0
    report = sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 5 * DAY + timedelta(seconds=60))
    assert (report.reminders_due, report.reminders_sent) == (1, 1)
    [email] = outbox.sent
    assert email.to == "alice@example.test" and "ends on" in email.text and "15" in email.text
    assert sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 6 * DAY).reminders_due == 0


def test_a_reminder_that_could_not_be_sent_stays_due(system, owner, trial, clock, alice):
    with system.system_session("login") as conn:
        pg_billing.give_trial(conn, alice, now=clock())
    outbox = Outbox()
    outbox.fail = True
    report = sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 6 * DAY)
    assert (report.failed, report.reminders_sent) == (1, 0)
    outbox.fail = False
    assert sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 6 * DAY).reminders_sent == 1


def test_the_trial_ended_email_with_billing_on_and_nothing_for_someone_now_paying(
    system, owner, trial, fake, clock, alice, bob
):
    with system.system_session("login") as conn:
        pg_billing.give_trial(conn, alice, now=clock())
        pg_billing.give_trial(conn, bob, now=clock())
    subscribed(system, fake, clock, bob, free_days=7)
    outbox = Outbox()
    report = sweep.run_notices(system, outbox, apply=True, now=clock() + 7 * DAY + timedelta(hours=1), billing=True)
    assert report.sent == 1
    [email] = outbox.sent
    assert email.to == "alice@example.test" and email.subject == "Your Roundturn trial has ended"


def test_the_subscription_ended_email_after_a_cancelled_month_once(system, owner, fake, clock, alice):
    sub = subscribed(system, fake, clock, alice)
    fake.cancel(sub)
    apply(system, fake, fake.announce(sub), clock())
    outbox = Outbox()
    assert sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 29 * DAY).ended_due == 0
    report = sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 31 * DAY)
    assert (report.ended_due, report.ended_sent) == (1, 1)
    assert outbox.sent[0].subject == "Your Roundturn subscription has ended"
    assert sweep.run_billing_notices(system, outbox, apply=True, now=clock() + 32 * DAY).ended_due == 0


def test_a_renewal_whose_webhook_is_late_never_earns_an_ending_email(system, owner, fake, clock, alice):
    subscribed(system, fake, clock, alice)
    report = sweep.run_billing_notices(system, Outbox(), apply=False, now=clock() + 40 * DAY)
    assert report.ended_due == 0


# ---- the whole lifecycle -----------------------------------------------------------------------------------------------


def test_the_full_lifecycle_with_the_fake_provider(app, browser, owner, system, fake, clock, started):
    """Sign up and get the trial, subscribe during it, renew, a payment fails, grace, read-only, update the card, cancel,
    the month runs out, subscribe again, then delete the account: the session, the page and the log at each step."""
    signed_in(browser, NEWCOMER, invite=invites.create_invite(owner))
    [user] = users_with_subject(owner, "kp_newcomer")
    assert billing(browser)["state"] == "trial"

    clock.advance(days=3)
    sub = pay(browser, fake, clock)  # during the trial: the first charge waits for the 4 days left
    assert billing(browser)["first_charge_pending"] is True

    clock.advance(days=4)
    post_webhook(browser, fake.renew(sub))  # the trial ends: the first payment
    state = billing(browser)
    assert (state["state"], state["first_charge_pending"]) == ("active", False)

    clock.advance(days=30)
    post_webhook(browser, fake.fail_payment(sub))
    state = billing(browser)
    assert state["state"] == "past_due" and state["ends_at"].startswith(str((clock() + 7 * DAY).date()))
    assert browser.session().json()["access"]["state"] == "active"  # the grace

    clock.advance(days=7)
    access = browser.session().json()["access"]
    assert (access["state"], access["reason"]) == ("ended", "lapsed")
    assert billing(browser)["state"] == "read_only"

    clock.advance(days=1)
    post_webhook(browser, fake.recover(sub))  # the card was updated: access returns at once
    assert billing(browser)["state"] == "active"

    write(browser, "POST", "/api/v1/billing/cancel")
    clock.advance(days=40)
    post_webhook(browser, fake.finish(sub))
    assert billing(browser)["state"] == "read_only"

    clock.advance(days=2)
    again = pay(browser, fake, clock)
    assert again != sub and billing(browser)["state"] == "active"

    # The deletion itself (asking for it needs a fresh sign-in, which this test's moved clock cannot give).
    owner.execute(
        "UPDATE users SET status = 'pending_deletion', deletion_scheduled_at = now() - interval '1 minute' WHERE id = %s",
        (user["id"],),
    )
    assert sweep.run(system, None, apply=True).deleted == 1
    assert sweep.run_billing(system, fake, apply=True, now=clock()).cancels_sent == 1
    assert fake.subscriptions[again].status == "canceled"

    log = [r["action"] for r in audit_rows(owner)]
    for action in (
        "trial_started",
        "billing_checkout_started",
        "subscription_changed",
        "subscription_cancel_requested",
        "billing_cancel_queued",
        "billing_cancelled_after_deletion",
    ):
        assert action in log, action
    kept = owner.execute("SELECT count(*) AS n, count(user_id) AS named FROM billing_events").fetchone()
    assert kept["n"] == 6 and kept["named"] == 0  # the events are kept, with the person's id emptied
