"""A stand-in for Stripe's API, served to `StripeBilling` through an `httpx.MockTransport`: no network, no account.

The shapes are Stripe's own as Northfield Labs' test mode answered them on 2026-10-23 (API version 2026-08-26.dahlia),
trimmed to the fields Roundturn reads and stripped of names and addresses (an email stays where Stripe puts one, so the
tests can show the connector never keeps it). It keeps customers, checkouts, subscriptions, coupons and promotion codes,
answers the calls the connector makes as Stripe did (a second DELETE is a 404, a cancel at the period end also sets
`canceled_at`, Checkout refuses free days under two, Managed Payments refuses payment-method settings), honours idempotency
keys, and does what Stripe does on its own (a checkout paid, a renewal, a failed payment, the end of a cancelled period, a
refund), returning the signed event Stripe would send.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, parse_qsl

import httpx

from tradejournal.billing.stripe import API_VERSION, EVENTS, StripeBilling, StripeSettings, sign

MONTH = timedelta(days=30)
ORIGIN = "https://app.example.com"
# Built from short pieces at run time: never a key-shaped literal in the repository (gitleaks scans the history).
API_KEY = "sk_" + "test_" + "Fake" * 6
LIVE_KEY = "sk_" + "live_" + "Fake" * 6
WEBHOOK_SECRET = "whsec_" + "Fake" * 6
PRODUCT = "prod_" + "Fake" * 4
PRICE = "price_" + "Fake" * 4


def settings(**changes: Any) -> StripeSettings:
    values: dict[str, Any] = {
        "environment": "test",
        "api_key": API_KEY,
        "webhook_secret": WEBHOOK_SECRET,
        "product_id": PRODUCT,
        "price_id": PRICE,
        "managed_payments": True,
    }
    return StripeSettings(**{**values, **changes})


@dataclass(frozen=True)
class Delivery:
    headers: dict[str, str]
    body: bytes
    event_id: str
    event_type: str


def unix(moment: datetime) -> int:
    return int(moment.timestamp())


class FakeStripeApi:
    def __init__(self, *, clock=lambda: datetime.now(UTC), wall=time.time, secret: str = WEBHOOK_SECRET) -> None:
        self.clock = clock
        self.wall = wall
        self.secret = secret
        self.customers: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.coupons: dict[str, dict[str, Any]] = {}
        self.promotions: dict[str, dict[str, Any]] = {}
        self.products: dict[str, dict[str, Any]] = {}
        self.prices: dict[str, dict[str, Any]] = {}
        self.portal_configurations: list[dict[str, Any]] = []
        self.webhook_endpoints: list[dict[str, Any]] = []
        self.requests: list[
            tuple[str, str, dict[str, str], str | None]
        ] = []  # method, path?query, form, idempotency key
        self.answers: list[httpx.Response | Exception] = []  # given first, in order, instead of the real answer
        self._replies: dict[str, tuple[tuple[str, dict], httpx.Response]] = {}  # by idempotency key
        self._ids = itertools.count(1)

    def provider(self, *, origin: str | None = ORIGIN, **changes: Any) -> StripeBilling:
        return StripeBilling(
            settings(**changes),
            app_origin=origin,
            http=httpx.Client(transport=httpx.MockTransport(self.handle)),
            wall=self.wall,
            sleep=lambda _seconds: None,
        )

    def _id(self, prefix: str) -> str:
        return f"{prefix}_Fake{next(self._ids):012d}"

    # ---- the API ---------------------------------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        form = dict(parse_qsl(request.content.decode(), keep_blank_values=True)) if request.content else {}
        where = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        key = request.headers.get("idempotency-key")
        self.requests.append((request.method, where, form, key))
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if request.headers.get("authorization") != f"Bearer {API_KEY}":
            return error(401, None, "authentication_error")
        if request.headers.get("stripe-version") != API_VERSION:
            return error(400, "api_version_invalid")
        if key is not None and key in self._replies:
            asked, reply = self._replies[key]
            # Stripe replays the first answer to a repeated key, and refuses the key with other parameters.
            return reply if asked == (request.url.path, form) else error(400, None, "idempotency_error")
        answer = self._route(request.method, request.url.path, parse_qs(request.url.query.decode()), form)
        if key is not None and answer.status_code < 500:
            self._replies[key] = ((request.url.path, form), answer)
        return answer

    def _route(self, method: str, path: str, query: dict[str, list[str]], form: dict[str, str]) -> httpx.Response:
        parts = path.strip("/").split("/")[1:]  # without "v1"
        one = {k: v[0] for k, v in query.items()}
        if parts == ["customers", "search"] and method == "GET":
            wanted = one.get("query", "").split(":", 1)[-1].strip("'")
            found = [c for c in self.customers.values() if c["metadata"].get("roundturn_user_id") == wanted]
            return ok({"object": "search_result", "data": found[:1], "has_more": False})
        if parts == ["customers"] and method == "POST":
            cid = self._id("cus")
            self.customers[cid] = {
                "id": cid,
                "object": "customer",
                "email": form.get("email"),
                "metadata": {"roundturn_user_id": form.get("metadata[roundturn_user_id]")},
                "livemode": False,
            }
            return ok(self.customers[cid])
        if parts == ["checkout", "sessions"] and method == "GET":
            found = [
                s
                for s in self.sessions.values()
                if s["customer"] == one.get("customer") and s["status"] == one.get("status", s["status"])
            ]
            return ok({"object": "list", "data": found, "has_more": False})
        if parts == ["checkout", "sessions"] and method == "POST":
            return self._create_session(form)
        if len(parts) >= 3 and parts[:2] == ["checkout", "sessions"]:
            session = self.sessions.get(parts[2])
            if session is None:
                return error(404, "resource_missing")
            if len(parts) == 4 and parts[3] == "expire" and method == "POST":
                if session["status"] != "open":
                    return error(400, None)
                session.update(status="expired", url=None)
                return ok(session)
            return ok(session)
        if parts == ["billing_portal", "sessions"] and method == "POST":
            if form.get("customer") not in self.customers:
                return error(400, "resource_missing")
            return ok(
                {
                    "id": self._id("bps"),
                    "object": "billing_portal.session",
                    "customer": form["customer"],
                    "return_url": form.get("return_url"),
                    "url": f"https://billing.stripe.com/p/session/test_{next(self._ids)}",
                }
            )
        if len(parts) == 2 and parts[0] == "subscriptions":
            return self._subscription(method, parts[1], form)
        if parts == ["coupons"] and method == "POST":
            if form["id"] in self.coupons:
                return error(400, "resource_already_exists")
            self.coupons[form["id"]] = coupon_from(form)
            return ok(self.coupons[form["id"]])
        if len(parts) == 2 and parts[0] == "coupons" and method == "GET":
            found = self.coupons.get(parts[1])
            return ok(found) if found else error(404, "resource_missing")
        if parts == ["promotion_codes"] and method == "POST":
            code = form["code"]
            if any(p["code"].upper() == code.upper() and p["active"] for p in self.promotions.values()):
                return error(400, "promotion_code_already_exists")
            if form.get("promotion[coupon]") not in self.coupons:
                return error(400, "resource_missing")
            pid = self._id("promo")
            self.promotions[pid] = {
                "id": pid,
                "object": "promotion_code",
                "active": True,
                "code": code,
                "promotion": {"type": "coupon", "coupon": form["promotion[coupon]"]},
                "max_redemptions": _int(form.get("max_redemptions")),
                "expires_at": _int(form.get("expires_at")),
                "metadata": {},
            }
            return ok(self.promotions[pid])
        if parts == ["promotion_codes"] and method == "GET":
            wanted, active = one.get("code", "").upper(), one.get("active")
            found = [
                p
                for p in self.promotions.values()
                if p["code"].upper() == wanted and (active is None or str(p["active"]).lower() == active)
            ]
            return ok({"object": "list", "data": found, "has_more": False})
        if len(parts) == 2 and parts[0] == "promotion_codes" and method == "POST":
            found = self.promotions.get(parts[1])
            if found is None:
                return error(404, "resource_missing")
            if "active" in form:
                found["active"] = form["active"] == "true"
            return ok(found)
        if len(parts) == 2 and parts[0] in ("products", "prices") and method == "GET":
            found = (self.products if parts[0] == "products" else self.prices).get(parts[1])
            return ok(found) if found else error(404, "resource_missing")
        if parts == ["billing_portal", "configurations"] and method == "GET":
            found = [c for c in self.portal_configurations if one.get("is_default") != "true" or c.get("is_default")]
            return ok({"object": "list", "data": found, "has_more": False})
        if parts == ["webhook_endpoints"] and method == "GET":
            return ok({"object": "list", "data": self.webhook_endpoints, "has_more": False})
        return error(404, "resource_missing")

    def _create_session(self, form: dict[str, str]) -> httpx.Response:
        if form.get("customer") not in self.customers:
            return error(400, "resource_missing")
        managed = form.get("managed_payments[enabled]") == "true"
        if managed and any(k.startswith("allowed_payment_method_types") for k in form):
            return error(400, None)  # "allowed_payment_method_types cannot be used with Managed Payments"
        trial = _int(form.get("subscription_data[trial_period_days]"))
        if trial is not None and trial < 2:
            return error(400, None)  # "The trial end date must be at least 2 days after ..."
        promotion = form.get("discounts[0][promotion_code]")
        if promotion is not None and not self.promotions.get(promotion, {}).get("active"):
            return error(400, "resource_missing")
        sid = f"cs_test_Fake{next(self._ids):020d}"
        self.sessions[sid] = {
            "id": sid,
            "object": "checkout.session",
            "status": "open",
            "mode": form.get("mode"),
            "customer": form["customer"],
            "client_reference_id": form.get("client_reference_id"),
            "customer_details": {"email": self.customers[form["customer"]]["email"], "name": None, "address": None},
            "payment_status": "unpaid",
            "subscription": None,
            "amount_total": 0,
            "currency": "usd",
            "livemode": False,
            "managed_payments": {"enabled": managed},
            "url": f"https://checkout.stripe.com/c/pay/{sid}#fidFake",
            "form": form,  # what was asked for, for the tests (not a Stripe field)
        }
        return ok(self.sessions[sid])

    def _subscription(self, method: str, sid: str, form: dict[str, str]) -> httpx.Response:
        sub = self.subscriptions.get(sid)
        if sub is None:
            return error(404, "resource_missing")
        if method == "GET":
            return ok(sub)
        if method == "DELETE":
            if sub["status"] == "canceled":
                return error(404, "resource_missing")  # "No such subscription", although a GET still shows it
            now = unix(self.clock())
            sub.update(status="canceled", ended_at=now, canceled_at=now, cancel_at=None, cancel_at_period_end=False)
            return ok(sub)
        if sub["status"] == "canceled":
            return error(400, None)
        if form.get("cancel_at_period_end") == "true":
            sub.update(
                cancel_at_period_end=True, cancel_at=period_end(sub), canceled_at=unix(self.clock())
            )  # Stripe sets canceled_at now, while the subscription runs on
        elif form.get("cancel_at_period_end") == "false":
            sub.update(cancel_at_period_end=False, cancel_at=None, canceled_at=None)
        return ok(sub)

    # ---- what Stripe does on its own ----------------------------------------------------------------------------------

    def complete(self, session_id: str, *, metadata: dict[str, str] | None = None) -> Delivery:
        """The person paid (or gave a card, for free days): Stripe makes the subscription and says so."""
        session = self.sessions[session_id]
        form = session["form"]
        trial = _int(form.get("subscription_data[trial_period_days]"))
        now = self.clock()
        ends = now + (timedelta(days=trial) if trial else MONTH)
        sid = self._id("sub")
        person = form.get("subscription_data[metadata][roundturn_user_id]")
        self.subscriptions[sid] = {
            "id": sid,
            "object": "subscription",
            "status": "trialing" if trial else "active",
            "customer": session["customer"],
            "cancel_at_period_end": False,
            "cancel_at": None,
            "canceled_at": None,
            "ended_at": None,
            "created": unix(now),
            "start_date": unix(now),
            "trial_start": unix(now) if trial else None,
            "trial_end": unix(ends) if trial else None,
            "items": {
                "object": "list",
                "data": [
                    {
                        "id": self._id("si"),
                        "object": "subscription_item",
                        "current_period_start": unix(now),
                        "current_period_end": unix(ends),
                        "price": {"id": PRICE, "product": PRODUCT, "unit_amount": 1500, "currency": "usd"},
                        "quantity": 1,
                    }
                ],
            },
            "metadata": metadata if metadata is not None else ({"roundturn_user_id": person} if person else {}),
            "discounts": [],
            "livemode": False,
            "managed_payments": session["managed_payments"],
        }
        session.update(status="complete", subscription=sid, payment_status="paid", url=None)
        return self.deliver("customer.subscription.created", self.subscriptions[sid])

    def checkout_completed(self, session_id: str) -> Delivery:
        session = {k: v for k, v in self.sessions[session_id].items() if k != "form"}
        return self.deliver("checkout.session.completed", session)

    def renew(self, sid: str) -> Delivery:
        sub = self.subscriptions[sid]
        start = period_end(sub)
        set_period(sub, start, start + unix_month())
        sub.update(status="active", trial_end=sub["trial_end"])
        return self.deliver("customer.subscription.updated", sub)

    def fail_payment(self, sid: str) -> Delivery:
        sub = self.subscriptions[sid]
        start = period_end(sub)
        set_period(sub, start, start + unix_month())
        sub["status"] = "past_due"
        return self.deliver("customer.subscription.updated", sub)

    def finish(self, sid: str) -> Delivery:
        """A cancel scheduled for the period's end takes effect, or the retries ran out (the dashboard says cancel)."""
        sub = self.subscriptions[sid]
        now = unix(self.clock())
        sub.update(status="canceled", ended_at=now, cancel_at=None, cancel_at_period_end=False)
        sub["canceled_at"] = sub["canceled_at"] or now
        return self.deliver("customer.subscription.deleted", sub)

    def announce(self, sid: str, event_type: str = "customer.subscription.updated") -> Delivery:
        return self.deliver(event_type, self.subscriptions[sid])

    def refund(self, sid: str, *, amount: int = 1500) -> Delivery:
        sub = self.subscriptions[sid]
        charge = {
            "id": self._id("ch"),
            "object": "charge",
            "amount": 1500,
            "amount_refunded": amount,
            "currency": "usd",
            "customer": sub["customer"],
            "payment_intent": self._id("pi"),
            "billing_details": {"email": "someone@example.com", "name": "Some One", "address": {"line1": "1 Main St"}},
            "refunded": True,
        }
        return self.deliver("charge.refunded", charge)

    def deliver(
        self, event_type: str, data: dict[str, Any], *, created: datetime | None = None, livemode: bool = False
    ) -> Delivery:
        event_id = self._id("evt")
        body = json.dumps(
            {
                "id": event_id,
                "object": "event",
                "api_version": API_VERSION,
                "created": unix(created or self.clock()),
                "data": {"object": data},
                "livemode": livemode,
                "pending_webhooks": 1,
                "type": event_type,
            }
        ).encode()
        headers = {"Stripe-Signature": sign(self.secret, body, int(self.wall())), "Content-Type": "application/json"}
        return Delivery(headers, body, event_id, event_type)

    # ---- the catalog, the portal and the endpoint, for the readiness check ---------------------------------------------

    def ready(self, origin: str = ORIGIN) -> FakeStripeApi:
        """Everything `stripe_check` asks for, as it should be."""
        self.products[PRODUCT] = {"id": PRODUCT, "active": True, "livemode": False, "tax_code": "txcd_10103000"}
        self.prices[PRICE] = {
            "id": PRICE,
            "active": True,
            "product": PRODUCT,
            "unit_amount": 1500,
            "currency": "usd",
            "tax_behavior": "exclusive",
            "recurring": {"interval": "month", "interval_count": 1, "trial_period_days": None},
        }
        self.portal_configurations = [
            {
                "id": self._id("bpc"),
                "is_default": True,
                "features": {
                    "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
                    "subscription_update": {"enabled": False},
                },
            }
        ]
        self.webhook_endpoints = [
            {
                "id": self._id("we"),
                "url": f"{origin}/webhooks/billing",
                "status": "enabled",
                "api_version": API_VERSION,
                "enabled_events": list(EVENTS),
            }
        ]
        return self


