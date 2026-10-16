"""What a payment provider tells us, in our own words. No provider's type crosses this line (the billing design)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Status = Literal["trialing", "active", "past_due", "paused", "canceled"]
STATUSES: tuple[Status, ...] = ("trialing", "active", "past_due", "paused", "canceled")
EventKind = Literal["subscription", "payment", "refund", "unknown"]


@dataclass(frozen=True)
class ProviderSubscription:
    """A subscription as the provider holds it now."""

    subscription_id: str
    customer_id: str | None
    status: Status
    current_period_end: datetime | None  # the end of the period paid for (or of the provider's trial)
    cancel_at_period_end: bool = False  # a cancel is scheduled for the end of the period
    ended_at: datetime | None = None  # when it was canceled, once it is
    user_ref: str | None = None  # the person's id as we put it in the checkout's custom data
    checkout_id: str | None = None  # the checkout it came from, when the provider says (its first event)


@dataclass(frozen=True)
class Refund:
    """Money given back or taken back (a refund, a chargeback). Recorded; it never changes access by itself."""

    action: str  # "refund", "chargeback", ... as the provider names it
    amount: str  # in the currency's smallest unit, as a string ("1500")
    currency: str
    subscription_id: str | None = None


@dataclass(frozen=True)
class BillingEvent:
    """One notification from the provider, checked and read. `payload` is what is stored: ids, statuses, dates and
    amounts, never a name, an email, an address or a card detail."""

    event_id: str
    type: str  # the provider's own name for it, kept for the record
    kind: EventKind
    occurred_at: datetime
    subscription: ProviderSubscription | None = None
    refund: Refund | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscountSpec:
    """A discount code as the provider is asked to hold it: a percentage off Roundturn Pro, for the first payment
    (`once`), the first `months` (`repeating`) or every payment (`forever`), at most `max_uses` people, until `expires_at`."""

    code: str
    percent_off: int
    duration: Literal["once", "repeating", "forever"]
    months: int | None = None
    max_uses: int | None = None
    expires_at: datetime | None = None
    note: str = ""


@dataclass(frozen=True)
class Checkout:
    """A checkout the server opened for a person. `pay_url` is where the browser goes to pay."""

    checkout_id: str
    pay_url: str


class BillingError(Exception):
    """Something the provider did or did not do. `reason` is a short code, safe to log; never a key or a body."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BadSignature(BillingError):
    """A webhook whose signature is missing, wrong or too old. Answered with 401 and nothing stored."""


class ProviderUnavailable(BillingError):
    """A timeout or a server error: nothing is known to have happened, so try again later."""


class ProviderRefused(BillingError):
    """The provider answered no. `detail` is a plain sentence that may be shown to the person."""

    def __init__(self, reason: str, detail: str = "The payment provider refused that. Nothing changed.") -> None:
        super().__init__(reason)
        self.detail = detail
