"""The person's own billing, and the provider's webhook (the billing design, "Routes" and "Webhook").

Every method acts on the signed-in person only: none takes another person's id, and the checkout is made on the server with
that person's id inside it. The database work runs in short transactions as the sign-in role (the webhook as its own role),
and no provider call is made while a transaction is open, so a slow provider never holds a connection others need.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import psycopg

from ..billing import price
from ..billing.provider import BillingProvider
from ..billing.types import BillingError, ProviderRefused, ProviderUnavailable
from ..pg import audit as pg_audit
from ..pg import billing as pg_billing
from ..pg import plans as pg_plans
from ..pg.system import SystemDatabase
from .schemas.billing import BillingOut, CheckoutOut, PortalOut

logger = logging.getLogger("tj.billing")

UNAVAILABLE = "The payment provider did not answer. Nothing was charged and nothing changed; try again in a minute."


class BillingRefusal(Exception):
    """The person asked for something that cannot be done now; `detail` is a sentence safe to show them."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class _Free:
    """What gives Pro without paying, as of now."""

    grant_ends_at: datetime | None
    grant_is_trial: bool
    override_live: bool
    override_ends_at: datetime | None  # None with `override_live`: no end date

    @property
    def until(self) -> datetime | None:
        """When the free days run out (the later of the grant and an override with an end); None when there are none."""
        ends = [d for d in (self.grant_ends_at, self.override_ends_at) if d is not None]
        return max(ends) if ends else None


