"""Billing on a real Postgres (migration 0021; the billing design, slices 1 to 4 and 7): the tables and who may touch them,
the trial, the plan rule reading a subscription, and every way a webhook event is stored and applied, with the fake provider."""

from __future__ import annotations

import json
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from billing_world import DAY, Clock, apply, checkout_for, make_fake, subscribed

from tradejournal.billing.types import ProviderSubscription
from tradejournal.entitlements import SubscriptionState, subscription_live_until
from tradejournal.pg import billing as pg_billing
from tradejournal.pg import plans


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake(clock):
    return make_fake(clock)


def sub_row(owner, user):
    return owner.execute("SELECT * FROM subscriptions WHERE user_id = %s", (user,)).fetchone()


def events(owner):
    return owner.execute("SELECT * FROM billing_events ORDER BY id").fetchall()


def actions(owner, prefix=""):
    return [
        (r["action"], r["meta"])
        for r in owner.execute("SELECT action, meta FROM audit_log ORDER BY id").fetchall()
        if r["action"].startswith(prefix)
    ]


# ---- the tables and who may touch them ----------------------------------------------------------------------------------

BILLING_TABLES = ["subscriptions", "billing_customers", "billing_checkouts", "billing_events", "billing_cancellations"]


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_the_request_role_has_no_grant_on_any_billing_table(owner, table):
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert not owner.execute("SELECT has_table_privilege('app_rw', %s, %s) AS ok", (table, privilege)).fetchone()[
            "ok"
        ], (table, privilege)


@pytest.mark.parametrize("table", BILLING_TABLES)
def test_billing_tables_are_account_tables_without_row_level_security(owner, table):
    row = owner.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (table,)).fetchone()
    assert row["relrowsecurity"] is False


def test_the_operator_reads_billing_state_but_never_an_events_payload(system, owner, alice):
    with system.system_session("admin") as conn:
        conn.execute("SELECT * FROM subscriptions")
        conn.execute("SELECT id, type, outcome, user_id, received_at FROM billing_events")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("admin") as conn:
        conn.execute("SELECT payload FROM billing_events")
    for table in ("subscriptions", "billing_events", "billing_customers", "billing_cancellations"):
        for privilege in ("INSERT", "UPDATE", "DELETE"):
            assert not owner.execute(
                "SELECT has_table_privilege('app_admin', %s, %s) AS ok", (table, privilege)
            ).fetchone()["ok"], (table, privilege)


def test_the_sign_in_role_cannot_write_a_status_or_a_date_only_the_cancel_flag(owner):
    def can(column, privilege="UPDATE"):
        return owner.execute(
            "SELECT has_column_privilege('app_login', 'subscriptions', %s, %s) AS ok", (column, privilege)
        ).fetchone()["ok"]

    assert can("cancel_at_period_end") and can("cancel_requested_by")
    for column in ("status", "current_period_end", "past_due_since", "provider_subscription_id", "user_id"):
        assert not can(column), column
    assert not owner.execute("SELECT has_table_privilege('app_login', 'subscriptions', 'INSERT') AS ok").fetchone()[
        "ok"
    ]
    assert not owner.execute("SELECT has_table_privilege('app_login', 'billing_events', 'SELECT') AS ok").fetchone()[
        "ok"
    ]


def test_the_webhook_role_reaches_billing_and_three_columns_of_users_and_nothing_else(system, owner, alice):
    with system.system_session("webhook") as conn:
        conn.execute("SELECT id, email, status FROM users")
    for statement in (
        "SELECT idp_subject FROM users",
        "SELECT * FROM grant_redemptions",
        "SELECT * FROM plan_overrides",
        "SELECT * FROM trades",
        "SELECT * FROM sessions",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), system.system_session("webhook") as conn:
            conn.execute(statement)


def test_the_sweep_can_correct_a_status_but_not_reassign_a_subscription(owner):
    def can(column):
        return owner.execute(
            "SELECT has_column_privilege('app_sweep', 'subscriptions', %s, 'UPDATE') AS ok", (column,)
        ).fetchone()["ok"]

    assert can("status") and can("current_period_end")
    assert not can("user_id") and not can("provider_subscription_id") and not can("provider")


