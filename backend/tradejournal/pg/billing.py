"""Billing on Postgres (migration 0021; the billing design). Who calls what, as which role:

    subscription_of, live_until       a person's subscription and when it stops giving Pro   (login, admin, sweep, webhook)
    give_trial                        the 7-day trial, inside the sign-up transaction          (login)
    customer_of, save_customer        the provider's customer for a person                     (login)
    recent_checkout, record_checkout  the checkouts the server opened                          (login)
    mark_cancelling                   a cancel or a resume the app asked the provider for      (login)
    apply_event                       one webhook event, stored once and applied in order      (webhook)
    queue_cancellation, ...           the final cancel of a deleted account's subscription     (sweep)
    reconcile                         the nightly check against the provider's own answer      (sweep)

Nothing here writes a plan, an override or a grant other than the trial: access is worked out from these rows by
`entitlements`, every time. No function returns or logs an email, a name or a card detail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import psycopg

from ..billing.types import BillingEvent, ProviderSubscription
from ..entitlements import SubscriptionState, subscription_live_until
from . import audit
from .plans import LOCK_CLASS, Redeemed, _give, _usable

TRIAL_DAYS = 7  # migration 0021 makes the trial's code with this many days
CHECKOUT_REUSE = timedelta(minutes=30)  # a second Subscribe within this lands on the checkout already open
OPEN_CHECKOUT = timedelta(days=2)  # a checkout this recent can still turn into a subscription
LIVE_STATUSES = ("trialing", "active", "past_due")

_COLUMNS = """user_id, provider, provider_customer_id, provider_subscription_id, plan, status, current_period_end,
              cancel_at_period_end, cancel_requested_by, past_due_since, ended_at, ended_notice_sent_at,
              provider_updated_at, created_at, updated_at"""


@dataclass(frozen=True)
class SubscriptionRow:
    user_id: UUID
    provider: str
    provider_customer_id: str | None
    provider_subscription_id: str
    plan: str
    status: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
    cancel_requested_by: str | None
    past_due_since: datetime | None
    ended_at: datetime | None
    ended_notice_sent_at: datetime | None
    provider_updated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def state(self) -> SubscriptionState:
        return SubscriptionState(
            self.plan,  # type: ignore[arg-type]  # a CHECK constraint holds it to the plans entitlements knows
            self.status,
            self.current_period_end,
            cancel_at_period_end=self.cancel_at_period_end,
            past_due_since=self.past_due_since,
            ended_at=self.ended_at,
        )

    def live_until(self) -> tuple[bool, datetime | None]:
        return subscription_live_until(self.state())

    def live(self, now: datetime) -> bool:
        counts, until = self.live_until()
        return counts and (until is None or until > now)


def subscription_of(conn: psycopg.Connection, user_id: UUID, *, lock: bool = False) -> SubscriptionRow | None:
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM subscriptions WHERE user_id = %s{' FOR UPDATE' if lock else ''}",  # noqa: S608
        (user_id,),
    ).fetchone()
    return SubscriptionRow(**row) if row else None


def _by_subscription_id(conn: psycopg.Connection, provider: str, subscription_id: str) -> SubscriptionRow | None:
    row = conn.execute(
        f"""SELECT {_COLUMNS} FROM subscriptions
            WHERE provider = %s AND provider_subscription_id = %s FOR UPDATE""",  # noqa: S608
        (provider, subscription_id),
    ).fetchone()
    return SubscriptionRow(**row) if row else None


# ---- the trial ----------------------------------------------------------------------------------------------------------


def trial_code_id(conn: psycopg.Connection) -> int | None:
    row = conn.execute("SELECT id FROM grant_codes WHERE kind = 'trial' AND revoked_at IS NULL").fetchone()
    return row["id"] if row else None


def give_trial(conn: psycopg.Connection, user_id: UUID, *, now: datetime | None = None) -> Redeemed | None:
    """The 7-day trial, from now, inside the sign-up transaction. Once per account (the code's one-per-person key holds it):
    None when this person already had it, or when the trial's code is missing or was revoked (sign-up goes ahead)."""
    moment = now or datetime.now(UTC)
    code = trial_code_id(conn)
    if code is None:
        return None
    conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_CLASS, code))
    row = _usable(conn, code, user_id, moment)
    return None if row is None else _give(conn, row, user_id, moment)


# ---- customers and checkouts (the sign-in role, for the person's own routes) ------------------------------------------


