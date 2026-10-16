"""What the Plan & billing page learns (the billing design, "Routes" and "Screens and emails")."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import ApiModel
from .session import DiscountOut

BillingState = Literal["trial", "covered", "active", "cancelling", "past_due", "read_only"]


class BillingOut(ApiModel):
    state: BillingState = Field(
        description="trial: the free trial runs; covered: an invitation, a code or the team gives Pro; active: paying; "
        "cancelling: paid to the end of the period and not renewing; past_due: the last payment failed and the grace runs; "
        "read_only: nothing gives Pro"
    )
    price: str = Field(description="The monthly price as an exact decimal, such as 15.00")
    currency: str
    interval: Literal["month"]
    price_text: str = Field(description="The price in words, as the page shows it")
    ends_at: datetime | None = Field(
        description="When the trial, the free days, the paid period (cancelling) or the grace (past_due) ends; for "
        "read_only, when access ended"
    )
    renews_at: datetime | None = Field(description="active: when the next payment is taken (the first one, on a trial)")
    first_charge_pending: bool = Field(
        description="active: no payment has been taken yet (the first waits for free days)"
    )
    can_subscribe: bool
    billing_starts_at: datetime | None = Field(
        description="With free days left: when a subscription started now would take its first payment"
    )
    can_manage: bool = Field(
        description="The provider has a customer for this person, so its billing page has something"
    )
    reason: Literal["expired", "revoked", "lapsed"] | None = Field(description="read_only: why")
    discount: DiscountOut | None = Field(
        default=None, description="A discount code saved for the next checkout, while Subscribe is offered"
    )


class CheckoutOut(ApiModel):
    pay_url: str = Field(description="Where the browser goes to pay: a path on this site")


class PortalOut(ApiModel):
    url: str = Field(description="The provider's page for the card, invoices and receipts, made for this click")