def test_a_deleted_account_takes_its_subscription_and_leaves_its_events_without_a_name(
    system, owner, clock, fake, alice
):
    subscribed(system, fake, clock, alice)
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    assert owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0
    assert owner.execute("SELECT count(*) AS n FROM billing_checkouts").fetchone()["n"] == 0
    [event] = events(owner)
    assert event["user_id"] is None and event["outcome"] == "applied"  # kept for tax, detached from the person


# ---- when a subscription is live: one rule, in SQL and in Python ----------------------------------------------------------

CASES = [
    ("active", 5, False, None),
    ("active", -0.5, False, None),
    ("active", 3, True, None),
    ("active", None, False, None),
    ("trialing", 2, False, None),
    ("trialing", 2, True, None),
    ("past_due", 20, False, -3),
    ("past_due", -2, False, None),
    ("past_due", None, False, None),
    ("paused", 5, False, None),
    ("canceled", 5, False, None),
]


@pytest.mark.parametrize(("status", "end", "cancelling", "failed"), CASES)
def test_billing_live_until_in_sql_says_what_entitlements_says(owner, clock, status, end, cancelling, failed):
    period_end = None if end is None else clock() + end * DAY
    past_due_since = None if failed is None else clock() + failed * DAY
    counts, until = subscription_live_until(
        SubscriptionState("pro", status, period_end, cancel_at_period_end=cancelling, past_due_since=past_due_since)
    )
    sql = owner.execute(
        """SELECT u IS NULL AS never, u = 'infinity'::timestamptz AS open, CASE WHEN isfinite(u) THEN u END AS at
           FROM billing_live_until(%s, %s, %s, %s) AS u""",
        (status, period_end, cancelling, past_due_since),
    ).fetchone()
    if not counts:
        assert sql["never"]
    elif until is None:
        assert sql["open"]  # live, with no end known yet
    else:
        assert sql["at"] == until


# ---- the trial ----------------------------------------------------------------------------------------------------------


def test_the_migration_makes_one_trial_code_of_seven_days_that_nobody_can_type(owner, trial, system, alice):
    row = owner.execute("SELECT * FROM grant_codes WHERE id = %s", (trial,)).fetchone()
    assert (row["kind"], row["days"], row["plan"], row["label"], row["created_by"], row["code"]) == (
        "trial",
        7,
        "pro",
        "7-day trial",
        "system:trial",
        None,
    )
    with pytest.raises(psycopg.errors.UniqueViolation):  # one trial code, ever
        owner.execute(
            "INSERT INTO grant_codes (code_hash, plan, days, kind) VALUES (sha256('x'::bytea), 'pro', 7, 'trial')"
        )


def test_a_trial_is_given_once_per_account_for_seven_days(system, owner, trial, clock, alice):
    with system.system_session("login") as conn:
        first = pg_billing.give_trial(conn, alice, now=clock())
        again = pg_billing.give_trial(conn, alice, now=clock() + 30 * DAY)
    assert first == plans.Redeemed("pro", 7, clock() + 7 * DAY) and again is None
    with system.system_session("login") as conn:
        plan = plans.effective(conn, alice, clock())
    assert (plan.source, plan.expires_at) == ("grant", clock() + 7 * DAY)


def test_no_trial_code_no_trial_and_no_error(system, clock, alice):
    with system.system_session("login") as conn:
        assert pg_billing.give_trial(conn, alice, now=clock()) is None


def test_the_trial_can_never_be_redeemed_by_typing_even_with_its_hash(system, owner, trial, clock, alice, monkeypatch):
    with system.system_session("login") as conn:
        monkeypatch.setattr(plans, "find_code", lambda *_: trial)  # as if someone had found the code
        assert plans.redeem(conn, alice, "ANYTHING", now=clock()) == plans.Refused()


def test_a_promo_code_is_not_blocked_by_a_running_trial_and_the_later_end_wins(system, owner, trial, clock, alice):
    plans.create_grant(owner, days=30, code="FREE30", now=clock())
    with system.system_session("login") as conn:
        pg_billing.give_trial(conn, alice, now=clock())
        outcome = plans.redeem(conn, alice, "FREE30", now=clock() + DAY)
        plan = plans.effective(conn, alice, clock() + 2 * DAY)
    assert isinstance(outcome, plans.Redeemed)
    assert plan.expires_at == clock() + 31 * DAY  # the promo runs longer, so it is the one that counts


def test_the_trial_is_not_listed_or_counted_among_the_operators_codes(service, owner, trial, op):
    assert service.list_grant_codes(limit=25, offset=0).total == 0
    assert service.overview().open_grant_codes == 0


