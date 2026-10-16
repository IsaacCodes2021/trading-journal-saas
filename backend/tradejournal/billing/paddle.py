"""`PaddleBilling`: the connector for Paddle Billing (docs/design/paddle-integration.md, sections 4 and 5).

Plain JSON over HTTPS with `httpx` (already a dependency); no Paddle SDK. Every call has a timeout (5 seconds to connect,
10 to read) and is tried at most three times, again only when Paddle said it was busy or down (429, 5xx) or the request
never left (a connection that failed); a read that timed out is not repeated, because Paddle may have acted. What crosses
back is the connector's own types (`types.py`): no Paddle field leaves this module.

Logs say which call, the status and Paddle's request id: never the API key, a request body, an email or Paddle's own
message (it can name a customer). Settings come from `PaddleSettings`, which refuses a sandbox key with live settings and
the reverse, the most common Paddle mistake.

Facts checked against Paddle's developer docs (the paddle-docs MCP server) on 2026-10-16: the webhook envelope (`event_id`,
`event_type`, `occurred_at`, `data`), the subscription fields (`current_billing_period`, `scheduled_change`, `canceled_at`,
`custom_data`, `transaction_id` on `subscription.created`), `customer_already_exists` (409, the existing id in its detail),
a transaction's non-catalog price with `trial_period`, portal sessions (`urls.general.overview`) and adjustments.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import httpx

from . import price, signature
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
)

logger = logging.getLogger("tj.billing")

SANDBOX_API = "https://sandbox-api.paddle.com"
LIVE_API = "https://api.paddle.com"
SIGNATURE_HEADER = "Paddle-Signature"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
RETRIES = 2  # after the first try
MAX_FREE_DAYS = 365  # a first charge further away than this is refused rather than guessed at
BUSY = frozenset({429, 500, 502, 503, 504})

_PRODUCT = re.compile(r"pro_[a-z0-9]{10,64}")
_PRICE = re.compile(r"pri_[a-z0-9]{10,64}")
_PADDLE_ID = re.compile(r"(?:ctm|sub|txn|dsc)_[a-z0-9]{10,64}")
_CUSTOMER_IN_DETAIL = re.compile(r"\bctm_[a-z0-9]{10,64}\b")
_ERROR_CODE = re.compile(r"[a-z0-9_]{1,64}")

Environment = Literal["sandbox", "production"]


@dataclass(frozen=True)
class PaddleSettings:
    """`TJ_PADDLE_*`. The web server needs all of them; a scheduled job (the sweep) needs only the environment and the key.
    Every value is checked here, so a mixed or mistyped set stops the process at start-up, before anyone is charged."""

    environment: Environment
    api_key: str = field(repr=False)
    webhook_secret: str | None = field(default=None, repr=False)
    client_token: str | None = None
    product_id: str | None = None
    price_id: str | None = None

    def __post_init__(self) -> None:
        if self.environment not in ("sandbox", "production"):
            raise ValueError("TJ_PADDLE_ENV must be 'sandbox' or 'production'")
        sandbox_key = "_sdbx" in self.api_key
        if not self.api_key or len(self.api_key) > 200:
            raise ValueError("TJ_PADDLE_API_KEY is missing")
        if self.environment == "sandbox" and not sandbox_key:
            raise ValueError("TJ_PADDLE_API_KEY is a live key, but TJ_PADDLE_ENV is sandbox")
        if self.environment == "production" and sandbox_key:
            raise ValueError("TJ_PADDLE_API_KEY is a sandbox key, but TJ_PADDLE_ENV is production")
        if self.webhook_secret is not None and not self.webhook_secret.startswith("pdl_ntfset_"):
            raise ValueError(
                "TJ_PADDLE_WEBHOOK_SECRET must be the notification destination's secret key (pdl_ntfset_...)"
            )
        if self.client_token is not None:
            wanted = "test_" if self.environment == "sandbox" else "live_"
            if not self.client_token.startswith(wanted):
                raise ValueError(
                    f"TJ_PADDLE_CLIENT_TOKEN must start with {wanted} for TJ_PADDLE_ENV={self.environment}"
                )
            if not re.fullmatch(r"[A-Za-z0-9_]{6,200}", self.client_token):
                raise ValueError("TJ_PADDLE_CLIENT_TOKEN has characters a client-side token never has")
        if self.product_id is not None and not _PRODUCT.fullmatch(self.product_id):
            raise ValueError("TJ_PADDLE_PRODUCT_ID must be a product id (pro_...)")
        if self.price_id is not None and not _PRICE.fullmatch(self.price_id):
            raise ValueError("TJ_PADDLE_PRICE_ID must be a price id (pri_...)")

    @property
    def api_url(self) -> str:
        return SANDBOX_API if self.environment == "sandbox" else LIVE_API

    @property
    def complete_for_web(self) -> bool:
        """What the web server needs: the webhook's secret, the payment page's token, and the catalog ids."""
        return None not in (self.webhook_secret, self.client_token, self.product_id, self.price_id)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PaddleSettings | None:
        """The settings, or None when none is given. Raises ValueError (naming the setting, never its value)."""
        names = ("TJ_PADDLE_ENV", "TJ_PADDLE_API_KEY", "TJ_PADDLE_WEBHOOK_SECRET", "TJ_PADDLE_CLIENT_TOKEN")
        names += ("TJ_PADDLE_PRODUCT_ID", "TJ_PADDLE_PRICE_ID")
        given = {name: env[name].strip() for name in names if env.get(name, "").strip()}
        if not given:
            return None
        if "TJ_PADDLE_ENV" not in given or "TJ_PADDLE_API_KEY" not in given:
            raise ValueError("TJ_PADDLE_ENV and TJ_PADDLE_API_KEY are needed with any TJ_PADDLE_* setting")
        return cls(
            environment=given["TJ_PADDLE_ENV"].lower(),  # type: ignore[arg-type]
            api_key=given["TJ_PADDLE_API_KEY"],
            webhook_secret=given.get("TJ_PADDLE_WEBHOOK_SECRET"),
            client_token=given.get("TJ_PADDLE_CLIENT_TOKEN"),
            product_id=given.get("TJ_PADDLE_PRODUCT_ID"),
            price_id=given.get("TJ_PADDLE_PRICE_ID"),
        )


class PaddleRefused(ProviderRefused):
    """Paddle answered with a 4xx. `code` is Paddle's short error code (safe to log); `paddle_detail` is its sentence,
    which is read here (an existing customer's id is in it) and never logged or shown."""

    def __init__(self, reason: str, *, status: int, code: str, paddle_detail: str) -> None:
        super().__init__(reason)
        self.status = status
        self.code = code
        self.paddle_detail = paddle_detail


class PaddleBilling:
    name = "paddle"

    def __init__(
        self,
        settings: PaddleSettings,
        *,
        http: httpx.Client | None = None,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._http = http or httpx.Client(timeout=TIMEOUT)
        self._wall = wall
        self._sleep = sleep

    # ---- the interface (provider.py) ------------------------------------------------------------------------------------

    def find_or_create_customer(self, *, email: str, user_id: UUID) -> str:
        """Paddle keeps one customer per address. A person who already has one (an earlier subscription, or a checkout
        that made it) gets that one back: Paddle names it in its 409, or it is looked up by the address."""
        try:
            data = self._call(
                "create_customer",
                "POST",
                "/customers",
                body={"email": email, "custom_data": {"roundturn_user_id": str(user_id)}},
            )
            return self._id(data, "ctm")
        except PaddleRefused as problem:
            if problem.code != "customer_already_exists":
                raise
            named = _CUSTOMER_IN_DETAIL.search(problem.paddle_detail)
            if named:
                return named.group(0)
        listed = self._call("find_customer", "GET", "/customers", params={"email": email})
        for customer in listed if isinstance(listed, list) else []:
            if isinstance(customer, dict) and str(customer.get("email", "")).casefold() == email.casefold():
                return self._id(customer, "ctm")
        raise ProviderRefused("paddle_customer_not_found")

    def create_checkout(
        self, *, customer_id: str, user_id: UUID, free_days: int, discount_id: str | None = None
    ) -> Checkout:
        """A transaction for Roundturn Pro, made on the server, with the person's id in its custom data (Paddle copies it to
        the subscription and every renewal). With free days left, a price of the same product whose trial is those days, so
        the first charge waits for them; the card is asked for now."""
        product, price_id = self.settings.product_id, self.settings.price_id
        if product is None or price_id is None:
            raise ProviderUnavailable("paddle_not_configured")
        if free_days > MAX_FREE_DAYS:
            raise ProviderRefused(
                "paddle_free_days_too_long",
                "You have more than a year of free days left. Subscribe closer to the day they end.",
            )
        if free_days > 0:
            item: dict[str, Any] = {
                "quantity": 1,
                "price": {
                    "product_id": product,
                    "description": "Roundturn Pro monthly, first charge after the person's free days",
                    "name": "Monthly",
                    "billing_cycle": {"interval": "month", "frequency": 1},
                    "trial_period": {"interval": "day", "frequency": free_days, "requires_payment_method": True},
                    "unit_price": {"amount": str(price.PRICE_CENTS), "currency_code": price.CURRENCY},
                },
            }
        else:
            item = {"price_id": price_id, "quantity": 1}
        body: dict[str, Any] = {
            "items": [item],
            "customer_id": self._checked(customer_id),
            "currency_code": price.CURRENCY,
            "collection_mode": "automatic",
            "custom_data": {"roundturn_user_id": str(user_id)},
        }
        if discount_id is not None:
            body["discount_id"] = self._checked(discount_id)
        transaction = self._id(self._call("create_transaction", "POST", "/transactions", body=body), "txn")
        return Checkout(transaction, self.pay_url(transaction))

    def pay_url(self, checkout_id: str) -> str:
        return f"/billing/pay?_ptxn={self._checked(checkout_id)}"

    def portal_url(self, *, customer_id: str, subscription_id: str | None) -> str:
        """A fresh, signed-in link to Paddle's customer portal (card, invoices, receipts, cancel). Made per click."""
        body = {"subscription_ids": [self._checked(subscription_id)]} if subscription_id else {}
        data = self._call(
            "portal_session", "POST", f"/customers/{self._checked(customer_id)}/portal-sessions", body=body
        )
        try:
            url = data["urls"]["general"]["overview"]
        except (KeyError, TypeError):
            raise ProviderUnavailable("paddle_portal_session_malformed") from None
        if not isinstance(url, str):
            raise ProviderUnavailable("paddle_portal_session_malformed")
        return url

    def cancel(self, subscription_id: str) -> ProviderSubscription:
        """Cancel at the end of the period paid for: Paddle keeps it active with a scheduled change until then."""
        return self._sub(
            "cancel",
            "POST",
            f"/subscriptions/{self._checked(subscription_id)}/cancel",
            {"effective_from": "next_billing_period"},
        )

    def resume(self, subscription_id: str) -> ProviderSubscription:
        """Remove the scheduled cancel. A subscription that has ended cannot come back (Paddle refuses; subscribe again)."""
        return self._sub(
            "resume", "PATCH", f"/subscriptions/{self._checked(subscription_id)}", {"scheduled_change": None}
        )

    def cancel_now(self, subscription_id: str) -> ProviderSubscription:
        """End it at once (an account deleted). One that has already ended counts as done."""
        sub = self._checked(subscription_id)
        try:
            return self._sub("cancel_now", "POST", f"/subscriptions/{sub}/cancel", {"effective_from": "immediately"})
        except PaddleRefused:
            current = self.fetch_subscription(sub)
            if current.status == "canceled":
                return current
            raise

    def fetch_subscription(self, subscription_id: str) -> ProviderSubscription:
        return self._sub("fetch_subscription", "GET", f"/subscriptions/{self._checked(subscription_id)}", None)

    def create_discount(self, spec: DiscountSpec) -> str:
        """A percentage off Roundturn Pro (every price of the product, so a checkout's own price with free days gets it
        too), usable at checkout with the app's code, which is how a checkout the server made can carry it. The checkout
        page hides Paddle's own discount box, so the app stays the one place a code is typed. With free days, Paddle starts
        a recurring discount after them (its docs, 2026-10-16). `maximum_recurring_intervals` is the months: VERIFY in the
        sandbox that "3" means three payments."""
        product = self.settings.product_id
        if product is None:
            raise ProviderUnavailable("paddle_not_configured")
        body: dict[str, Any] = {
            "description": (f"{spec.code}: {spec.note}" if spec.note else spec.code)[:500],
            "type": "percentage",
            "amount": str(spec.percent_off),
            "enabled_for_checkout": True,
            "code": spec.code,
            "recur": spec.duration != "once",
            "usage_limit": spec.max_uses,
            "expires_at": _rfc3339(spec.expires_at),
            "restrict_to": [product],
        }
        if spec.duration != "once":
            body["maximum_recurring_intervals"] = spec.months if spec.duration == "repeating" else None
        return self._id(self._call("create_discount", "POST", "/discounts", body=body), "dsc")

    def find_discount(self, code: str) -> str | None:
        """Paddle's list, narrowed to this code and to active discounts (VERIFY the `code` filter in the sandbox)."""
        listed = self._call("find_discount", "GET", "/discounts", params={"code": code, "status": "active"})
        for discount in listed if isinstance(listed, list) else []:
            if (
                isinstance(discount, dict)
                and str(discount.get("code", "")).upper() == code.upper()
                and discount.get("status") == "active"
            ):
                return self._id(discount, "dsc")
        return None

    def archive_discount(self, discount_id: str) -> None:
        self._call("archive_discount", "PATCH", f"/discounts/{self._checked(discount_id)}", body={"status": "archived"})

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> BillingEvent:
        secret = self.settings.webhook_secret
        header = next((v for k, v in headers.items() if k.lower() == SIGNATURE_HEADER.lower()), None)
        if not secret or not signature.verify(header, raw_body, secret, now=int(self._wall())):
            raise BadSignature("signature")
        try:
            body = json.loads(raw_body)
            event_type = str(body["event_type"])
            event_id = str(body["event_id"])
            occurred = _when(body["occurred_at"])
            data = body.get("data") or {}
            if occurred is None or not isinstance(data, dict):
                raise ValueError("envelope")
        except (ValueError, KeyError, TypeError):
            raise BadSignature("malformed") from None
        return read_event(event_id, event_type, occurred, data)

    # ---- talking to Paddle ----------------------------------------------------------------------------------------------

    def _sub(self, what: str, method: str, path: str, body: dict[str, Any] | None) -> ProviderSubscription:
        try:
            return subscription_from_paddle(self._call(what, method, path, body=body))
        except (KeyError, ValueError, TypeError):
            raise ProviderUnavailable(f"paddle_{what}_malformed") from None

    def _call(
        self,
        what: str,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """One API call; its `data`. Raises ProviderUnavailable (busy, down, a timeout, a key Paddle refuses, an answer it
        cannot read) or PaddleRefused (any other 4xx)."""
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        url = self.settings.api_url + path
        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                response = self._http.request(method, url, json=body, params=params, headers=headers)
            except (httpx.ConnectError, httpx.ConnectTimeout):  # never reached Paddle: safe to try again
                if last:
                    raise ProviderUnavailable(f"paddle_{what}_network") from None
                self._sleep(0.5 * 2**attempt)
                continue
            except httpx.TimeoutException:  # sent, no answer: Paddle may have acted, so it is not repeated
                raise ProviderUnavailable(f"paddle_{what}_timeout") from None
            except httpx.HTTPError:
                raise ProviderUnavailable(f"paddle_{what}_network") from None
            parsed = _json(response)
            request_id = _request_id(parsed)
            logger.info("paddle %s: %s (request %s)", what, response.status_code, request_id)
            if response.status_code in BUSY and not last:
                self._sleep(_retry_after(response, attempt))
                continue
            if 200 <= response.status_code < 300:
                if not isinstance(parsed, dict) or "data" not in parsed:
                    raise ProviderUnavailable(f"paddle_{what}_malformed")
                return parsed["data"]
            if response.status_code in BUSY:
                raise ProviderUnavailable(f"paddle_{what}_{response.status_code}")
            if response.status_code in (401, 403):  # the key, its permissions or the account: never the person's doing
                logger.error("paddle %s refused the API key (%s, request %s)", what, response.status_code, request_id)
                raise ProviderUnavailable(f"paddle_{what}_{response.status_code}")
            error = parsed.get("error") if isinstance(parsed, dict) else None
            code = str(error.get("code", "")) if isinstance(error, dict) else ""
            code = code if _ERROR_CODE.fullmatch(code) else "unknown"
            detail = str(error.get("detail", "")) if isinstance(error, dict) else ""
            raise PaddleRefused(f"paddle_{what}_{code}", status=response.status_code, code=code, paddle_detail=detail)
        raise ProviderUnavailable(f"paddle_{what}_retries")  # not reached: the last try returns or raises

    @staticmethod
    def _id(data: Any, prefix: str) -> str:
        value = data.get("id") if isinstance(data, dict) else None
        if not isinstance(value, str) or not value.startswith(prefix + "_") or not _PADDLE_ID.fullmatch(value):
            raise ProviderUnavailable(f"paddle_{prefix}_id_malformed")
        return value

    @staticmethod
    def _checked(paddle_id: str) -> str:
        """An id going into a path or a body: only Paddle's own shape, so nothing stored can turn into another request."""
        if not _PADDLE_ID.fullmatch(paddle_id):
            raise ProviderRefused("paddle_id_malformed")
        return paddle_id


# ---- what Paddle says, in the connector's words --------------------------------------------------------------------------

# What is kept of each event (billing_events.payload): ids, statuses, dates and amounts, never a name, an email or an address.
_KEPT_SUBSCRIPTION = ("id", "status", "customer_id", "transaction_id", "next_billed_at", "canceled_at", "paused_at")
_KEPT_ADJUSTMENT = ("id", "action", "status", "reason", "transaction_id", "subscription_id", "currency_code")
_KEPT_TRANSACTION = ("id", "status", "origin", "subscription_id", "currency_code", "billed_at")


def read_event(event_id: str, event_type: str, occurred: datetime, data: dict[str, Any]) -> BillingEvent:
    """A signed Paddle notification read into a `BillingEvent`. A signed event of a shape this does not read is kept as
    `unknown`: acknowledged, stored, nothing applied."""
    if event_type.startswith("subscription."):
        kind = "subscription"
        kept: dict[str, Any] = {k: data.get(k) for k in _KEPT_SUBSCRIPTION if k in data}
        kept["current_billing_period"] = _period(data.get("current_billing_period"))
        kept["scheduled_change"] = _scheduled(data.get("scheduled_change"))
        custom = data.get("custom_data")
        if isinstance(custom, dict) and isinstance(custom.get("roundturn_user_id"), str):
            kept["roundturn_user_id"] = custom["roundturn_user_id"]
        try:
            subscription: ProviderSubscription | None = subscription_from_paddle(data)
        except (KeyError, ValueError, TypeError):
            kind, subscription = "unknown", None
        return BillingEvent(
            event_id, event_type, kind, occurred, subscription=subscription, payload=_envelope(event_type, kept)
        )
    if event_type in ("adjustment.created", "adjustment.updated"):
        kept = {k: data.get(k) for k in _KEPT_ADJUSTMENT if k in data}
        totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
        kept["total"] = totals.get("total")
        refund = Refund(
            action=str(data.get("action", "refund")),
            amount=str(totals.get("total", "0")),
            currency=str(data.get("currency_code", price.CURRENCY)),
            subscription_id=data.get("subscription_id") if isinstance(data.get("subscription_id"), str) else None,
        )
        return BillingEvent(
            event_id, event_type, "refund", occurred, refund=refund, payload=_envelope(event_type, kept)
        )
    if event_type.startswith("transaction."):
        kept = {k: data.get(k) for k in _KEPT_TRANSACTION if k in data}
        return BillingEvent(event_id, event_type, "payment", occurred, payload=_envelope(event_type, kept))
    return BillingEvent(event_id, event_type, "unknown", occurred, payload={"event_type": event_type})


def subscription_from_paddle(data: Mapping[str, Any]) -> ProviderSubscription:
    """Paddle's subscription in the connector's words. Cancelling is a scheduled change with action `cancel`; it ends at that
    change's `effective_at` (normally the end of the period paid for). Paused and canceled ones have no billing period."""
    status = data["status"]
    if status not in STATUSES:
        raise ValueError("unknown status")
    identifier = data["id"]
    if not isinstance(identifier, str) or not identifier.startswith("sub_") or not _PADDLE_ID.fullmatch(identifier):
        raise ValueError("not a subscription id")
    period = data.get("current_billing_period") or {}
    scheduled = data.get("scheduled_change") or {}
    cancelling = isinstance(scheduled, dict) and scheduled.get("action") == "cancel"
    end = _when(period.get("ends_at")) if isinstance(period, dict) else None
    if cancelling and _when(scheduled.get("effective_at")) is not None:
        end = _when(scheduled.get("effective_at"))
    custom = data.get("custom_data") or {}
    user_ref = custom.get("roundturn_user_id") if isinstance(custom, dict) else None
    transaction = data.get("transaction_id")
    customer = data.get("customer_id")
    return ProviderSubscription(
        subscription_id=identifier,
        customer_id=customer if isinstance(customer, str) else None,
        status=status,
        current_period_end=end,
        cancel_at_period_end=cancelling,
        ended_at=_when(data.get("canceled_at")),
        user_ref=user_ref if isinstance(user_ref, str) else None,
        checkout_id=transaction if isinstance(transaction, str) else None,
    )


def _envelope(event_type: str, kept: dict[str, Any]) -> dict[str, Any]:
    return {"event_type": event_type, "data": kept}


def _period(value: Any) -> dict[str, Any] | None:
    return {k: value.get(k) for k in ("starts_at", "ends_at")} if isinstance(value, dict) else None


def _scheduled(value: Any) -> dict[str, Any] | None:
    return {k: value.get(k) for k in ("action", "effective_at")} if isinstance(value, dict) else None


def _when(value: Any) -> datetime | None:
    """An RFC 3339 time as Paddle writes it (`Z`, and up to nine digits of a second), in UTC; None for none."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not a time")
    text = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)  # Python reads at most six digits of a second
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError("a time without its zone")
    return moment.astimezone(UTC)


def _rfc3339(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z") if moment is not None else None


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _request_id(parsed: Any) -> str:
    meta = parsed.get("meta") if isinstance(parsed, dict) else None
    value = meta.get("request_id") if isinstance(meta, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9-]{1,64}", value) else "-"


def _retry_after(response: httpx.Response, attempt: int) -> float:
    try:
        wanted = float(response.headers.get("retry-after", ""))
    except ValueError:
        wanted = 0.5 * 2**attempt
    return max(0.0, min(wanted, 2.0))
