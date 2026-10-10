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
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
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

# Billing states that count as having the subscription (doc 03: active, trialing, or in the seven-day grace).
SUBSCRIPTION_LIVE = frozenset({"active", "trialing", "grace"})


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
    reason: Literal["expired", "revoked"] | None = None


@dataclass(frozen=True)
class EndedGrant:
    """The most recent time-limited grant that is no longer working: it ran out at `ends_at`, or was ended early."""

    ended_at: datetime
    revoked: bool


def access_of(plan: EffectivePlan, last_ended: EndedGrant | None) -> Access:
    """Read-only exactly when the person is on the free plan *because* time-limited access ended. An active grant, an override
    (the operator decided) and a subscription all keep full access; so does an account that never had a grant."""
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
    if (
        subscription is not None
        and subscription.status in SUBSCRIPTION_LIVE
        and subscription.plan in _RANK
        and (subscription.current_period_end is None or subscription.current_period_end > now)
    ):
        candidates.append(EffectivePlan(subscription.plan, "subscription", subscription.current_period_end))
    if not candidates:
        return FREE
    # The higher plan; on a tie, the subscription.
    return max(candidates, key=lambda c: (_RANK[c.plan], c.source == "subscription"))