# ---- the plan rule reads the subscription ---------------------------------------------------------------------------------


def test_a_paying_person_is_on_pro_from_their_subscription(system, clock, fake, alice):
    subscribed(system, fake, clock, alice)
    with system.system_session("login") as conn:
        plan = plans.effective(conn, alice, clock())
        access = plans.access(conn, alice, clock(), billing=True)
    assert (plan.plan, plan.source) == ("pro", "subscription") and access.state == "active"


def test_with_billing_on_a_lapsed_subscription_is_read_only_as_lapsed_and_with_it_off_nothing_changes(
    system, clock, fake, alice
):
    sub = subscribed(system, fake, clock, alice)
    fake.cancel(sub)
    apply(system, fake, fake.announce(sub), clock())
    later = clock() + 40 * DAY
    apply(system, fake, fake.finish(sub), clock())
    with system.system_session("login") as conn:
        on = plans.access(conn, alice, later, billing=True)
        off = plans.access(conn, alice, later)
    assert (on.state, on.reason) == ("ended", "lapsed")
    assert off.state == "active"  # the switch off is today's rule exactly: nobody who never had a code is affected


def test_the_operators_list_counts_a_paying_person_as_pro_and_a_revoked_beta_as_free(
    system, owner, clock, fake, service, op, alice, bob
):
    subscribed(system, fake, clock, alice)
    grant_id, _ = plans.create_grant(owner, days=30, now=clock())
    owner.execute(
        "INSERT INTO grant_redemptions (grant_code_id, user_id, plan, starts_at, ends_at, revoked_at) "
        "VALUES (%s, %s, 'pro', now(), now() + interval '20 days', now())",
        (grant_id, bob),
    )
    from tradejournal.pg.admin import plans_for

    with system.system_session("admin") as conn:
        found = plans_for(conn, [alice, bob], clock())
    assert (found[alice].source, found[bob].source) == ("subscription", "free")


# ---- the webhook: stored once, applied in order -------------------------------------------------------------------------


def test_a_checkout_that_completes_makes_the_subscription_and_says_so_in_the_log(system, owner, clock, fake, alice):
    checkout = checkout_for(system, fake, clock, alice)
    applied = apply(system, fake, fake.complete_checkout(checkout), clock())
    assert (applied.outcome, applied.user_id, applied.status_after) == ("applied", alice, "active")
    row = sub_row(owner, alice)
    assert (row["status"], row["provider"], row["cancel_at_period_end"]) == ("active", "fake", False)
    assert row["current_period_end"] == clock() + 30 * DAY
    assert owner.execute("SELECT completed_at FROM billing_checkouts").fetchone()["completed_at"] == clock()
    assert actions(owner, "subscription") == [
        ("subscription_changed", {"from": None, "to": "active", "cancelling": False})
    ]


def test_the_same_event_twice_does_nothing_the_second_time(system, owner, clock, fake, alice):
    delivery = fake.complete_checkout(checkout_for(system, fake, clock, alice))
    assert apply(system, fake, delivery, clock()).outcome == "applied"
    assert apply(system, fake, delivery, clock()).outcome == "duplicate"
    assert len(events(owner)) == 1 and len(actions(owner, "subscription")) == 1


def test_an_older_event_arriving_late_is_kept_as_stale_and_changes_nothing(system, owner, clock, fake, alice):
    sub = subscribed(system, fake, clock, alice)
    early = fake.announce(sub)  # made now...
    clock.advance(minutes=5)
    fake.cancel(sub)
    apply(system, fake, fake.announce(sub), clock())  # ...the newer one lands first
    assert apply(system, fake, early, clock()).outcome == "stale"
    assert sub_row(owner, alice)["cancel_at_period_end"] is True


def test_an_update_that_arrives_before_the_creation_still_finds_the_person_through_the_open_checkout(
    system, owner, clock, fake, alice
):
    checkout = checkout_for(system, fake, clock, alice)
    created = fake.complete_checkout(checkout)
    sub = next(iter(fake.subscriptions))
    clock.advance(seconds=1)
    updated = fake.announce(sub)  # carries no checkout id, only the custom data
    assert apply(system, fake, updated, clock()).outcome == "applied"
    assert apply(system, fake, created, clock()).outcome == "stale"  # older than what is now held
    assert sub_row(owner, alice)["provider_subscription_id"] == sub