def customer_of(conn: psycopg.Connection, user_id: UUID, provider: str) -> str | None:
    row = conn.execute(
        "SELECT provider_customer_id FROM billing_customers WHERE user_id = %s AND provider = %s", (user_id, provider)
    ).fetchone()
    return row["provider_customer_id"] if row else None


def save_customer(conn: psycopg.Connection, user_id: UUID, provider: str, customer_id: str) -> str:
    """Remember the provider's customer for this person; the one already stored wins (two tabs at once)."""
    conn.execute(
        """INSERT INTO billing_customers (user_id, provider, provider_customer_id) VALUES (%s, %s, %s)
           ON CONFLICT DO NOTHING""",
        (user_id, provider, customer_id),
    )
    return customer_of(conn, user_id, provider) or customer_id


def recent_checkout(conn: psycopg.Connection, user_id: UUID, provider: str, *, now: datetime) -> str | None:
    """A checkout opened for this person in the last half hour that has not become a subscription: a second Subscribe goes
    back to it rather than opening another."""
    row = conn.execute(
        """SELECT provider_checkout_id FROM billing_checkouts
           WHERE user_id = %s AND provider = %s AND completed_at IS NULL AND created_at > %s
           ORDER BY created_at DESC LIMIT 1""",
        (user_id, provider, now - CHECKOUT_REUSE),
    ).fetchone()
    return row["provider_checkout_id"] if row else None


def record_checkout(
    conn: psycopg.Connection, checkout_id: str, provider: str, user_id: UUID, *, now: datetime | None = None
) -> None:
    conn.execute(
        """INSERT INTO billing_checkouts (provider_checkout_id, provider, user_id, created_at) VALUES (%s, %s, %s, %s)
           ON CONFLICT DO NOTHING""",
        (checkout_id, provider, user_id, now or datetime.now(UTC)),
    )


def mark_cancelling(
    conn: psycopg.Connection,
    user_id: UUID,
    *,
    cancelling: bool,
    by: Literal["person", "deletion"] | None,
    now: datetime | None = None,
) -> bool:
    """What the app asked the provider for, shown at once; the provider's event confirms it (and wins if they differ)."""
    row = conn.execute(
        """UPDATE subscriptions SET cancel_at_period_end = %s, cancel_requested_by = %s, updated_at = %s
           WHERE user_id = %s RETURNING user_id""",
        (cancelling, by if cancelling else None, now or datetime.now(UTC), user_id),
    ).fetchone()
    return row is not None


# ---- the webhook (the webhook role) -----------------------------------------------------------------------------------

Outcome = Literal["duplicate", "applied", "stale", "unknown_type", "no_account", "refund_recorded", "received", "alert"]


@dataclass(frozen=True)
class Applied:
    outcome: Outcome
    user_id: UUID | None = None
    status_before: str | None = None
    status_after: str | None = None
    alert: str | None = None  # why the operator should look (a short reason code), when there is a reason


def apply_event(
    conn: psycopg.Connection, provider: str, event: BillingEvent, *, now: datetime | None = None
) -> Applied:
    """Store the event once, then apply it if it is about a subscription we can place and newer than what is held. Call it
    in one transaction: the stored event and what it changed are kept, or neither is."""
    moment = now or datetime.now(UTC)
    sub = event.subscription
    about = sub.subscription_id if sub else (event.refund.subscription_id if event.refund else None)
    stored = conn.execute(
        """INSERT INTO billing_events (provider, event_key, type, occurred_at, provider_subscription_id, payload, received_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (provider, event_key) DO NOTHING RETURNING id""",
        (provider, event.event_id, event.type[:100], event.occurred_at, about, json.dumps(event.payload), moment),
    ).fetchone()
    if stored is None:
        return Applied("duplicate")  # delivered before: nothing more happens

    def done(applied: Applied) -> Applied:
        conn.execute(
            "UPDATE billing_events SET outcome = %s, user_id = %s WHERE id = %s",
            (applied.outcome, applied.user_id, stored["id"]),
        )
        return applied

    if event.kind in ("refund", "payment"):
        owner = _owner_of_id(conn, provider, about)
        if event.kind == "refund":
            if owner is not None:
                audit.record(
                    conn,
                    "billing_refund_recorded",
                    user_id=owner,
                    actor=audit.ACTOR_SYSTEM,
                    meta={"action": event.refund.action if event.refund else None},
                )
            return done(Applied("refund_recorded", owner, alert="refund"))
        return done(Applied("received", owner))
    if event.kind != "subscription" or sub is None:
        return done(Applied("unknown_type"))

    current = _by_subscription_id(conn, provider, sub.subscription_id)
    if current is not None:
        if current.provider_updated_at is not None and event.occurred_at < current.provider_updated_at:
            return done(Applied("stale", current.user_id))  # an older event, arrived late: what we hold is newer
        return done(_write(conn, provider, event, sub, current.user_id, current, moment))

    owner, problem = _owner_of_new(conn, provider, sub, moment)
    if problem == "no_account":
        if sub.status in LIVE_STATUSES:
            queue_cancellation_of(conn, provider, sub.subscription_id)
        return done(Applied("no_account"))
    if problem == "stale":
        return done(Applied("stale", owner))
    if owner is None:
        return done(Applied("alert", alert=problem or "unplaced"))  # stored, nothing granted: the operator looks
    existing = subscription_of(conn, owner, lock=True)
    if existing is not None and existing.provider_subscription_id != sub.subscription_id:
        if existing.status in LIVE_STATUSES and existing.live(moment):
            # A second live subscription for one person: never overwrite the first. Charged twice: the operator refunds one.
            audit.record(
                conn,
                "billing_alert",
                user_id=owner,
                actor=audit.ACTOR_SYSTEM,
                meta={"reason": "second_subscription"},
            )
            return done(Applied("alert", owner, alert="second_subscription"))
    return done(_write(conn, provider, event, sub, owner, existing, moment))


