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
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def grant(plan="pro", start=-1, end=30):
    return Grant(plan, NOW + start * DAY, NOW + end * DAY)


def sub(plan="pro", status="active", end=20):
    return SubscriptionState(plan, status, None if end is None else NOW + end * DAY)


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
    @pytest.mark.parametrize("status", ["active", "trialing", "grace"])
    def test_a_live_subscription_gives_its_plan_until_the_period_ends(self, status):
        assert effective_plan(NOW, subscription=sub(status=status)) == EffectivePlan(
            "pro", "subscription", NOW + 20 * DAY
        )

    @pytest.mark.parametrize("status", ["canceled", "past_due", "unpaid", "incomplete", "paused", "", "ACTIVE"])
    def test_any_other_state_gives_nothing(self, status):
        assert effective_plan(NOW, subscription=sub(status=status)) == FREE

    def test_a_period_that_has_ended_gives_nothing_but_one_with_no_end_is_open(self):
        assert effective_plan(NOW, subscription=sub(end=-1)) == FREE
        assert effective_plan(NOW, subscription=sub(end=0)) == FREE
        assert effective_plan(NOW, subscription=sub(end=None)) == EffectivePlan("pro", "subscription", None)


class TestGrantAgainstSubscription:
    def test_the_higher_plan_wins(self):
        assert effective_plan(NOW, grants=[grant("pro")], subscription=sub("free")).source == "grant"
        assert effective_plan(NOW, grants=[grant("free")], subscription=sub("pro")).source == "subscription"

    def test_on_a_tie_the_subscription_is_the_source_because_it_is_the_one_that_renews(self):
        result = effective_plan(NOW, grants=[grant(end=90)], subscription=sub(end=20))
        assert result == EffectivePlan("pro", "subscription", NOW + 20 * DAY)

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