def test_custom_data_that_names_someone_else_than_the_checkout_grants_nothing(system, owner, clock, fake, alice, bob):
    checkout = checkout_for(system, fake, clock, alice)
    applied = apply(system, fake, fake.complete_checkout(checkout, user_ref=str(bob)), clock())
    assert (applied.outcome, applied.alert) == ("alert", "mismatch")
    assert owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0
    assert events(owner)[0]["outcome"] == "alert"


def test_a_subscription_from_no_checkout_of_ours_grants_nothing_even_if_its_custom_data_names_someone(
    system, owner, clock, fake, alice
):
    """Someone could open a checkout of their own with any custom data in it: only a checkout the server opened counts."""
    customer = fake.find_or_create_customer(email="stranger@example.test", user_id=uuid4())
    checkout = fake.create_checkout(customer_id=customer, user_id=alice, free_days=0)  # never recorded by us
    applied = apply(system, fake, fake.complete_checkout(checkout.checkout_id), clock())
    assert applied.outcome == "alert" and owner.execute("SELECT count(*) AS n FROM subscriptions").fetchone()["n"] == 0


def test_an_event_for_an_account_deleted_since_is_kept_and_its_live_subscription_is_queued_to_end(
    system, owner, clock, fake, alice
):
    checkout = checkout_for(system, fake, clock, alice)
    delivery = fake.complete_checkout(checkout)
    owner.execute("DELETE FROM users WHERE id = %s", (alice,))
    assert apply(system, fake, delivery, clock()).outcome == "no_account"
    [queued] = owner.execute("SELECT * FROM billing_cancellations").fetchall()
    assert queued["provider_subscription_id"] == next(iter(fake.subscriptions))


def test_an_unknown_kind_of_event_is_kept_and_acknowledged(system, owner, clock, fake):
    body = json.dumps(
        {"event_id": "evt_x1", "event_type": "customer.updated", "occurred_at": clock().isoformat(), "data": {}}
    ).encode()

    class D:
        headers = fake.sign(body)

    D.body = body
    assert apply(system, fake, D, clock()).outcome == "unknown_type"
    assert events(owner)[0]["type"] == "customer.updated"


def test_a_refund_is_recorded_for_the_operator_and_changes_no_access(system, owner, clock, fake, alice):
    sub = subscribed(system, fake, clock, alice)
    applied = apply(system, fake, fake.refund(sub), clock())
    assert (applied.outcome, applied.user_id, applied.alert) == ("refund_recorded", alice, "refund")
    assert sub_row(owner, alice)["status"] == "active"
    assert ("billing_refund_recorded", {"action": "refund"}) in actions(owner, "billing")


def test_a_failed_payment_starts_seven_days_of_grace_from_the_first_failure_and_a_recovery_ends_it(
    system, owner, clock, fake, alice
):
    sub = subscribed(system, fake, clock, alice)
    clock.advance(days=30)
    apply(system, fake, fake.fail_payment(sub), clock())
    first_failure = clock()
    assert sub_row(owner, alice)["past_due_since"] == first_failure
    clock.advance(days=3)
    apply(system, fake, fake.fail_payment(sub), clock())  # a retry fails again: the grace still counts from the first
    assert sub_row(owner, alice)["past_due_since"] == first_failure
    with system.system_session("login") as conn:
        assert plans.access(conn, alice, first_failure + 7 * DAY - timedelta(seconds=1), billing=True).state == "active"
        assert plans.access(conn, alice, first_failure + 7 * DAY, billing=True).state == "ended"
    apply(system, fake, fake.recover(sub), clock())
    row = sub_row(owner, alice)
    assert (row["status"], row["past_due_since"]) == ("active", None)


def test_a_second_live_subscription_for_one_person_never_replaces_the_first(system, owner, clock, fake, alice):
    first = subscribed(system, fake, clock, alice)
    checkout = checkout_for(system, fake, clock, alice)
    applied = apply(system, fake, fake.complete_checkout(checkout), clock())
    assert (applied.outcome, applied.alert) == ("alert", "second_subscription")
    assert sub_row(owner, alice)["provider_subscription_id"] == first
    assert ("billing_alert", {"reason": "second_subscription"}) in actions(owner, "billing")