def _owner_of_id(conn: psycopg.Connection, provider: str, subscription_id: str | None) -> UUID | None:
    if not subscription_id:
        return None
    row = conn.execute(
        "SELECT user_id FROM subscriptions WHERE provider = %s AND provider_subscription_id = %s",
        (provider, subscription_id),
    ).fetchone()
    return row["user_id"] if row else None


def _owner_of_new(
    conn: psycopg.Connection, provider: str, sub: ProviderSubscription, now: datetime
) -> tuple[UUID | None, str | None]:
    """Whose a subscription we have not seen is. The checkout the server opened decides; the custom data is only a cross
    check, because a checkout could be opened by someone else with any custom data in it. Returns (the person, None), or
    (None, why not)."""
    ref = _uuid(sub.user_ref)
    if sub.checkout_id:
        row = conn.execute(
            "SELECT user_id FROM billing_checkouts WHERE provider = %s AND provider_checkout_id = %s",
            (provider, sub.checkout_id),
        ).fetchone()
        if row is not None:
            if sub.user_ref is not None and ref != row["user_id"]:
                return None, "mismatch"  # the checkout and the custom data disagree: grant nothing
            return row["user_id"], None
    if ref is None:
        return None, "unplaced"
    exists = conn.execute("SELECT 1 FROM users WHERE id = %s", (ref,)).fetchone()
    if exists is None:
        return None, "no_account"
    opened = conn.execute(
        """SELECT 1 FROM billing_checkouts
           WHERE user_id = %s AND provider = %s AND completed_at IS NULL AND created_at > %s LIMIT 1""",
        (ref, provider, now - OPEN_CHECKOUT),
    ).fetchone()
    if opened is not None:
        return ref, None  # its first event came later than this one; the open checkout is the person's
    held = subscription_of(conn, ref)
    if held is not None and held.provider_subscription_id != sub.subscription_id:
        return ref, "stale"  # a late event about a subscription this person has since replaced
    return None, "unplaced"


def _uuid(value: str | None) -> UUID | None:
    try:
        return UUID(value) if value else None
    except ValueError:
        return None


