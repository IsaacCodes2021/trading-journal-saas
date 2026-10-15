"""Which plan a person is on: the rules of doc 03 section 5, one at a time, on plain values."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tradejournal.entitlements import (
    FREE,
    LIMITS,
    PLANS,
    EffectivePlan,
    Grant,
    Override,
    SubscriptionState,
    effective_plan,
    limits_for,
    subscription_lapsed_at,
    subscription_live_until,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def grant(plan="pro", start=-1, end=30):
    return Grant(plan, NOW + start * DAY, NOW + end * DAY)


def sub(plan="pro", status="active", end=20, cancelling=False, past_due_since=None, ended_at=None):
    return SubscriptionState(
        plan,
        status,
        None if end is None else NOW + end * DAY,
        cancel_at_period_end=cancelling,
        past_due_since=past_due_since,
        ended_at=ended_at,
    )


def test_nobody_with_nothing_is_on_the_free_plan():
    assert effective_plan(NOW) == FREE == EffectivePlan("free", "free", None)


class TestGrants:
    def test_an_active_grant_gives_its_plan_until_it_ends(self):
        assert effective_plan(NOW, grants=[grant()]) == EffectivePlan("pro", "grant", NOW + 30 * DAY)

    def test_a_grant_has_not_started_before_its_start_and_has_ended_at_its_end(self):
        assert effective_plan(NOW, grants=[grant(start=1, end=31)]) == FREE
        assert effective_plan(NOW, grants=[Grant("pro", NOW - DAY, NOW)]) == FREE  # ends exactly now: over
        assert effective_plan(NOW, grants=[Grant("pro", NOW, NOW + DAY)]).source == "grant"  # starts exactly now: on

    def test_of_several_the_one_that_ends_last_is_shown(self):
        later = grant(end=60)
        assert effective_plan(NOW, grants=[grant(end=10), later, grant(end=20)]).expires_at == later.ends_at

    def test_an_expired_one_among_active_ones_is_ignored(self):
        assert effective_plan(NOW, grants=[grant(start=-90, end=-60), grant(end=5)]).expires_at == NOW + 5 * DAY

    def test_a_grant_for_a_plan_this_version_does_not_know_gives_nothing(self):
        assert effective_plan(NOW, grants=[Grant("platinum", NOW - DAY, NOW + DAY)]) == FREE  # type: ignore[arg-type]


class TestSubscriptions:
    @pytest.mark.parametrize("status", ["active", "trialing"])
    def test_a_renewing_subscription_gives_its_plan_until_a_day_after_the_period_ends(self, status):
        assert effective_plan(NOW, subscription=sub(status=status)) == EffectivePlan(
            "pro", "subscription", NOW + 21 * DAY
        )

    @pytest.mark.parametrize("status", ["canceled", "paused", "grace", "unpaid", "incomplete", "", "ACTIVE"])
    def test_any_other_state_gives_nothing(self, status):
        assert effective_plan(NOW, subscription=sub(status=status)) == FREE

    def test_a_renewal_whose_webhook_is_late_keeps_access_for_one_day_and_not_a_second_longer(self):
        assert effective_plan(NOW, subscription=sub(end=0)).source == "subscription"  # the period ended just now
        assert effective_plan(NOW, subscription=sub(end=-1)) == FREE  # a whole day past it: over
        just_inside = SubscriptionState("pro", "active", NOW - DAY + timedelta(microseconds=1))
        assert effective_plan(NOW, subscription=just_inside).expires_at == NOW + timedelta(microseconds=1)

    def test_a_cancelling_subscription_ends_exactly_at_the_end_of_the_paid_period(self):
        assert effective_plan(NOW, subscription=sub(end=3, cancelling=True)) == EffectivePlan(
            "pro", "subscription", NOW + 3 * DAY
        )
        assert effective_plan(NOW, subscription=sub(end=0, cancelling=True)) == FREE  # no margin once cancelling

    def test_a_failed_payment_gives_seven_days_from_the_first_failure_whatever_the_period_says(self):
        failed = sub(status="past_due", end=30, past_due_since=NOW - 6 * DAY)  # the provider moved the period on
        assert effective_plan(NOW, subscription=failed) == EffectivePlan("pro", "subscription", NOW + DAY)
        assert effective_plan(NOW + DAY, subscription=failed) == FREE  # the last second of grace is over at 7 days
        assert effective_plan(NOW + DAY - timedelta(microseconds=1), subscription=failed).source == "subscription"

    def test_a_failed_payment_with_no_first_failure_recorded_counts_from_the_period_end(self):
        assert effective_plan(NOW, subscription=sub(status="past_due", end=-6)).expires_at == NOW + DAY
        assert effective_plan(NOW, subscription=sub(status="past_due", end=None)) == FREE

    def test_one_with_no_period_end_known_yet_is_open(self):
        assert effective_plan(NOW, subscription=sub(end=None)) == EffectivePlan("pro", "subscription", None)


class TestWhenASubscriptionStopped:
    def test_a_live_one_has_not_lapsed(self):
        assert subscription_lapsed_at(sub(), NOW) is None
        assert subscription_lapsed_at(None, NOW) is None
        assert subscription_live_until(sub()) == (True, NOW + 21 * DAY)

    def test_a_cancelled_month_lapses_at_the_end_of_the_period(self):
        assert subscription_lapsed_at(sub(end=-2, cancelling=True), NOW) == NOW - 2 * DAY

    def test_a_grace_that_ran_out_lapses_seven_days_after_the_first_failure(self):
        assert (
            subscription_lapsed_at(sub(status="past_due", end=20, past_due_since=NOW - 9 * DAY), NOW) == NOW - 2 * DAY
        )

    def test_a_canceled_one_lapses_when_it_was_ended_or_else_at_its_period_end(self):
        assert subscription_lapsed_at(sub(status="canceled", end=5, ended_at=NOW - DAY), NOW) == NOW - DAY
        assert subscription_lapsed_at(sub(status="canceled", end=-3), NOW) == NOW - 3 * DAY
        assert subscription_live_until(sub(status="paused")) == (False, None)


class TestGrantAgainstSubscription:
    def test_the_higher_plan_wins(self):
        assert effective_plan(NOW, grants=[grant("pro")], subscription=sub("free")).source == "grant"
        assert effective_plan(NOW, grants=[grant("free")], subscription=sub("pro")).source == "subscription"

    def test_on_a_tie_the_subscription_is_the_source_because_it_is_the_one_that_renews(self):
        result = effective_plan(NOW, grants=[grant(end=90)], subscription=sub(end=20))
        assert result == EffectivePlan("pro", "subscription", NOW + 21 * DAY)  # renewing: a day past the period

    def test_a_lapsed_subscription_falls_back_to_the_grant(self):
        assert effective_plan(NOW, grants=[grant()], subscription=sub(status="canceled")).source == "grant"

    def test_a_lapsed_grant_falls_back_to_the_subscription(self):
        assert effective_plan(NOW, grants=[grant(start=-90, end=-1)], subscription=sub()).source == "subscription"


class TestOverrides:
    def test_an_override_wins_outright_even_when_it_lowers_the_plan(self):
        result = effective_plan(NOW, override=Override("free", None), grants=[grant()], subscription=sub())
        assert result == EffectivePlan("free", "override", None)

    def test_an_override_can_raise_the_plan(self):
        assert effective_plan(NOW, override=Override("pro", NOW + 5 * DAY)) == EffectivePlan(
            "pro", "override", NOW + 5 * DAY
        )

    def test_an_expired_override_is_ignored_and_the_next_source_applies(self):
        assert effective_plan(NOW, override=Override("free", NOW - DAY), grants=[grant()]).source == "grant"
        assert (
            effective_plan(NOW, override=Override("free", NOW), grants=[grant()]).source == "grant"
        )  # ends exactly now

    def test_a_revoked_override_is_ignored(self):
        assert effective_plan(NOW, override=Override("free", None, revoked=True), grants=[grant()]).source == "grant"

    def test_a_revoked_override_with_nothing_else_is_the_free_plan(self):
        assert effective_plan(NOW, override=Override("pro", None, revoked=True)) == FREE


class TestWhatAPlanIncludes:
    def test_every_plan_has_an_entry_and_nothing_is_limited_until_that_is_decided(self):
        assert set(LIMITS) == set(PLANS)
        assert all(limits_for(plan) == {} for plan in PLANS)

    def test_a_plan_this_version_does_not_know_gets_the_free_limits(self):
        assert limits_for("platinum") == limits_for("free")

    def test_what_is_returned_is_a_copy(self):
        limits_for("free")["max_active_accounts"] = 5
        assert LIMITS["free"] == {}