class PgBillingService:
    def __init__(
        self,
        system: SystemDatabase,
        provider: BillingProvider,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._system = system
        self._provider = provider
        self._clock = clock

    @property
    def provider(self) -> BillingProvider:
        return self._provider

    # ---- what the Plan page shows -------------------------------------------------------------------------------------

    def state(self, user_id: UUID) -> BillingOut:
        now = self._clock()
        with self._system.system_session("login") as conn:
            return self._state(conn, user_id, now)

    def _state(self, conn: psycopg.Connection, user_id: UUID, now: datetime) -> BillingOut:
        row = pg_billing.subscription_of(conn, user_id)
        free = _free(conn, user_id, now)
        customer = pg_billing.customer_of(conn, user_id, self._provider.name)
        base = {
            "price": price.amount(),
            "currency": price.CURRENCY,
            "interval": "month",
            "price_text": price.PRICE_TEXT,
            "can_manage": customer is not None,
        }
        if row is not None and row.live(now):
            _, until = row.live_until()
            if row.status == "past_due":
                return BillingOut(
                    state="past_due",
                    ends_at=until,
                    renews_at=None,
                    first_charge_pending=False,
                    can_subscribe=False,
                    billing_starts_at=None,
                    reason=None,
                    **base,
                )
            if row.cancel_at_period_end:
                return BillingOut(
                    state="cancelling",
                    ends_at=row.current_period_end,
                    renews_at=None,
                    first_charge_pending=row.status == "trialing",
                    can_subscribe=False,
                    billing_starts_at=None,
                    reason=None,
                    **base,
                )
            return BillingOut(
                state="active",
                ends_at=None,
                renews_at=row.current_period_end,
                first_charge_pending=row.status == "trialing",
                can_subscribe=False,
                billing_starts_at=None,
                reason=None,
                **base,
            )
        starts = free.until if free.until is not None and free.until > now else None
        if free.override_live:
            # The team's own decision. With no end date there is nothing to pay for, so Subscribe is not offered.
            return BillingOut(
                state="covered",
                ends_at=free.override_ends_at,
                renews_at=None,
                first_charge_pending=False,
                can_subscribe=free.override_ends_at is not None,
                billing_starts_at=starts if free.override_ends_at is not None else None,
                reason=None,
                **base,
            )
        if free.grant_ends_at is not None:
            return BillingOut(
                state="trial" if free.grant_is_trial else "covered",
                ends_at=free.grant_ends_at,
                renews_at=None,
                first_charge_pending=False,
                can_subscribe=True,
                billing_starts_at=starts,
                reason=None,
                **base,
            )
        access = pg_plans.access(conn, user_id, now, billing=True)
        return BillingOut(
            state="read_only",
            ends_at=access.ends_at,
            renews_at=None,
            first_charge_pending=False,
            can_subscribe=True,
            billing_starts_at=None,
            reason=access.reason,
            **base,
        )

    # ---- subscribing ----------------------------------------------------------------------------------------------------

    def start_checkout(self, user_id: UUID, *, ip_hash: str | None = None) -> CheckoutOut:
        """Open a checkout for this person (or return the one opened in the last half hour). The first payment waits for
        the free days they have left, rounded up to whole days, so nobody pays for days they already have."""
        now = self._clock()
        provider = self._provider
        with self._system.system_session("login") as conn:
            state = self._state(conn, user_id, now)
            if not state.can_subscribe:
                if state.state in ("active", "cancelling", "past_due"):
                    raise BillingRefusal(409, "You already have a subscription. Manage it on this page.")
                raise BillingRefusal(
                    409, "Your plan is set by the team and has no end date, so there is nothing to pay."
                )
            reuse = pg_billing.recent_checkout(conn, user_id, provider.name, now=now)
            if reuse is not None:
                return CheckoutOut(pay_url=provider.pay_url(reuse))
            email = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()["email"]
            customer = pg_billing.customer_of(conn, user_id, provider.name)
            free_until = state.billing_starts_at
        free_days = math.ceil((free_until - now) / timedelta(days=1)) if free_until is not None else 0
        try:
            if customer is None:
                made = provider.find_or_create_customer(email=email, user_id=user_id)
                with self._system.system_session("login") as conn:
                    customer = pg_billing.save_customer(conn, user_id, provider.name, made)
            checkout = provider.create_checkout(customer_id=customer, user_id=user_id, free_days=free_days)
        except ProviderUnavailable as problem:
            logger.warning("checkout not opened: %s", problem.reason)
            raise BillingRefusal(503, UNAVAILABLE) from None
        except ProviderRefused as problem:
            logger.warning("checkout refused: %s", problem.reason)
            raise BillingRefusal(409, "The payment provider would not open a checkout. Nothing was charged.") from None
        with self._system.system_session("login") as conn:
            pg_billing.record_checkout(conn, checkout.checkout_id, provider.name, user_id, now=now)
            pg_audit.record(
                conn, "billing_checkout_started", user_id=user_id, ip_hash=ip_hash, meta={"free_days": free_days}
            )
        return CheckoutOut(pay_url=checkout.pay_url)

    # ---- cancel, resume, the provider's page ----------------------------------------------------------------------------

    def cancel(self, user_id: UUID, *, ip_hash: str | None = None) -> BillingOut:
        now = self._clock()
        with self._system.system_session("login") as conn:
            row = pg_billing.subscription_of(conn, user_id)
        if row is None or not row.live(now) or row.status not in ("active", "trialing"):
            raise BillingRefusal(409, "There is no renewing subscription to cancel.")
        if not row.cancel_at_period_end:
            self._call(lambda: self._provider.cancel(row.provider_subscription_id))
            with self._system.system_session("login") as conn:
                pg_billing.mark_cancelling(conn, user_id, cancelling=True, by="person", now=now)
                pg_audit.record(conn, "subscription_cancel_requested", user_id=user_id, ip_hash=ip_hash)
        return self.state(user_id)

    def resume(self, user_id: UUID, *, ip_hash: str | None = None) -> BillingOut:
        now = self._clock()
        with self._system.system_session("login") as conn:
            row = pg_billing.subscription_of(conn, user_id)
        if row is None or not row.live(now) or not row.cancel_at_period_end:
            raise BillingRefusal(409, "There is no cancelled subscription to resume. Subscribe instead.")
        self._call(lambda: self._provider.resume(row.provider_subscription_id))
        with self._system.system_session("login") as conn:
            pg_billing.mark_cancelling(conn, user_id, cancelling=False, by=None, now=now)
            pg_audit.record(conn, "subscription_resumed", user_id=user_id, ip_hash=ip_hash)
        return self.state(user_id)

    def portal(self, user_id: UUID, *, ip_hash: str | None = None) -> PortalOut:
        with self._system.system_session("login") as conn:
            customer = pg_billing.customer_of(conn, user_id, self._provider.name)
            row = pg_billing.subscription_of(conn, user_id)
        if customer is None:
            raise BillingRefusal(409, "There is nothing to manage yet: subscribe first.")
        url = self._call(
            lambda: self._provider.portal_url(
                customer_id=customer, subscription_id=row.provider_subscription_id if row else None
            )
        )
        if not url.startswith("https://"):
            logger.warning("billing page link refused: not https")
            raise BillingRefusal(503, UNAVAILABLE)
        with self._system.system_session("login") as conn:
            pg_audit.record(conn, "billing_portal_opened", user_id=user_id, ip_hash=ip_hash)
        return PortalOut(url=url)

    def _call(self, call: Callable):
        try:
            return call()
        except ProviderUnavailable as problem:
            logger.warning("billing call failed: %s", problem.reason)
            raise BillingRefusal(503, UNAVAILABLE) from None
        except ProviderRefused as problem:
            logger.warning("billing call refused: %s", problem.reason)
            raise BillingRefusal(409, problem.detail) from None

    # ---- account deletion -----------------------------------------------------------------------------------------------

    def stop_renewal_for_deletion(self, user_id: UUID) -> None:
        """Deletion was asked for: stop the next renewal now (they keep what they paid for). Best effort: when the account
        is actually deleted, the sweep ends the subscription whatever happened here."""
        now = self._clock()
        with self._system.system_session("login") as conn:
            row = pg_billing.subscription_of(conn, user_id)
        if row is None or not row.live(now) or row.cancel_at_period_end or row.status not in ("active", "trialing"):
            return
        try:
            self._provider.cancel(row.provider_subscription_id)
        except BillingError as problem:
            logger.warning("renewal not stopped at deletion request: %s", problem.reason)
            return
        with self._system.system_session("login") as conn:
            pg_billing.mark_cancelling(conn, user_id, cancelling=True, by="deletion", now=now)
            pg_audit.record(
                conn,
                "subscription_cancel_requested",
                user_id=user_id,
                actor=pg_audit.ACTOR_SYSTEM,
                meta={"via": "deletion"},
            )

    def resume_after_restore(self, user_id: UUID) -> None:
        """They signed in again within the 7 days, so the deletion is off: undo the cancel it made (and only that one: a
        cancel the person asked for themselves stays)."""
        now = self._clock()
        with self._system.system_session("login") as conn:
            row = pg_billing.subscription_of(conn, user_id)
        if row is None or not row.live(now) or not row.cancel_at_period_end or row.cancel_requested_by != "deletion":
            return
        try:
            self._provider.resume(row.provider_subscription_id)
        except BillingError as problem:
            logger.warning("renewal not resumed after a restored account: %s", problem.reason)
            return
        with self._system.system_session("login") as conn:
            pg_billing.mark_cancelling(conn, user_id, cancelling=False, by=None, now=now)
            pg_audit.record(
                conn, "subscription_resumed", user_id=user_id, actor=pg_audit.ACTOR_SYSTEM, meta={"via": "restore"}
            )

    # ---- the webhook ----------------------------------------------------------------------------------------------------

    def handle_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> pg_billing.Applied:
        """Check the signature (raises `BadSignature`), then store and apply the event in one transaction as the webhook's
        own role. An event the operator should look at (a refund, a mismatch, a second subscription) is logged by kind."""
        event = self._provider.verify_webhook(headers, raw_body)
        with self._system.system_session("webhook") as conn:
            applied = pg_billing.apply_event(conn, self._provider.name, event, now=self._clock())
        if applied.alert:
            logger.warning("billing event needs a look: %s (%s, %s)", applied.alert, event.type, applied.outcome)
        return applied


def _free(conn: psycopg.Connection, user_id: UUID, now: datetime) -> _Free:
    grant = conn.execute(
        """SELECT r.ends_at, g.kind FROM grant_redemptions r JOIN grant_codes g ON g.id = r.grant_code_id
           WHERE r.user_id = %s AND r.starts_at <= %s AND r.ends_at > %s AND r.revoked_at IS NULL
           ORDER BY r.ends_at DESC LIMIT 1""",
        (user_id, now, now),
    ).fetchone()
    override = conn.execute(
        """SELECT plan, expires_at FROM plan_overrides
           WHERE user_id = %s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %s)""",
        (user_id, now),
    ).fetchone()
    live_override = override is not None and override["plan"] == "pro"
    return _Free(
        grant_ends_at=grant["ends_at"] if grant else None,
        grant_is_trial=bool(grant and grant["kind"] == "trial"),
        override_live=live_override,
        override_ends_at=override["expires_at"] if live_override else None,
    )