def _write(
    conn: psycopg.Connection,
    provider: str,
    event: BillingEvent,
    sub: ProviderSubscription,
    owner: UUID,
    before: SubscriptionRow | None,
    now: datetime,
) -> Applied:
    same = before is not None and before.provider_subscription_id == sub.subscription_id
    if sub.status == "past_due":
        # Grace counts from the first failure of this run of failures: a second "past due" keeps the first date.
        past_due_since = (
            before.past_due_since
            if same and before is not None and before.status == "past_due" and before.past_due_since
            else event.occurred_at
        )
    else:
        past_due_since = None
    by = (before.cancel_requested_by if same and before is not None else None) or "person"
    ended_at = sub.ended_at or (event.occurred_at if sub.status == "canceled" else None)
    renewing = sub.status in ("trialing", "active", "past_due") and not sub.cancel_at_period_end
    notice = None if renewing or not same or before is None else before.ended_notice_sent_at
    conn.execute(
        """INSERT INTO subscriptions (user_id, provider, provider_customer_id, provider_subscription_id, plan, status,
                                      current_period_end, cancel_at_period_end, cancel_requested_by, past_due_since,
                                      ended_at, ended_notice_sent_at, provider_updated_at, created_at, updated_at)
           VALUES (%(user)s, %(provider)s, %(customer)s, %(sub)s, 'pro', %(status)s, %(period_end)s, %(cancelling)s,
                   %(by)s, %(past_due)s, %(ended)s, %(notice)s, %(occurred)s, %(now)s, %(now)s)
           ON CONFLICT (user_id) DO UPDATE SET
               provider = EXCLUDED.provider,
               provider_customer_id = coalesce(EXCLUDED.provider_customer_id, subscriptions.provider_customer_id),
               provider_subscription_id = EXCLUDED.provider_subscription_id,
               status = EXCLUDED.status,
               current_period_end = EXCLUDED.current_period_end,
               cancel_at_period_end = EXCLUDED.cancel_at_period_end,
               cancel_requested_by = EXCLUDED.cancel_requested_by,
               past_due_since = EXCLUDED.past_due_since,
               ended_at = EXCLUDED.ended_at,
               ended_notice_sent_at = EXCLUDED.ended_notice_sent_at,
               provider_updated_at = EXCLUDED.provider_updated_at,
               updated_at = EXCLUDED.updated_at""",
        {
            "user": owner,
            "provider": provider,
            "customer": sub.customer_id,
            "sub": sub.subscription_id,
            "status": sub.status,
            "period_end": sub.current_period_end,
            "cancelling": sub.cancel_at_period_end,
            "by": by if sub.cancel_at_period_end else None,
            "past_due": past_due_since,
            "ended": ended_at,
            "notice": notice,
            "occurred": event.occurred_at,
            "now": now,
        },
    )
    if sub.customer_id:
        conn.execute(
            """INSERT INTO billing_customers (user_id, provider, provider_customer_id) VALUES (%s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (owner, provider, sub.customer_id),
        )
    conn.execute(
        """UPDATE billing_checkouts SET completed_at = %s
           WHERE user_id = %s AND provider = %s AND completed_at IS NULL
             AND (provider_checkout_id = %s OR created_at > %s)""",
        (now, owner, provider, sub.checkout_id or "", now - OPEN_CHECKOUT),
    )
    status_before = before.status if same and before is not None else None
    changed = (
        not same
        or before is None
        or (before.status, before.cancel_at_period_end) != (sub.status, sub.cancel_at_period_end)
    )
    if changed:
        audit.record(
            conn,
            "subscription_changed",
            user_id=owner,
            actor=audit.ACTOR_SYSTEM,
            meta={"from": status_before, "to": sub.status, "cancelling": sub.cancel_at_period_end},
        )
    return Applied("applied", owner, status_before, sub.status)


# ---- the final cancel after a deletion, and the nightly check (the sweep role) -----------------------------------------


def queue_cancellation(conn: psycopg.Connection, user_id: UUID) -> bool:
    """In the deletion's transaction: remember to end this person's subscription at the provider, if it has not ended. The
    entry outlives the account (it holds no person's id), and the sweep retries it until the provider confirms."""
    row = conn.execute(
        """INSERT INTO billing_cancellations (provider, provider_subscription_id)
           SELECT provider, provider_subscription_id FROM subscriptions WHERE user_id = %s AND status <> 'canceled'
           ON CONFLICT DO NOTHING RETURNING provider_subscription_id""",
        (user_id,),
    ).fetchone()
    return row is not None


def queue_cancellation_of(conn: psycopg.Connection, provider: str, subscription_id: str) -> None:
    conn.execute(
        """INSERT INTO billing_cancellations (provider, provider_subscription_id) VALUES (%s, %s)
           ON CONFLICT DO NOTHING""",
        (provider, subscription_id),
    )


def cancellations(conn: psycopg.Connection, provider: str, *, limit: int = 100) -> list[str]:
    rows = conn.execute(
        """SELECT provider_subscription_id FROM billing_cancellations WHERE provider = %s
           ORDER BY created_at, provider_subscription_id LIMIT %s""",
        (provider, limit),
    ).fetchall()
    return [r["provider_subscription_id"] for r in rows]


def cancellation_done(conn: psycopg.Connection, provider: str, subscription_id: str) -> None:
    conn.execute(
        "DELETE FROM billing_cancellations WHERE provider = %s AND provider_subscription_id = %s",
        (provider, subscription_id),
    )


def cancellation_failed(conn: psycopg.Connection, provider: str, subscription_id: str, reason: str) -> None:
    conn.execute(
        """UPDATE billing_cancellations SET attempts = attempts + 1, last_error = %s
           WHERE provider = %s AND provider_subscription_id = %s""",
        (reason[:100], provider, subscription_id),
    )


def to_check(conn: psycopg.Connection, provider: str, *, limit: int = 500) -> list[str]:
    """Subscriptions the nightly check asks the provider about: every one not ended."""
    rows = conn.execute(
        """SELECT provider_subscription_id FROM subscriptions WHERE provider = %s AND status <> 'canceled'
           ORDER BY updated_at, provider_subscription_id LIMIT %s""",
        (provider, limit),
    ).fetchall()
    return [r["provider_subscription_id"] for r in rows]


def reconcile(conn: psycopg.Connection, provider: str, answer: ProviderSubscription, *, now: datetime) -> bool:
    """Make the stored subscription agree with the provider's answer. True when something was wrong (a webhook was missed),
    and then the correction is kept as an event with the outcome `reconciled`."""
    held = _by_subscription_id(conn, provider, answer.subscription_id)
    if held is None:
        return False
    wanted = (answer.status, answer.current_period_end, answer.cancel_at_period_end)
    if (held.status, held.current_period_end, held.cancel_at_period_end) == wanted:
        return False
    past_due_since = (held.past_due_since or now) if answer.status == "past_due" else None
    ended_at = answer.ended_at or (held.ended_at or now if answer.status == "canceled" else None)
    conn.execute(
        """UPDATE subscriptions SET status = %s, current_period_end = %s, cancel_at_period_end = %s, past_due_since = %s,
                                    ended_at = %s, provider_updated_at = %s, updated_at = %s
           WHERE provider = %s AND provider_subscription_id = %s""",
        (
            answer.status,
            answer.current_period_end,
            answer.cancel_at_period_end,
            past_due_since,
            ended_at,
            now,
            now,
            provider,
            answer.subscription_id,
        ),
    )
    conn.execute(
        """INSERT INTO billing_events (provider, event_key, type, occurred_at, user_id, provider_subscription_id, outcome,
                                       payload, received_at)
           VALUES (%s, %s, 'reconcile', %s, %s, %s, 'reconciled', %s, %s)""",
        (
            provider,
            f"reconcile:{answer.subscription_id}:{now.isoformat()}",
            now,
            held.user_id,
            answer.subscription_id,
            json.dumps(
                {
                    "before": {"status": held.status, "cancelling": held.cancel_at_period_end},
                    "after": {"status": answer.status, "cancelling": answer.cancel_at_period_end},
                }
            ),
            now,
        ),
    )
    return True


# ---- the billing emails (the sweep role, through migration 0021's functions) ------------------------------------------

REMINDER_BEFORE = timedelta(days=2)  # the trial reminder goes this long before the trial ends (design question 7)
NOTICE_RECENT = timedelta(
    days=14
)  # an ending older than this earns no email (switching the job on writes to nobody old)


@dataclass(frozen=True)
class Reminder:
    redemption_id: int
    user_id: UUID
    email: str
    first_name: str
    ends_at: datetime


@dataclass(frozen=True)
class EndedNotice:
    user_id: UUID
    email: str
    first_name: str
    ended_at: datetime


def trial_reminders_due(conn: psycopg.Connection, *, now: datetime, limit: int = 100) -> list[Reminder]:
    rows = conn.execute(
        "SELECT redemption_id, user_id, email, first_name, ends_at FROM trial_reminders_due(%s, %s, %s)",
        (now, REMINDER_BEFORE, limit),
    ).fetchall()
    return [Reminder(**r) for r in rows]


def mark_reminder_sent(conn: psycopg.Connection, redemption_id: int, *, now: datetime) -> bool:
    return bool(
        conn.execute("SELECT mark_trial_reminder_sent(%s, %s) AS done", (redemption_id, now)).fetchone()["done"]
    )


def subscription_ended_due(conn: psycopg.Connection, *, now: datetime, limit: int = 100) -> list[EndedNotice]:
    rows = conn.execute(
        "SELECT user_id, email, first_name, ended_at FROM subscription_ended_notices_due(%s, %s, %s)",
        (now, now - NOTICE_RECENT, limit),
    ).fetchall()
    return [EndedNotice(**r) for r in rows]


def mark_ended_notice_sent(conn: psycopg.Connection, user_id: UUID, *, now: datetime) -> bool:
    row = conn.execute("SELECT mark_subscription_ended_notice_sent(%s, %s) AS done", (user_id, now)).fetchone()
    return bool(row["done"])
