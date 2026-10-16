"""A stand-in for Paddle's API, served to `PaddleBilling` through an `httpx.MockTransport`: no network, no account.

The shapes are Paddle's own, from the examples in its developer docs (checked through the paddle-docs MCP server on
2026-10-16), trimmed to the fields Roundturn reads and stripped of every name, email and address. It keeps customers,
transactions and subscriptions, answers the calls the adapter makes, and does what Paddle does on its own (a checkout
paid, a renewal, a failed payment, the end of a cancelled period, a refund), returning the signed notification Paddle
would send. Error codes other than `customer_already_exists` and `not_found` are this file's guesses: the adapter relies
on none of them.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx

from tradejournal.billing import signature
from tradejournal.billing.paddle import PaddleBilling, PaddleSettings

MONTH = timedelta(days=30)
# Built from short pieces at run time: never a high-entropy literal in the repository (gitleaks scans the history).
API_KEY = "pdl_" + "sdbx_" + "apikey_" + "test" * 4
WEBHOOK_SECRET = "pdl_" + "ntfset_" + "test" * 4
CLIENT_TOKEN = "test_" + "token" * 4
PRODUCT = "pro_" + "0" * 24 + "01"
PRICE = "pri_" + "0" * 24 + "01"


def settings(**changes: Any) -> PaddleSettings:
    values: dict[str, Any] = {
        "environment": "sandbox",
        "api_key": API_KEY,
        "webhook_secret": WEBHOOK_SECRET,
        "client_token": CLIENT_TOKEN,
        "product_id": PRODUCT,
        "price_id": PRICE,
    }
    return PaddleSettings(**{**values, **changes})


@dataclass(frozen=True)
class Delivery:
    headers: dict[str, str]
    body: bytes
    event_id: str
    event_type: str


def iso(moment: datetime | None) -> str | None:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z") if moment is not None else None


class FakePaddleApi:
    def __init__(self, *, clock=lambda: datetime.now(UTC), wall=time.time, secret: str = WEBHOOK_SECRET) -> None:
        self.clock = clock
        self.wall = wall
        self.secret = secret
        self.customers: dict[str, str] = {}  # id -> email
        self.transactions: dict[str, dict[str, Any]] = {}
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.requests: list[tuple[str, str, Any]] = []  # (method, path and query, the JSON body)
        self.answers: list[httpx.Response | Exception] = []  # given first, in order, instead of the real answer
        self._ids = itertools.count(1)

    def provider(self, **changes: Any) -> PaddleBilling:
        return PaddleBilling(
            settings(**changes),
            http=httpx.Client(transport=httpx.MockTransport(self.handle)),
            wall=self.wall,
            sleep=lambda _seconds: None,
        )

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._ids):026d}"

    # ---- the API ---------------------------------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        where = request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        self.requests.append((request.method, where, body))
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        if request.headers.get("authorization") != f"Bearer {API_KEY}":
            return error(403, "forbidden", "You aren't permitted to perform this request.")
        parts = request.url.path.strip("/").split("/")
        method = request.method
        if parts == ["customers"] and method == "POST":
            return self._create_customer(body)
        if parts == ["customers"] and method == "GET":
            wanted = parse_qs(request.url.query.decode()).get("email", [""])[0].casefold()
            found = [{"id": cid, "email": email} for cid, email in self.customers.items() if email == wanted]
            return ok(found)
        if len(parts) == 3 and parts[0] == "customers" and parts[2] == "portal-sessions" and method == "POST":
            return self._portal(parts[1], body)
        if parts == ["transactions"] and method == "POST":
            return self._create_transaction(body)
        if len(parts) >= 2 and parts[0] == "subscriptions":
            sub = self.subscriptions.get(parts[1])
            if sub is None:
                return error(404, "not_found", f"Entity {parts[1]} not found")
            if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
                return self._cancel(sub, body)
            if len(parts) == 2 and method == "PATCH":
                if sub["status"] == "canceled":
                    return error(400, "subscription_update_when_canceled", "Cannot update a canceled subscription")
                if "scheduled_change" in (body or {}) and body["scheduled_change"] is None:
                    sub["scheduled_change"] = None
                return ok(sub)
            if len(parts) == 2 and method == "GET":
                return ok(sub)
        return error(404, "not_found", "Entity not found")

    def _create_customer(self, body: dict[str, Any]) -> httpx.Response:
        email = str(body.get("email", "")).casefold()
        for cid, known in self.customers.items():
            if known == email:
                return error(409, "customer_already_exists", f"customer email conflicts with customer of id {cid}")
        cid = self._id("ctm")
        self.customers[cid] = email
        return ok({"id": cid, "status": "active", "email": email, "custom_data": body.get("custom_data")}, status=201)

    def _create_transaction(self, body: dict[str, Any]) -> httpx.Response:
        if body.get("customer_id") not in self.customers:
            return error(400, "transaction_customer_not_found", "customer not found")
        tid = self._id("txn")
        self.transactions[tid] = body
        return ok(
            {
                "id": tid,
                "status": "ready",
                "customer_id": body["customer_id"],
                "custom_data": body.get("custom_data"),
                "collection_mode": body.get("collection_mode"),
                "currency_code": body.get("currency_code"),
                "discount_id": body.get("discount_id"),
                "checkout": {"url": f"https://app.example.test/billing/pay?_ptxn={tid}"},
            },
            status=201,
        )

    def _portal(self, customer: str, body: dict[str, Any]) -> httpx.Response:
        if customer not in self.customers:
            return error(404, "not_found", f"Entity {customer} not found")
        overview = f"https://customer-portal.paddle.com/cpl_test?action=overview&token=pga_{next(self._ids)}"
        subs = [
            {
                "id": sid,
                "cancel_subscription": f"{overview}&action=cancel_subscription&subscription_id={sid}",
                "update_subscription_payment_method": f"{overview}&action=update_subscription_payment_method",
            }
            for sid in (body or {}).get("subscription_ids", [])
        ]
        return ok(
            {
                "id": self._id("cpls"),
                "customer_id": customer,
                "urls": {"general": {"overview": overview}, "subscriptions": subs},
            },
            status=201,
        )

    def _cancel(self, sub: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
        if sub["status"] == "canceled":
            return error(400, "subscription_update_when_canceled", "Cannot update a canceled subscription")
        if (body or {}).get("effective_from") == "immediately":
            sub.update(
                status="canceled", canceled_at=iso(self.clock()), current_billing_period=None, scheduled_change=None
            )
        else:
            ends = sub["current_billing_period"]["ends_at"]
            sub["scheduled_change"] = {"action": "cancel", "effective_at": ends, "resume_at": None}
        return ok(sub)

    # ---- what Paddle does on its own --------------------------------------------------------------------------------------

    def complete(self, transaction_id: str, *, custom_data: dict[str, Any] | None = None) -> Delivery:
        """The person paid (or gave a card, for a trial): Paddle makes the subscription and says so."""
        txn = self.transactions[transaction_id]
        item = txn["items"][0]
        trial = (item.get("price") or {}).get("trial_period")
        now = self.clock()
        ends = now + (timedelta(days=trial["frequency"]) if trial else MONTH)
        sid = self._id("sub")
        self.subscriptions[sid] = {
            "id": sid,
            "status": "trialing" if trial else "active",
            "customer_id": txn["customer_id"],
            "address_id": "add_" + "0" * 26,
            "business_id": None,
            "currency_code": "USD",
            "collection_mode": "automatic",
            "started_at": iso(now),
            "first_billed_at": None if trial else iso(now),
            "next_billed_at": iso(ends),
            "paused_at": None,
            "canceled_at": None,
            "current_billing_period": {"starts_at": iso(now), "ends_at": iso(ends)},
            "billing_cycle": {"interval": "month", "frequency": 1},
            "scheduled_change": None,
            "custom_data": custom_data if custom_data is not None else txn.get("custom_data"),
            "discount": None,
        }
        return self.deliver("subscription.created", {**self.subscriptions[sid], "transaction_id": transaction_id})

    def renew(self, sid: str) -> Delivery:
        sub = self.subscriptions[sid]
        start = datetime.fromisoformat(sub["current_billing_period"]["ends_at"])
        sub.update(status="active", current_billing_period={"starts_at": iso(start), "ends_at": iso(start + MONTH)})
        sub["next_billed_at"] = sub["current_billing_period"]["ends_at"]
        return self.deliver("subscription.updated", sub)

    def fail_payment(self, sid: str) -> Delivery:
        sub = self.subscriptions[sid]
        start = datetime.fromisoformat(sub["current_billing_period"]["ends_at"])
        sub.update(status="past_due", current_billing_period={"starts_at": iso(start), "ends_at": iso(start + MONTH)})
        return self.deliver("subscription.past_due", sub)

    def finish(self, sid: str) -> Delivery:
        """A cancel scheduled for the end of the period takes effect."""
        sub = self.subscriptions[sid]
        sub.update(status="canceled", canceled_at=iso(self.clock()), current_billing_period=None, scheduled_change=None)
        return self.deliver("subscription.canceled", sub)

    def announce(self, sid: str, event_type: str = "subscription.updated") -> Delivery:
        return self.deliver(event_type, self.subscriptions[sid])

    def refund(self, sid: str, *, amount: str = "1500", action: str = "refund") -> Delivery:
        sub = self.subscriptions[sid]
        return self.deliver(
            "adjustment.created",
            {
                "id": self._id("adj"),
                "action": action,
                "type": "full",
                "reason": "error",
                "status": "pending_approval",
                "transaction_id": self._id("txn"),
                "subscription_id": sid,
                "customer_id": sub["customer_id"],
                "currency_code": "USD",
                "totals": {
                    "subtotal": amount,
                    "tax": "0",
                    "total": amount,
                    "fee": "0",
                    "earnings": amount,
                    "currency_code": "USD",
                },
            },
        )

    def deliver(self, event_type: str, data: dict[str, Any], *, occurred_at: datetime | None = None) -> Delivery:
        event_id = self._id("evt")
        body = json.dumps(
            {
                "event_id": event_id,
                "event_type": event_type,
                "occurred_at": iso(occurred_at or self.clock()),
                "notification_id": self._id("ntf"),
                "data": data,
            }
        ).encode()
        headers = {
            "Paddle-Signature": signature.sign(self.secret, body, int(self.wall())),
            "Content-Type": "application/json",
        }
        return Delivery(headers, body, event_id, event_type)


def ok(data: Any, *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"data": data, "meta": {"request_id": "00000000-0000-4000-8000-000000000001"}})


def error(status: int, code: str, detail: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "error": {
                "type": "request_error",
                "code": code,
                "detail": detail,
                "documentation_url": f"https://developer.paddle.com/errors/shared/{code}",
            },
            "meta": {"request_id": "00000000-0000-4000-8000-000000000002"},
        },
    )
