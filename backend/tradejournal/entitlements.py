"""Which plan a person is on, and what it includes (doc 03 section 5, ADR-012).

The effective plan is computed every time and never stored, so nothing that writes billing state can overwrite what an
operator decided. In this order:

1. an active **override** wins outright, even when it lowers the plan;
2. otherwise the **higher** of an active **grant** (a code, or an invite that carried one) and an active **subscription**;
   when both are the same plan the subscription is the source, since it is the one that renews;
3. otherwise the **free** plan.

Anything over a plan's limits when a grant or override ends stays and is read-only; data is never deleted (the rule that
enforces that lives with the limits, when there are any). This module knows nothing about the database or the web: it
takes plain values, so every rule is a plain test.

**Access** is a separate, simpler question: may this person change anything? A person whose full access came from a code or
an invite (a beta account, a promo code) and has run out, or was ended by an operator, keeps their data and can look at all
of it and download it, but cannot create, change or delete (`access_of`). A person who never had such access (the owner,
accounts made without one) is never affected, so this rule can be switched on without touching anyone who has no end date.

**With billing switched on** (`TJ_BILLING_ENABLED`, the billing design) there is no free plan: someone with nothing live is
read-only, and a subscription that ended is a third reason, `lapsed`. A subscription is live while it is paid for: to the end of
the paid period plus one day while it renews (so a renewal whose webhook is late locks nobody out), to the end of the period
exactly once it is cancelling, and for 7 days from the first failed payment (`past_due_since`; the provider may already have
moved the period on). With the switch off, every rule above is exactly as it was.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Plan = Literal["free", "pro"]
Source = Literal["override", "grant", "subscription", "free"]

PLANS: tuple[Plan, ...] = ("free", "pro")  # lowest first
_RANK = {plan: rank for rank, plan in enumerate(PLANS)}

# What each plan includes. Both are empty on purpose: what Free and Pro limit (accounts, history, files) is a business
# decision that has not been made, and nothing here may invent it. The shape is fixed, so a limit added later (for
# example `max_active_accounts`) is one line and shows up on the Plan page by itself. `None` is never a value: a plan that
# does not limit something simply does not list it.
LIMITS: dict[Plan, dict[str, int | bool]] = {"free": {}, "pro": {}}

# Stored subscription states that can give the plan (migration 0021): paid for (active), in a provider's trial while free days
# run (trialing), or a renewal that failed and is in its seven days of grace (past_due). Paused and canceled never do.
SUBSCRIPTION_LIVE = frozenset({"active", "trialing", "past_due"})
GRACE = timedelta(days=7)  # after the first failed payment, before read-only
RENEWAL_MARGIN = timedelta(days=1)  # a renewing subscription stays live this long past its period, for a late webhook


@dataclass(frozen=True)
class Override:
    plan: Plan
    expires_at: datetime | None
    revoked: bool = False


@dataclass(frozen=True)
class Grant:
    plan: Plan
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class SubscriptionState:
    plan: Plan
    status: str
    current_period_end: datetime | None
    cancel_at_period_end: bool = False
    past_due_since: datetime | None = None  # when the first failed payment of this run of failures happened
    ended_at: datetime | None = None  # when the provider ended it (canceled)


def subscription_live_until(sub: SubscriptionState) -> tuple[bool, datetime | None]:
    """Whether the subscription can give its plan at all, and until when: (False, None) for one that never will again, (True,
    None) for a live one with no end known yet, else (True, the instant it stops). Live means `now < until`."""
    if sub.status not in SUBSCRIPTION_LIVE or sub.plan not in _RANK:
        return False, None
    if sub.status == "past_due":
        start = sub.past_due_since or sub.current_period_end
        return (True, start + GRACE) if start is not None else (False, None)
    if sub.current_period_end is None:
        return True, None
    if sub.cancel_at_period_end:
        return True, sub.current_period_end
    return True, sub.current_period_end + RENEWAL_MARGIN


def subscription_lapsed_at(sub: SubscriptionState | None, now: datetime) -> datetime | None:
    """When a subscription that no longer gives the plan stopped giving it, or None if it still does (or there is none). This
    is what "lapsed" refers to on a read-only account."""
    if sub is None:
        return None
    counts, until = subscription_live_until(sub)
    if counts:
        return until if until is not None and until <= now else None
    return sub.ended_at or sub.current_period_end or sub.past_due_since


@dataclass(frozen=True)
class EffectivePlan:
    plan: Plan
    source: Source
    expires_at: datetime | None


FREE = EffectivePlan("free", "free", None)


@dataclass(frozen=True)
class Access:
    """May the person change things? `ends_at` is when their time-limited access ends (state "active") or ended ("ended")."""

    state: Literal["active", "ended"]
    ends_at: datetime | None = None
    reason: Literal["expired", "revoked", "lapsed"] | None = None


@dataclass(frozen=True)
class EndedGrant:
    """The most recent time-limited grant that is no longer working: it ran out at `ends_at`, or was ended early."""

    ended_at: datetime
    revoked: bool


def access_of(
    plan: EffectivePlan,
    last_ended: EndedGrant | None,
    *,
    billing: bool = False,
    lapsed_at: datetime | None = None,
) -> Access:
    """Read-only exactly when the person is on the free plan *because* time-limited access ended. An active grant, an override
    (the operator decided) and a subscription all keep full access; so does an account that never had a grant.

    With `billing` on there is no free plan, so the free plan always means read-only, and the reason is whichever ended last:
    a grant (expired or revoked) or a subscription (`lapsed_at`; "lapsed"). An account with nothing at all is "lapsed" with no
    date. `lapsed_at` is ignored with billing off, so turning the switch off restores today's rule exactly."""
    if plan.source == "free" and billing:
        if lapsed_at is not None and (last_ended is None or lapsed_at >= last_ended.ended_at):
            return Access("ended", lapsed_at, "lapsed")
        if last_ended is None:
            return Access("ended", None, "lapsed")
    if plan.source == "free" and last_ended is not None:
        return Access("ended", last_ended.ended_at, "revoked" if last_ended.revoked else "expired")
    return Access("active", plan.expires_at if plan.source == "grant" else None)


def limits_for(plan: str) -> dict[str, int | bool]:
    """What the plan includes. An unknown plan name (a row written by a newer version) gets the free plan's limits."""
    return dict(LIMITS.get(plan, LIMITS["free"]))  # type: ignore[call-overload]


def effective_plan(
    now: datetime,
    *,
    override: Override | None = None,
    grants: Iterable[Grant] = (),
    subscription: SubscriptionState | None = None,
) -> EffectivePlan:
    if override is not None and not override.revoked and (override.expires_at is None or override.expires_at > now):
        return EffectivePlan(override.plan, "override", override.expires_at)

    candidates: list[EffectivePlan] = []
    live = [g for g in grants if g.starts_at <= now < g.ends_at and g.plan in _RANK]
    if live:
        best = max(live, key=lambda g: (_RANK[g.plan], g.ends_at))
        candidates.append(EffectivePlan(best.plan, "grant", best.ends_at))
    if subscription is not None:
        counts, until = subscription_live_until(subscription)
        if counts and (until is None or until > now):
            candidates.append(EffectivePlan(subscription.plan, "subscription", until))
    if not candidates:
        return FREE
    # The higher plan; on a tie, the subscription.
    return max(candidates, key=lambda c: (_RANK[c.plan], c.source == "subscription"))