def coupon_from(form: dict[str, str]) -> dict[str, Any]:
    return {
        "id": form["id"],
        "object": "coupon",
        "name": form.get("name"),
        "percent_off": float(form["percent_off"]),
        "duration": form["duration"],
        "duration_in_months": _int(form.get("duration_in_months")),
        "max_redemptions": _int(form.get("max_redemptions")),
        "redeem_by": _int(form.get("redeem_by")),
        "applies_to": {"products": [form.get("applies_to[products][0]")]},
        "valid": True,
    }


def period_end(sub: dict[str, Any]) -> int:
    return sub["items"]["data"][0]["current_period_end"]


def set_period(sub: dict[str, Any], start: int, end: int) -> None:
    sub["items"]["data"][0].update(current_period_start=start, current_period_end=end)


def unix_month() -> int:
    return int(MONTH.total_seconds())


def _int(value: str | None) -> int | None:
    return int(value) if value not in (None, "") else None


def ok(data: Any, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data, headers={"Request-Id": "req_Fake0000000001"})


def error(status: int, code: str | None, kind: str = "invalid_request_error") -> httpx.Response:
    body: dict[str, Any] = {"error": {"type": kind, "message": "Stripe's own words, never read by the connector"}}
    if code is not None:
        body["error"]["code"] = code
    return httpx.Response(status, json=body, headers={"Request-Id": "req_Fake0000000002"})
