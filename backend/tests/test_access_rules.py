"""May a person change things? Read-only exactly when time-limited access ended (entitlements.access_of)."""

from datetime import UTC, datetime, timedelta

import pytest

from tradejournal.entitlements import FREE, Access, EffectivePlan, EndedGrant, access_of

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
ENDED = EndedGrant(NOW - timedelta(days=3), revoked=False)


def test_an_account_that_never_had_a_grant_is_never_read_only():
    assert access_of(FREE, None) == Access("active")


def test_the_free_plan_after_a_grant_ran_out_is_read_only_and_says_when_and_why():
    assert access_of(FREE, ENDED) == Access("ended", ENDED.ended_at, "expired")


def test_the_free_plan_after_a_grant_was_ended_early_says_revoked():
    revoked = EndedGrant(NOW - timedelta(hours=2), revoked=True)
    assert access_of(FREE, revoked) == Access("ended", revoked.ended_at, "revoked")


def test_a_running_grant_is_active_and_knows_its_end_even_with_an_older_one_behind_it():
    running = EffectivePlan("pro", "grant", NOW + timedelta(days=12))
    assert access_of(running, ENDED) == Access("active", NOW + timedelta(days=12))


@pytest.mark.parametrize("source", ["override", "subscription"])
def test_an_override_or_a_subscription_keeps_full_access_whatever_came_before(source):
    plan = EffectivePlan("pro", source, None)
    assert access_of(plan, ENDED) == Access("active", None)


def test_an_operator_lowering_the_plan_is_their_decision_and_not_read_only():
    lowered = EffectivePlan("free", "override", None)
    assert access_of(lowered, ENDED) == Access("active", None)


# ---- with billing switched on (the billing design): no free plan, so nothing live means read-only ----------------------


def test_with_billing_on_an_account_with_nothing_at_all_is_read_only_as_lapsed():
    assert access_of(FREE, None, billing=True) == Access("ended", None, "lapsed")


def test_with_billing_on_a_subscription_that_lapsed_after_the_grant_ended_says_lapsed_and_when():
    lapsed = NOW - timedelta(hours=1)
    assert access_of(FREE, ENDED, billing=True, lapsed_at=lapsed) == Access("ended", lapsed, "lapsed")


def test_with_billing_on_a_grant_that_ended_after_the_subscription_says_expired():
    lapsed = ENDED.ended_at - timedelta(days=1)
    assert access_of(FREE, ENDED, billing=True, lapsed_at=lapsed) == Access("ended", ENDED.ended_at, "expired")


def test_with_billing_on_anything_live_keeps_full_access():
    for source in ("grant", "subscription", "override"):
        plan = EffectivePlan("pro", source, NOW + timedelta(days=2))
        assert access_of(plan, ENDED, billing=True, lapsed_at=NOW).state == "active"


def test_with_billing_off_a_lapsed_subscription_changes_nothing_so_switching_off_restores_today():
    assert access_of(FREE, None, lapsed_at=NOW) == Access("active")
    assert access_of(FREE, ENDED, lapsed_at=NOW) == Access("ended", ENDED.ended_at, "expired")
