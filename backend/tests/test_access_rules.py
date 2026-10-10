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