def test_resubscribing_after_an_end_replaces_the_row_and_late_news_of_the_old_one_is_stale(
    system, owner, clock, fake, alice
):
    old = subscribed(system, fake, clock, alice)
    old_news = fake.announce(old)
    clock.advance(minutes=1)
    apply(system, fake, fake.finish(old), clock())
    clock.advance(days=3)
    new = subscribed(system, fake, clock, alice)
    assert sub_row(owner, alice)["provider_subscription_id"] == new and new != old
    assert apply(system, fake, old_news, clock()).outcome == "stale"
    assert sub_row(owner, alice)["status"] == "active"


def test_a_cancel_made_outside_the_app_is_mirrored_and_marked_as_the_persons(system, owner, clock, fake, alice):
    sub = subscribed(system, fake, clock, alice)
    fake.cancel(sub)  # from the link in one of the provider's emails
    apply(system, fake, fake.announce(sub), clock())
    row = sub_row(owner, alice)
    assert (row["cancel_at_period_end"], row["cancel_requested_by"]) == (True, "person")


def test_no_event_ever_writes_a_plan_an_override_or_a_grant(system, owner, clock, fake, alice):
    before = [
        owner.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("plan_overrides", "grant_redemptions")
    ]  # noqa: S608
    sub = subscribed(system, fake, clock, alice)
    for delivery in (fake.fail_payment(sub), fake.recover(sub), fake.refund(sub), fake.finish(sub)):
        clock.advance(seconds=1)
        apply(system, fake, delivery, clock())
    after = [
        owner.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("plan_overrides", "grant_redemptions")
    ]  # noqa: S608
    assert before == after


def test_what_is_stored_holds_no_email_or_name(system, owner, clock, fake, alice):
    subscribed(system, fake, clock, alice)
    stored = json.dumps([r["payload"] for r in events(owner)])
    assert "@" not in stored and "email" not in stored


# ---- the nightly check ---------------------------------------------------------------------------------------------------


def test_the_nightly_check_corrects_a_missed_webhook_and_records_that_it_did(system, owner, clock, fake, alice):
    sub = subscribed(system, fake, clock, alice)
    fake.cancel(sub)  # the provider has it cancelling; its webhook never came
    answer = fake.fetch_subscription(sub)
    with system.system_session("sweep") as conn:
        assert pg_billing.reconcile(conn, "fake", answer, now=clock()) is True
    with system.system_session("sweep") as conn:
        assert pg_billing.reconcile(conn, "fake", answer, now=clock()) is False  # nothing left to correct
    assert sub_row(owner, alice)["cancel_at_period_end"] is True
    assert [e["outcome"] for e in events(owner)] == ["applied", "reconciled"]


def test_the_nightly_check_starts_grace_when_it_finds_a_failed_payment(system, owner, clock, fake, alice):
    sub = subscribed(system, fake, clock, alice)
    fake.fail_payment(sub)
    with system.system_session("sweep") as conn:
        pg_billing.reconcile(conn, "fake", fake.fetch_subscription(sub), now=clock())
    assert sub_row(owner, alice)["past_due_since"] == clock()


def test_the_check_ignores_a_subscription_it_does_not_hold(system, clock):
    stranger = ProviderSubscription("sub_unknown", None, "active", None)
    with system.system_session("sweep") as conn:
        assert pg_billing.reconcile(conn, "fake", stranger, now=clock()) is False


# ---- what the operator sees --------------------------------------------------------------------------------------------


def test_the_operator_sees_the_subscription_and_its_events_but_never_their_payload(
    system, service, owner, op, clock, fake, alice
):
    sub = subscribed(system, fake, clock, alice)
    apply(system, fake, fake.refund(sub), clock())
    detail = service.user_detail(op, alice)
    assert detail.subscription is not None
    assert (detail.subscription.status, detail.subscription.provider_subscription_id) == ("active", sub)
    assert [(e.type, e.outcome) for e in detail.billing_events] == [
        ("refund.created", "refund_recorded"),
        ("subscription.created", "applied"),
    ]
    assert "payload" not in detail.model_dump_json() and "1500" not in detail.model_dump_json()


def test_the_overview_counts_who_pays_and_who_is_on_a_trial(system, service, owner, trial, op, clock, fake, alice, bob):
    subscribed(system, fake, clock, alice)
    from conftest import OPERATOR_NOW  # the operator service's own clock

    with system.system_session("login") as conn:
        pg_billing.give_trial(conn, bob, now=OPERATOR_NOW - DAY)
    overview = service.overview()
    assert (overview.paying, overview.on_trial) == (1, 1)
