"""`FakeBilling`: a payment provider in memory, for the tests and for clicking through locally (never in production; the
configuration refuses it there).

It keeps customers, checkouts and subscriptions in dictionaries, with ids that count up and a clock the test controls. Tests
drive what a real provider would do on its own (a checkout paid, a renewal, a failed payment, a recovery, the end of a
cancelled month, a refund) and get back the signed webhook deliveries it would send, which they post to the real route. It
can also fail on request, in the ways a real one does: down, refusing, or acting and then timing out (it did it, but we
never heard).
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from . import signature
from .types import (
    STATUSES,
    BadSignature,
    BillingEvent,
    Checkout,
    DiscountSpec,
    ProviderRefused,
    ProviderSubscription,
    ProviderUnavailable,
    Refund,
    Status,
)

MONTH = timedelta(days=30)  # the fake's billing period
SIGNATURE_HEADER = "Fake-Signature"
Failure = Literal["down", "refuse", "act_then_timeout"]


@dataclass(frozen=True)
class Delivery:
    """One webhook as it would arrive: the headers and the raw body to post to `/webhooks/billing`."""

    headers: dict[str, str]
    body: bytes
    event_id: str
    event_type: str


@dataclass
class _Checkout:
    customer_id: str
    user_id: UUID
    free_days: int
    discount_id: str | None
    completed: bool = False


class FakeBilling:
    name = "fake"

    def __init__(
        self,
        *,
        secret: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.secret = secret
        self.clock = clock
        self.wall = wall
        self.customers: dict[str, str] = {}  # id -> email
        self.checkouts: dict[str, _Checkout] = {}
        self.subscriptions: dict[str, ProviderSubscription] = {}
        self.discounts: dict[str, dict[str, Any]] = {}  # id -> the spec as a dict, and "archived"
        self.calls: list[tuple[str, str]] = []  # (what, the id it was about): what the app asked for
        self._failures: list[Failure] = []
        self._ids = itertools.count(1)

    # ---- failing on request ---------------------------------------------------------------------------------------------

    def fail_next(self, how: Failure, times: int = 1) -> None:
        self._failures.extend([how] * times)

    def _gate(self, call: str, about: str) -> Failure | None:
        self.calls.append((call, about))
        if not self._failures:
            return None
        how = self._failures.pop(0)
        if how == "down":
            raise ProviderUnavailable("fake_down")
        if how == "refuse":
            raise ProviderRefused("fake_refused")
        return how  # act_then_timeout: the caller acts, then raises

    def _next(self, prefix: str) -> str:
        return f"{prefix}_fake_{next(self._ids):04d}"

    # ---- the interface (provider.py) ------------------------------------------------------------------------------------

    def find_or_create_customer(self, *, email: str, user_id: UUID) -> str:
        how = self._gate("find_or_create_customer", str(user_id))
        found = next((cid for cid, known in self.customers.items() if known == email.casefold()), None)
        customer = found or self._next("ctm")
        self.customers[customer] = email.casefold()
        if how == "act_then_timeout":
            raise ProviderUnavailable("fake_timeout")
        return customer

    def create_checkout(
        self, *, customer_id: str, user_id: UUID, free_days: int, discount_id: str | None = None
    ) -> Checkout:
        how = self._gate("create_checkout", str(user_id))
        if customer_id not in self.customers:
            raise ProviderRefused("fake_no_customer")
        checkout = self._next("txn")
        self.checkouts[checkout] = _Checkout(customer_id, user_id, max(0, free_days), discount_id)
        if how == "act_then_timeout":
            raise ProviderUnavailable("fake_timeout")
        return Checkout(checkout, self.pay_url(checkout))

    def pay_url(self, checkout_id: str) -> str:
        return f"/billing/pay?_ptxn={checkout_id}"

    def portal_url(self, *, customer_id: str, subscription_id: str | None) -> str:
        self._gate("portal_url", customer_id)
        if customer_id not in self.customers:
            raise ProviderRefused("fake_no_customer")
        return f"https://billing.fake.test/portal/{customer_id}/{next(self._ids)}"

    def cancel(self, subscription_id: str) -> ProviderSubscription:
        return self._change("cancel", subscription_id, lambda s: replace(s, cancel_at_period_end=True))

    def resume(self, subscription_id: str) -> ProviderSubscription:
        def undo(sub: ProviderSubscription) -> ProviderSubscription:
            if sub.status == "canceled":
                raise ProviderRefused("fake_canceled", "That subscription has already ended. Subscribe again instead.")
            return replace(sub, cancel_at_period_end=False)

        return self._change("resume", subscription_id, undo)

    def cancel_now(self, subscription_id: str) -> ProviderSubscription:
        now = self.clock()
        return self._change(
            "cancel_now",
            subscription_id,
            lambda s: (
                s if s.status == "canceled" else replace(s, status="canceled", cancel_at_period_end=False, ended_at=now)
            ),
        )

    def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        self._gate("fetch_subscription", subscription_id)
        found = self.subscriptions.get(subscription_id)
        if found is None:
            raise ProviderRefused("fake_not_found")
        return found

    def create_discount(self, spec: DiscountSpec) -> str:
        how = self._gate("create_discount", spec.code)
        if any(d["code"] == spec.code.upper() for d in self.discounts.values()):
            raise ProviderRefused("fake_discount_code_conflict")
        discount = self._next("dsc")
        self.discounts[discount] = {**asdict(spec), "code": spec.code.upper(), "archived": False}
        if how == "act_then_timeout":
            raise ProviderUnavailable("fake_timeout")
        return discount

    def find_discount(self, code: str) -> str | None:
        self._gate("find_discount", code)
        return next(
            (did for did, d in self.discounts.items() if d["code"] == code.upper() and not d["archived"]),
            None,
        )

    def archive_discount(self, discount_id: str) -> None:
        how = self._gate("archive_discount", discount_id)
        if discount_id not in self.discounts:
            raise ProviderRefused("fake_not_found")
        self.discounts[discount_id]["archived"] = True
        if how == "act_then_timeout":
            raise ProviderUnavailable("fake_timeout")

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> BillingEvent:
        header = next((v for k, v in headers.items() if k.lower() == SIGNATURE_HEADER.lower()), None)
        if not signature.verify(header, raw_body, self.secret, now=int(self.wall())):
            raise BadSignature("signature")
        try:
            body = json.loads(raw_body)
            event_type = str(body["event_type"])
            data = body.get("data") or {}
            occurred = datetime.fromisoformat(body["occurred_at"])
            event_id = str(body["event_id"])
        except (ValueError, KeyError, TypeError):
            raise BadSignature("malformed") from None
        kind = (
            "subscription"
            if event_type.startswith("subscription.")
            else "payment"
            if event_type.startswith("payment.")
            else "refund"
            if event_type == "refund.created"
            else "unknown"
        )
        subscription = None
        if kind == "subscription":
            try:
                subscription = _subscription_of(data)
            except (KeyError, ValueError, TypeError):
                kind = "unknown"  # signed, so genuine, but not a shape we read: kept and acknowledged, nothing applied
        refund = (
            Refund(
                action=str(data.get("action", "refund")),
                amount=str(data.get("amount", "0")),
                currency=str(data.get("currency", "USD")),
                subscription_id=data.get("subscription_id"),
            )
            if kind == "refund"
            else None
        )
        kept = {k: v for k, v in data.items() if k not in ("email", "name", "address")}  # what is stored
        return BillingEvent(
            event_id=event_id,
            type=event_type,
            kind=kind,  # type: ignore[arg-type]
            occurred_at=occurred,
            subscription=subscription,
            refund=refund,
            payload={"event_type": event_type, "data": kept},
        )

    def _change(
        self, call: str, subscription_id: str, change: Callable[[ProviderSubscription], ProviderSubscription]
    ) -> ProviderSubscription:
        how = self._gate(call, subscription_id)
        found = self.subscriptions.get(subscription_id)
        if found is None:
            raise ProviderRefused("fake_not_found")
        changed = change(found)
        self.subscriptions[subscription_id] = changed
        if how == "act_then_timeout":
            raise ProviderUnavailable("fake_timeout")
        return changed

    # ---- what a provider does on its own (tests drive these) ------------------------------------------------------------

    def complete_checkout(self, checkout_id: str, *, user_ref: str | None = None) -> Delivery:
        """The person paid (or, with free days, gave a card): a subscription is made. `user_ref` overrides the custom data,
        to test a checkout that names someone else."""
        checkout = self.checkouts[checkout_id]
        checkout.completed = True
        now = self.clock()
        trial = checkout.free_days > 0
        sub = ProviderSubscription(
            subscription_id=self._next("sub"),
            customer_id=checkout.customer_id,
            status="trialing" if trial else "active",
            current_period_end=now + (timedelta(days=checkout.free_days) if trial else MONTH),
            user_ref=user_ref if user_ref is not None else str(checkout.user_id),
            checkout_id=checkout_id,
        )
        self.subscriptions[sub.subscription_id] = sub
        return self.deliver("subscription.created", sub)

    def renew(self, subscription_id: str) -> Delivery:
        """A period ends and the next is paid (a trial's end is its first charge)."""
        sub = self.subscriptions[subscription_id]
        period_end = sub.current_period_end or self.clock()
        return self._set(
            subscription_id, "subscription.updated", status="active", current_period_end=period_end + MONTH
        )

    def fail_payment(self, subscription_id: str) -> Delivery:
        """A renewal's payment failed; the provider moves the period on and keeps retrying."""
        sub = self.subscriptions[subscription_id]
        period_end = sub.current_period_end or self.clock()
        return self._set(
            subscription_id, "subscription.past_due", status="past_due", current_period_end=period_end + MONTH
        )

    def recover(self, subscription_id: str) -> Delivery:
        """A retry worked, or the person updated their card."""
        return self._set(subscription_id, "subscription.updated", status="active")

    def finish(self, subscription_id: str) -> Delivery:
        """A cancelled month ran out, or retries ran out: the provider ends the subscription."""
        return self._set(
            subscription_id,
            "subscription.canceled",
            status="canceled",
            cancel_at_period_end=False,
            ended_at=self.clock(),
        )

    def pause(self, subscription_id: str) -> Delivery:
        return self._set(subscription_id, "subscription.paused", status="paused")

    def announce(self, subscription_id: str, event_type: str = "subscription.updated") -> Delivery:
        """Send the subscription as it is now (a change made through the app, which the provider confirms)."""
        return self.deliver(event_type, self.subscriptions[subscription_id])

    def refund(self, subscription_id: str, amount: str = "1500") -> Delivery:
        return self._send(
            "refund.created",
            {"action": "refund", "amount": amount, "currency": "USD", "subscription_id": subscription_id},
        )

    def deliver(self, event_type: str, sub: ProviderSubscription, *, occurred_at: datetime | None = None) -> Delivery:
        data = {
            "id": sub.subscription_id,
            "customer_id": sub.customer_id,
            "status": sub.status,
            "current_period_end": _iso(sub.current_period_end),
            "cancel_at_period_end": sub.cancel_at_period_end,
            "ended_at": _iso(sub.ended_at),
            "custom_data": {"roundturn_user_id": sub.user_ref} if sub.user_ref else None,
        }
        if event_type == "subscription.created" and sub.checkout_id:
            data["checkout_id"] = sub.checkout_id
        return self._send(event_type, data, occurred_at=occurred_at)

    def sign(self, raw_body: bytes) -> dict[str, str]:
        return {
            SIGNATURE_HEADER: signature.sign(self.secret, raw_body, int(self.wall())),
            "Content-Type": "application/json",
        }

    def _set(self, subscription_id: str, event_type: str, **changes: Any) -> Delivery:
        sub = replace(self.subscriptions[subscription_id], **changes)
        self.subscriptions[subscription_id] = sub
        return self.deliver(event_type, sub)

    def _send(self, event_type: str, data: dict[str, Any], *, occurred_at: datetime | None = None) -> Delivery:
        event_id = self._next("evt")
        body = json.dumps(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": _iso(occurred_at or self.clock()),
                "data": data,
            }
        ).encode()
        return Delivery(self.sign(body), body, event_id, event_type)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _subscription_of(data: dict[str, Any]) -> ProviderSubscription:
    def when(key: str) -> datetime | None:
        value = data.get(key)
        return datetime.fromisoformat(value) if value else None

    status: Status = data["status"]
    if status not in STATUSES:
        raise ValueError("unknown status")
    custom = data.get("custom_data") or {}
    return ProviderSubscription(
        subscription_id=str(data["id"]),
        customer_id=data.get("customer_id"),
        status=status,
        current_period_end=when("current_period_end"),
        cancel_at_period_end=bool(data.get("cancel_at_period_end")),
        ended_at=when("ended_at"),
        user_ref=custom.get("roundturn_user_id"),
        checkout_id=data.get("checkout_id"),
    )
