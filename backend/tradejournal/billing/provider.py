"""The connector every payment provider implements (the billing design, "Provider connector"; Paddle guide section 4.1).

The app calls these and nothing else. Every call has a timeout and at most two retries; a provider that does not answer
raises `ProviderUnavailable`, one that says no raises `ProviderRefused`. No call ever logs a key, a request body or an email.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from .types import BillingEvent, Checkout, DiscountSpec, ProviderSubscription


class BillingProvider(Protocol):
    name: str  # "fake" or "paddle": what `subscriptions.provider` records

    def find_or_create_customer(self, *, email: str, user_id: UUID) -> str:
        """The provider's customer for this address, made if there is none. One per person, even after they resubscribe."""
        ...

    def create_checkout(
        self, *, customer_id: str, user_id: UUID, free_days: int, discount_id: str | None = None
    ) -> Checkout:
        """A checkout for Roundturn Pro, monthly, with the person's id in its custom data. `free_days` is how long the first
        charge waits (the person's free days left, rounded up; 0 charges now)."""
        ...

    def pay_url(self, checkout_id: str) -> str:
        """Where the browser pays for a checkout already opened (a second click lands on the same one)."""
        ...

    def portal_url(self, *, customer_id: str, subscription_id: str | None) -> str:
        """A fresh link to the provider's page for the card, invoices and receipts. Never cached."""
        ...

    def cancel(self, subscription_id: str) -> ProviderSubscription:
        """Stop the next renewal; it runs to the end of the period paid for."""
        ...

    def resume(self, subscription_id: str) -> ProviderSubscription:
        """Undo a scheduled cancel, before the period ends."""
        ...

    def cancel_now(self, subscription_id: str) -> ProviderSubscription:
        """End it at once, with no further charge (an account deleted). Ending one already ended is not an error."""
        ...

    def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        """The provider's current answer, for the nightly check."""
        ...

    def create_discount(self, spec: DiscountSpec) -> str:
        """Make the discount at the provider, applicable to Roundturn Pro only, and return its id. The code is the one people
        type in the app; the provider's checkout applies it when a checkout carries it."""
        ...

    def find_discount(self, code: str) -> str | None:
        """The id of an active discount the provider already holds with this code, or None. A retry after a timeout uses
        it, so a code the provider made but never confirmed is adopted rather than made twice."""
        ...

    def archive_discount(self, discount_id: str) -> None:
        """Stop the discount being applied to new checkouts. People already subscribed with it keep it. Archiving one
        already archived is not an error."""
        ...

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> BillingEvent:
        """Check the signature over the raw bytes and read the event, with personal details removed. Raises
        `BadSignature`."""
        ...
