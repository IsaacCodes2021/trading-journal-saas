"""`StripeBilling`: the connector for Stripe Checkout and Stripe Billing (docs/design/stripe-plan.md, Phase 4).

Built beside the Paddle connector, never instead of it: which one runs is `TJ_BILLING_PROVIDER` (`paddle` or `stripe`).
Plain HTTPS with `httpx` (already a dependency; no Stripe SDK, as for Paddle): form-encoded requests, JSON answers, the API
version pinned on every call, and an idempotency key on every POST that its retries reuse, so a retry after a timeout can
make no second customer, checkout or coupon. Every call has a timeout (5 seconds to connect, 10 to read) and is tried at most
three times, again only when Stripe was busy or down (429, 5xx) or said to (`Stripe-Should-Retry`), or the answer never
came. What crosses back is the connector's own types (`types.py`): no Stripe field leaves this module.

Logs say which call, the status and Stripe's request id: never the key, a request body, an email or Stripe's own message.

Facts checked in Northfield Labs' Stripe test mode on 2026-10-23, through Stripe's API (the plan's Phase 3):

- The account's API version is 2026-08-26.dahlia, and Managed Payments (Stripe as merchant of record) is on by default:
  a checkout under it may not name its payment methods, its tax or its customer updates (Stripe answers 400).
- A subscription's period end is on its item (`items.data[0].current_period_end`); during free days it is the trial's end.
- A cancel at the period end sets `cancel_at_period_end`, `cancel_at` and also `canceled_at` while the subscription still
  runs, so its end is read from `ended_at` only. Undoing the cancel clears all three.
- Ending one at once (DELETE) answers the canceled subscription; a second DELETE answers 404 "No such subscription" although
  reading it still shows it canceled.
- Checkout refuses free days that end less than 2 days ahead; a checkout lives 24 hours; its `url` is on checkout.stripe.com.
- A coupon's `duration_in_months` counts from the day it is applied, free days included: "3 months" gives three discounted
  payments when the free days are under a month, fewer when they are longer.
- A refund (`charge.refunded`) names the payment and the customer, not the subscription. Invoice and checkout events carry
  the buyer's email; nothing here keeps it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx

from .types import (
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

logger = logging.getLogger("tj.billing")

API_URL = "https://api.stripe.com"
API_VERSION = "2026-08-26.dahlia"  # the account's version on 2026-10-23; the webhook endpoints must use the same one
CHECKOUT_PAGE = "https://checkout.stripe.com/"
SIGNATURE_HEADER = "Stripe-Signature"
TOLERANCE_SECONDS = 300  # what Stripe's own libraries allow (Paddle's five seconds stay Paddle's, signature.py)
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
RETRIES = 2  # after the first try
MAX_FREE_DAYS = 365  # a first charge further away than this is refused rather than guessed at
MIN_TRIAL_DAYS = 2  # Checkout refuses free days that end sooner: one day left is given as two
BUSY = frozenset({429, 500, 502, 503, 504})

# The events a webhook endpoint sends (docs/runbooks/billing.md). `customer.*` is left out: it carries names and emails.
EVENTS = (
    "checkout.session.completed",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
    "invoice.paid",
    "invoice.payment_failed",
    "charge.refunded",
    "charge.dispute.created",
)

_KEY = re.compile(r"(?:sk|rk)_(test|live)_[A-Za-z0-9]{10,250}")
_WEBHOOK_SECRET = re.compile(r"whsec_[A-Za-z0-9+/=]{16,250}")
_STRIPE_ID = re.compile(r"(cus|sub|promo|evt|in|ch|pi|re|dp|price|prod|bps|bpc|we)_[A-Za-z0-9]{6,250}")
_SESSION_ID = re.compile(r"cs_(?:test|live)_[A-Za-z0-9]{10,250}")
_COUPON_ID = re.compile(r"rt-[a-z0-9]{3,20}")
_ERROR_CODE = re.compile(r"[a-z0-9_]{1,64}")
_REQUEST_ID = re.compile(r"req_[A-Za-z0-9]{1,64}")

# Stripe's subscription statuses in Roundturn's words. `incomplete` (a first payment that needs the buyer) is not here: it
# gives nothing, and Checkout makes the subscription only once the first payment or the free days are settled.
_STATUSES: dict[str, Status] = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "past_due",  # retries end in a cancel (the dashboard setting), so this should not be seen
    "paused": "paused",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
}

Environment = Literal["test", "live"]
_TRUE, _FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


@dataclass(frozen=True)
class StripeSettings:
    """`TJ_STRIPE_*`. The web server needs the webhook's secret and the catalog ids; a scheduled job (the sweep) needs only
    the environment and the key. Every value is checked here, so a mixed or mistyped set stops the process at start-up,
    before anyone is charged. `managed_payments` says whether Stripe sells as merchant of record (the account's default):
    each checkout states it, so the dashboard's default never decides it silently."""

    environment: Environment
    api_key: str = field(repr=False)
    webhook_secret: str | None = field(default=None, repr=False)
    product_id: str | None = None
    price_id: str | None = None
    managed_payments: bool = True

    def __post_init__(self) -> None:
        if self.environment not in ("test", "live"):
            raise ValueError("TJ_STRIPE_ENV must be 'test' or 'live'")
        key = _KEY.fullmatch(self.api_key or "")
        if key is None:
            raise ValueError("TJ_STRIPE_API_KEY must be a secret key (sk_...) or a restricted key (rk_...)")
        if key.group(1) != self.environment:
            raise ValueError(f"TJ_STRIPE_API_KEY is a {key.group(1)} key, but TJ_STRIPE_ENV is {self.environment}")
        if self.webhook_secret is not None and not _WEBHOOK_SECRET.fullmatch(self.webhook_secret):
            raise ValueError("TJ_STRIPE_WEBHOOK_SECRET must be the webhook endpoint's signing secret (whsec_...)")
        if self.product_id is not None and not re.fullmatch(r"prod_[A-Za-z0-9]{6,250}", self.product_id):
            raise ValueError("TJ_STRIPE_PRODUCT_ID must be a product id (prod_...)")
        if self.price_id is not None and not re.fullmatch(r"price_[A-Za-z0-9]{6,250}", self.price_id):
            raise ValueError("TJ_STRIPE_PRICE_ID must be a price id (price_...)")

    @property
    def livemode(self) -> bool:
        return self.environment == "live"

    @property
    def complete_for_web(self) -> bool:
        """What the web server needs: the webhook's secret and the catalog ids."""
        return None not in (self.webhook_secret, self.product_id, self.price_id)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> StripeSettings | None:
        """The settings, or None when none is given. Raises ValueError (naming the setting, never its value)."""
        names = ("TJ_STRIPE_ENV", "TJ_STRIPE_API_KEY", "TJ_STRIPE_WEBHOOK_SECRET", "TJ_STRIPE_PRODUCT_ID")
        names += ("TJ_STRIPE_PRICE_ID", "TJ_STRIPE_MANAGED_PAYMENTS")
        given = {name: env[name].strip() for name in names if env.get(name, "").strip()}
        if not given:
            return None
        if "TJ_STRIPE_ENV" not in given or "TJ_STRIPE_API_KEY" not in given:
            raise ValueError("TJ_STRIPE_ENV and TJ_STRIPE_API_KEY are needed with any TJ_STRIPE_* setting")
        managed = given.get("TJ_STRIPE_MANAGED_PAYMENTS", "true").lower()
        if managed not in _TRUE + _FALSE:
            raise ValueError("TJ_STRIPE_MANAGED_PAYMENTS must be true or false")
        return cls(
            environment=given["TJ_STRIPE_ENV"].lower(),  # type: ignore[arg-type]
            api_key=given["TJ_STRIPE_API_KEY"],
            webhook_secret=given.get("TJ_STRIPE_WEBHOOK_SECRET"),
            product_id=given.get("TJ_STRIPE_PRODUCT_ID"),
            price_id=given.get("TJ_STRIPE_PRICE_ID"),
            managed_payments=managed in _TRUE,
        )


class StripeRefused(ProviderRefused):
    """Stripe answered with a 4xx. `code` is Stripe's short error code (safe to log); its message is never read."""

    def __init__(self, reason: str, *, status: int, code: str) -> None:
        super().__init__(reason)
        self.status = status
        self.code = code


class StripeBilling:
    name = "stripe"

    def __init__(
        self,
        settings: StripeSettings,
        *,
        app_origin: str | None = None,
        http: httpx.Client | None = None,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        new_key: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.settings = settings
        self._origin = _origin(app_origin)
        self._http = http or httpx.Client(timeout=TIMEOUT)
        self._wall = wall
        self._sleep = sleep
        self._new_key = new_key

    # ---- the interface (provider.py) ------------------------------------------------------------------------------------

    def find_or_create_customer(self, *, email: str, user_id: UUID) -> str:
        """One customer per person, found by the person's id in its metadata, else made. The idempotency key is the person's
        id, so a retry after a timeout (or a second click while the first is on its way) gets the same customer back."""
        found = self._call(
            "find_customer",
            "GET",
            "/v1/customers/search",
            params={"query": f"metadata['roundturn_user_id']:'{user_id}'", "limit": "1"},
        )
        for customer in _data(found):
            return self._id(customer, "cus")
        made = self._call(
            "create_customer",
            "POST",
            "/v1/customers",
            form={"email": email, "metadata": {"roundturn_user_id": str(user_id)}},
            key=f"rt-customer-{user_id}",
        )
        return self._id(made, "cus")

    def create_checkout(
        self, *, customer_id: str, user_id: UUID, free_days: int, discount_id: str | None = None
    ) -> Checkout:
        """A Checkout Session for Roundturn Pro, made on the server, with the person's id on the session and on the
        subscription it makes (Stripe copies the subscription's metadata into every event about it). With free days left the
        first charge waits for them; the card is asked for now. The person's other open checkouts are closed first, so two
        cannot both be paid."""
        price_id = self.settings.price_id
        if price_id is None or self._origin is None:
            raise ProviderUnavailable("stripe_not_configured")
        if free_days > MAX_FREE_DAYS:
            raise ProviderRefused(
                "stripe_free_days_too_long",
                "You have more than a year of free days left. Subscribe closer to the day they end.",
            )
        customer = self._checked(customer_id, "cus")
        self._close_open_checkouts(customer)
        person = {"roundturn_user_id": str(user_id)}
        subscription: dict[str, Any] = {"metadata": person}
        if free_days > 0:
            subscription["trial_period_days"] = max(free_days, MIN_TRIAL_DAYS)
        form: dict[str, Any] = {
            "mode": "subscription",
            "customer": customer,
            "client_reference_id": str(user_id),
            "line_items": [{"price": price_id, "quantity": 1}],
            "subscription_data": subscription,
            "metadata": person,
            "success_url": f"{self._origin}/settings/plan?checkout=done",
            "cancel_url": f"{self._origin}/settings/plan",
            "managed_payments": {"enabled": self.settings.managed_payments},
        }
        if discount_id is not None:
            form["discounts"] = [{"promotion_code": self._checked(discount_id, "promo")}]
        if not self.settings.managed_payments:  # Roundturn is the seller: Stripe Tax, the address it needs, cards only
            form["automatic_tax"] = {"enabled": True}
            form["billing_address_collection"] = "required"
            form["customer_update"] = {"address": "auto", "name": "auto"}
            form["allowed_payment_method_types"] = ["card"]
        session = self._call("create_checkout", "POST", "/v1/checkout/sessions", form=form)
        checkout_id = self._session_id(session)
        return Checkout(checkout_id, self.pay_url(checkout_id))

    def pay_url(self, checkout_id: str) -> str:
        """Roundturn's own address for a checkout (routers/stripe_pay.py), which sends the browser on to Stripe's page."""
        return f"/billing/stripe/pay?cs={self._checked_session(checkout_id)}"

    def portal_url(self, *, customer_id: str, subscription_id: str | None) -> str:
        """A fresh link to Stripe's customer portal (card, invoices, cancel), with the account's default portal settings.
        Made per click; the subscription is not named, as the portal shows the customer's."""
        if self._origin is None:
            raise ProviderUnavailable("stripe_not_configured")
        data = self._call(
            "portal_session",
            "POST",
            "/v1/billing_portal/sessions",
            form={"customer": self._checked(customer_id, "cus"), "return_url": f"{self._origin}/settings/plan"},
        )
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ProviderUnavailable("stripe_portal_session_malformed")
        return url

    def cancel(self, subscription_id: str) -> ProviderSubscription:
        """Cancel at the end of the period paid for (or of the free days)."""
        return self._sub(
            "cancel",
            "POST",
            f"/v1/subscriptions/{self._checked(subscription_id, 'sub')}",
            {"cancel_at_period_end": True},
        )

    def resume(self, subscription_id: str) -> ProviderSubscription:
        """Undo a scheduled cancel. One that has ended cannot come back (Stripe refuses; subscribe again)."""
        return self._sub(
            "resume",
            "POST",
            f"/v1/subscriptions/{self._checked(subscription_id, 'sub')}",
            {"cancel_at_period_end": False},
        )

    def cancel_now(self, subscription_id: str) -> ProviderSubscription:
        """End it at once (an account deleted). Stripe answers a second end with 404, so a refusal is read back: one that has
        already ended counts as done."""
        sub = self._checked(subscription_id, "sub")
        try:
            return self._sub("cancel_now", "DELETE", f"/v1/subscriptions/{sub}", None)
        except StripeRefused:
            current = self.fetch_subscription(sub)
            if current.status == "canceled":
                return current
            raise

    def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        return self._sub(
            "fetch_subscription", "GET", f"/v1/subscriptions/{self._checked(subscription_id, 'sub')}", None
        )

    def create_discount(self, spec: DiscountSpec) -> str:
        """A coupon (the terms) and its promotion code (the code people type in the app), for Roundturn Pro only. The coupon's
        id is the code's own (`rt-launch20`), so a retry after a timeout finds the coupon it made instead of a second one;
        a coupon already there with other terms is refused rather than reused."""
        product = self.settings.product_id
        if product is None:
            raise ProviderUnavailable("stripe_not_configured")
        coupon_id = f"rt-{spec.code.lower()}"
        if not _COUPON_ID.fullmatch(coupon_id):
            raise ProviderRefused("stripe_code_malformed", "A discount code is 3 to 20 letters and digits.")
        coupon: dict[str, Any] = {
            "id": coupon_id,
            "name": spec.code.upper()[:40],
            "percent_off": spec.percent_off,
            "duration": spec.duration,
            "applies_to": {"products": [product]},
            "max_redemptions": spec.max_uses,
            "redeem_by": _unix(spec.expires_at),
            "metadata": {"roundturn": "discount"},
        }
        if spec.duration == "repeating":
            coupon["duration_in_months"] = spec.months
        try:
            self._call("create_coupon", "POST", "/v1/coupons", form=coupon)  # the id, not a key, makes it once
        except StripeRefused as problem:
            if problem.code != "resource_already_exists":
                raise
            held = self._call("find_coupon", "GET", f"/v1/coupons/{coupon_id}")
            if not _same_terms(held, coupon):
                raise ProviderRefused(
                    "stripe_coupon_conflict", "Stripe already holds this code with other terms. Use another code."
                ) from None
        promotion = {
            "promotion": {"type": "coupon", "coupon": coupon_id},
            "code": spec.code.upper(),
            "max_redemptions": spec.max_uses,
            "expires_at": _unix(spec.expires_at),
            "metadata": {"roundturn": "discount"},
        }
        made = self._call("create_promotion_code", "POST", "/v1/promotion_codes", form=promotion)
        return self._id(made, "promo")

    def find_discount(self, code: str) -> str | None:
        """The active promotion code with this code (Stripe's lookup ignores letter case), or None."""
        listed = self._call(
            "find_discount", "GET", "/v1/promotion_codes", params={"code": code, "active": "true", "limit": "10"}
        )
        for promotion in _data(listed):
            if str(promotion.get("code", "")).upper() == code.upper() and promotion.get("active") is True:
                return self._id(promotion, "promo")
        return None

    def archive_discount(self, discount_id: str) -> None:
        """Turn the promotion code off: no new checkout takes it; people who subscribed with it keep their discount."""
        self._call(
            "archive_discount",
            "POST",
            f"/v1/promotion_codes/{self._checked(discount_id, 'promo')}",
            form={"active": False},
        )

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> BillingEvent:
        secret = self.settings.webhook_secret
        header = next((v for k, v in headers.items() if k.lower() == SIGNATURE_HEADER.lower()), None)
        if not secret or not verify_signature(header, raw_body, secret, now=int(self._wall())):
            raise BadSignature("signature")
        try:
            body = json.loads(raw_body)
            event_id = str(body["id"])
            event_type = str(body["type"])
            created = body["created"]
            livemode = body["livemode"]
            data = body["data"]["object"]
            if (
                not _STRIPE_ID.fullmatch(event_id)
                or not event_id.startswith("evt_")
                or not isinstance(created, int)
                or not isinstance(livemode, bool)
                or not isinstance(data, dict)
            ):
                raise ValueError("envelope")
        except (ValueError, KeyError, TypeError):
            raise BadSignature("malformed") from None
        occurred = datetime.fromtimestamp(created, UTC)
        if livemode != self.settings.livemode:
            # A key or a secret of the other mode: stored and logged, never applied (a test payment grants no Pro).
            logger.warning("stripe event of the other mode ignored (%s)", event_type)
            payload = {"event_type": event_type, "livemode": livemode, "ignored": "other_mode"}
            return BillingEvent(event_id, event_type, "unknown", occurred, payload=payload)
        return read_event(event_id, event_type, occurred, data)

    # ---- for the payment link and the readiness check -------------------------------------------------------------------

    def checkout_destination(self, checkout_id: str, user_id: UUID) -> str:
        """Where the browser goes for a checkout: Stripe's page while it is open and is this person's, back to Plan & billing
        once it is paid (with the flag that makes the page wait for Stripe's word), else Plan & billing."""
        session = self._call("fetch_checkout", "GET", f"/v1/checkout/sessions/{self._checked_session(checkout_id)}")
        if session.get("client_reference_id") != str(user_id):
            return "/settings/plan"
        status, url = session.get("status"), session.get("url")
        if status == "open" and isinstance(url, str) and url.startswith(CHECKOUT_PAGE):
            return url
        if status == "complete":
            return "/settings/plan?checkout=done"
        return "/settings/plan"

    def read(self, what: str, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """A GET for the readiness check (billing/stripe_check.py)."""
        return self._call(what, "GET", path, params=params)

    # ---- talking to Stripe ----------------------------------------------------------------------------------------------

    def _close_open_checkouts(self, customer: str) -> None:
        listed = self._call(
            "list_open_checkouts",
            "GET",
            "/v1/checkout/sessions",
            params={"customer": customer, "status": "open", "limit": "10"},
        )
        for session in _data(listed):
            checkout_id = session.get("id")
            if not isinstance(checkout_id, str) or not _SESSION_ID.fullmatch(checkout_id):
                continue
            try:
                self._call("expire_checkout", "POST", f"/v1/checkout/sessions/{checkout_id}/expire", form={})
            except StripeRefused:
                pass  # paid or expired in the meantime: nothing is left open

    def _sub(self, what: str, method: str, path: str, form: dict[str, Any] | None) -> ProviderSubscription:
        try:
            return subscription_from_stripe(self._call(what, method, path, form=form))
        except (KeyError, ValueError, TypeError):
            raise ProviderUnavailable(f"stripe_{what}_malformed") from None

    def _call(
        self,
        what: str,
        method: str,
        path: str,
        *,
        form: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        """One API call; its answer. Raises ProviderUnavailable (busy, down, no answer, a key Stripe refuses, an answer it
        cannot read) or StripeRefused (any other 4xx). Every try of one POST carries the same idempotency key, so Stripe acts
        once whatever the network did; a DELETE that went through answers the next try with 404, which the caller reads
        back."""
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Stripe-Version": API_VERSION}
        content = None
        if method == "POST":
            headers["Idempotency-Key"] = key or self._new_key()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            content = urlencode(form_pairs(form or {})).encode()
        url = API_URL + path
        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                response = self._http.request(method, url, content=content, params=params, headers=headers)
            except httpx.TimeoutException:  # no answer: the idempotency key makes another try safe
                if last:
                    raise ProviderUnavailable(f"stripe_{what}_timeout") from None
                self._sleep(0.5 * 2**attempt)
                continue
            except httpx.TransportError:
                if last:
                    raise ProviderUnavailable(f"stripe_{what}_network") from None
                self._sleep(0.5 * 2**attempt)
                continue
            except httpx.HTTPError:
                raise ProviderUnavailable(f"stripe_{what}_network") from None
            status = response.status_code
            request_id = response.headers.get("request-id", "")
            request_id = request_id if _REQUEST_ID.fullmatch(request_id) else "-"
            logger.info("stripe %s: %s (request %s)", what, status, request_id)
            should_retry = response.headers.get("stripe-should-retry", "").lower()
            if not last and should_retry != "false" and (status in BUSY or should_retry == "true"):
                self._sleep(_retry_after(response, attempt))
                continue
            parsed = _json(response)
            if 200 <= status < 300:
                if not isinstance(parsed, dict):
                    raise ProviderUnavailable(f"stripe_{what}_malformed")
                return parsed
            if status in BUSY:
                raise ProviderUnavailable(f"stripe_{what}_{status}")
            if status in (401, 403):  # the key or its permissions: never the person's doing
                logger.error("stripe %s refused the API key (%s, request %s)", what, status, request_id)
                raise ProviderUnavailable(f"stripe_{what}_{status}")
            error = parsed.get("error") if isinstance(parsed, dict) else None
            code = str(error.get("code") or error.get("type") or "") if isinstance(error, dict) else ""
            code = code if _ERROR_CODE.fullmatch(code) else "unknown"
            raise StripeRefused(f"stripe_{what}_{code}", status=status, code=code)
        raise ProviderUnavailable(f"stripe_{what}_retries")  # not reached: the last try returns or raises

    @staticmethod
    def _id(data: Any, prefix: str) -> str:
        value = data.get("id") if isinstance(data, dict) else None
        if not isinstance(value, str) or not value.startswith(prefix + "_") or not _STRIPE_ID.fullmatch(value):
            raise ProviderUnavailable(f"stripe_{prefix}_id_malformed")
        return value

    @staticmethod
    def _session_id(data: Any) -> str:
        value = data.get("id") if isinstance(data, dict) else None
        if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
            raise ProviderUnavailable("stripe_cs_id_malformed")
        return value

    @staticmethod
    def _checked(stripe_id: str, prefix: str) -> str:
        """An id going into a path or a body: only Stripe's own shape, so nothing stored can turn into another request."""
        if (
            not isinstance(stripe_id, str)
            or not stripe_id.startswith(prefix + "_")
            or not _STRIPE_ID.fullmatch(stripe_id)
        ):
            raise ProviderRefused("stripe_id_malformed")
        return stripe_id

    @staticmethod
    def _checked_session(checkout_id: str) -> str:
        if not isinstance(checkout_id, str) or not _SESSION_ID.fullmatch(checkout_id):
            raise ProviderRefused("stripe_id_malformed")
        return checkout_id


# ---- the webhook signature -------------------------------------------------------------------------------------------


def sign(secret: str, raw_body: bytes, ts: int) -> str:
    """`t=<unix seconds>,v1=<hex>`: HMAC-SHA256 of "<t>.<raw body>" under the endpoint's whole secret (whsec_...)."""
    digest = hmac.new(secret.encode(), str(ts).encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(
    header: str | None, raw_body: bytes, secret: str, *, now: int, tolerance: int = TOLERANCE_SECONDS
) -> bool:
    """True when one of the header's v1 values is the body's signature and its time is within `tolerance` of `now`. While a
    secret is being rolled Stripe sends one v1 per secret; any one that matches is enough."""
    if not header or not secret or len(header) > 2000:
        return False
    pairs = [part.split("=", 1) for part in header.split(",") if "=" in part]
    ts = next((value.strip() for key, value in pairs if key.strip() == "t"), "")
    signatures = [value.strip() for key, value in pairs if key.strip() == "v1"]
    if not ts.isdigit() or len(ts) > 12 or abs(now - int(ts)) > tolerance or not signatures:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected.encode(), candidate.encode()) for candidate in signatures)


# ---- what Stripe says, in the connector's words ----------------------------------------------------------------------

# What is kept of each event (billing_events.payload): ids, statuses, dates and amounts, never a name, an email or an address.
_KEPT_SUBSCRIPTION = ("id", "status", "customer", "cancel_at_period_end", "cancel_at", "canceled_at", "ended_at")
_KEPT_SUBSCRIPTION += ("trial_end",)
_KEPT_CHECKOUT = ("id", "status", "mode", "payment_status", "subscription", "customer", "client_reference_id")
_KEPT_CHECKOUT += ("amount_total", "currency")
_KEPT_INVOICE = ("id", "status", "billing_reason", "amount_due", "amount_paid", "currency", "attempt_count", "customer")
_KEPT_CHARGE = ("id", "amount", "amount_refunded", "currency", "customer", "payment_intent")
_KEPT_DISPUTE = ("id", "amount", "currency", "charge", "reason", "status")


def read_event(event_id: str, event_type: str, occurred: datetime, data: dict[str, Any]) -> BillingEvent:
    """A signed Stripe event read into a `BillingEvent`. A signed event of a shape this does not read is kept as `unknown`:
    acknowledged, stored, nothing applied. Stripe's `created` is whole seconds: two events of one second are applied in the
    order they arrive, and the nightly check corrects the rare pair that arrives reversed."""
    if event_type.startswith("customer.subscription."):
        kept: dict[str, Any] = {k: _scalar(data.get(k)) for k in _KEPT_SUBSCRIPTION if k in data}
        kept["current_period_end"] = _period_end(data)
        user_ref = _user_ref(data)
        if user_ref is not None:
            kept["roundturn_user_id"] = user_ref
        try:
            subscription = subscription_from_stripe(data)
        except (KeyError, ValueError, TypeError):
            return BillingEvent(event_id, event_type, "unknown", occurred, payload=_envelope(event_type, kept))
        return BillingEvent(
            event_id,
            event_type,
            "subscription",
            occurred,
            subscription=subscription,
            payload=_envelope(event_type, kept),
        )
    if event_type.startswith("checkout.session."):
        kept = {k: _scalar(data.get(k)) for k in _KEPT_CHECKOUT if k in data}
        return BillingEvent(event_id, event_type, "payment", occurred, payload=_envelope(event_type, kept))
    if event_type.startswith("invoice."):
        kept = {k: _scalar(data.get(k)) for k in _KEPT_INVOICE if k in data}
        kept["subscription"] = _invoice_subscription(data)
        return BillingEvent(event_id, event_type, "payment", occurred, payload=_envelope(event_type, kept))
    if event_type == "charge.refunded":
        kept = {k: _scalar(data.get(k)) for k in _KEPT_CHARGE if k in data}
        refund = Refund(
            action="refund",
            amount=str(data.get("amount_refunded", 0)),
            currency=str(data.get("currency", "usd")).upper(),
        )
        return BillingEvent(
            event_id, event_type, "refund", occurred, refund=refund, payload=_envelope(event_type, kept)
        )
    if event_type == "charge.dispute.created":
        kept = {k: _scalar(data.get(k)) for k in _KEPT_DISPUTE if k in data}
        refund = Refund(
            action="chargeback", amount=str(data.get("amount", 0)), currency=str(data.get("currency", "usd")).upper()
        )
        return BillingEvent(
            event_id, event_type, "refund", occurred, refund=refund, payload=_envelope(event_type, kept)
        )
    return BillingEvent(event_id, event_type, "unknown", occurred, payload={"event_type": event_type})


def subscription_from_stripe(data: Mapping[str, Any]) -> ProviderSubscription:
    """Stripe's subscription in the connector's words. A cancel at the period end is `cancel_at_period_end` (or a
    `cancel_at` from the portal); it ends at `cancel_at`. An ended one has no period, as with Paddle. `canceled_at` is not
    the end: Stripe sets it when the cancel is asked for."""
    status = _STATUSES.get(data["status"])
    if status is None:
        raise ValueError("a status that gives nothing yet")
    identifier = data["id"]
    if not isinstance(identifier, str) or not identifier.startswith("sub_") or not _STRIPE_ID.fullmatch(identifier):
        raise ValueError("not a subscription id")
    cancel_at = _from_unix(data.get("cancel_at"))
    cancelling = status != "canceled" and (bool(data.get("cancel_at_period_end")) or cancel_at is not None)
    if status == "canceled":
        end = None
    elif cancelling and cancel_at is not None:
        end = cancel_at
    else:
        end = _from_unix(_period_end(data))
    customer = data.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    return ProviderSubscription(
        subscription_id=identifier,
        customer_id=customer if isinstance(customer, str) else None,
        status=status,
        current_period_end=end,
        cancel_at_period_end=cancelling,
        ended_at=_from_unix(data.get("ended_at")),
        user_ref=_user_ref(data),
        checkout_id=None,  # a subscription does not name its checkout; the person's id in its metadata places it
    )


def form_pairs(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """A nested value as Stripe's form fields: {"line_items": [{"price": "p"}]} -> [("line_items[0][price]", "p")]. True
    and False are "true" and "false"; None leaves the field out."""
    if isinstance(value, dict):
        pairs: list[tuple[str, str]] = []
        for key, item in value.items():
            pairs += form_pairs(item, f"{prefix}[{key}]" if prefix else str(key))
        return pairs
    if isinstance(value, list | tuple):
        pairs = []
        for index, item in enumerate(value):
            pairs += form_pairs(item, f"{prefix}[{index}]")
        return pairs
    if value is None:
        return []
    if isinstance(value, bool):
        return [(prefix, "true" if value else "false")]
    return [(prefix, str(value))]


def _period_end(data: Mapping[str, Any]) -> int | None:
    items = data.get("items")
    rows = items.get("data") if isinstance(items, dict) else None
    if (
        isinstance(rows, list)
        and rows
        and isinstance(rows[0], dict)
        and isinstance(rows[0].get("current_period_end"), int)
    ):
        return rows[0]["current_period_end"]
    trial_end = data.get("trial_end")
    return trial_end if isinstance(trial_end, int) else None


def _user_ref(data: Mapping[str, Any]) -> str | None:
    metadata = data.get("metadata")
    value = metadata.get("roundturn_user_id") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) else None


def _invoice_subscription(data: Mapping[str, Any]) -> str | None:
    parent = data.get("parent")
    details = parent.get("subscription_details") if isinstance(parent, dict) else None
    value = details.get("subscription") if isinstance(details, dict) else None
    return value if isinstance(value, str) else None


def _scalar(value: Any) -> Any:
    """A kept field: an expanded object is kept as its id only."""
    return value.get("id") if isinstance(value, dict) else value


def _same_terms(held: Mapping[str, Any], wanted: Mapping[str, Any]) -> bool:
    keys = ("percent_off", "duration", "duration_in_months", "max_redemptions", "redeem_by")
    return all(_number(held.get(k)) == _number(wanted.get(k)) for k in keys)


def _number(value: Any) -> Any:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else value


def _envelope(event_type: str, kept: dict[str, Any]) -> dict[str, Any]:
    return {"event_type": event_type, "data": kept}


def _data(listed: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = listed.get("data")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _from_unix(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("not a time")
    return datetime.fromtimestamp(value, UTC)


def _unix(moment: datetime | None) -> int | None:
    return int(moment.timestamp()) if moment is not None else None


def _origin(app_origin: str | None) -> str | None:
    if app_origin is None:
        return None
    parts = urlsplit(app_origin)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/"):
        raise ValueError("the app origin must look like https://app.example.com")
    return f"{parts.scheme}://{parts.netloc}"


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        wanted = float(response.headers.get("retry-after", ""))
    except ValueError:
        wanted = 0.5 * 2**attempt
    return max(0.0, min(wanted, 2.0))
