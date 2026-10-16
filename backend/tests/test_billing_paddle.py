"""The Paddle connector without a network: its settings, each API call it makes and what it does with the answer, its
retries, and reading Paddle's notifications (docs/design/paddle-integration.md sections 4, 5 and 7.1)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from paddle_world import API_KEY, CLIENT_TOKEN, PRICE, PRODUCT, WEBHOOK_SECRET, FakePaddleApi, error, ok, settings

from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.billing import signature
from tradejournal.billing.factory import provider_for, provider_from_env
from tradejournal.billing.paddle import (
    PaddleBilling,
    PaddleRefused,
    PaddleSettings,
    read_event,
    subscription_from_paddle,
)
from tradejournal.billing.types import BadSignature, DiscountSpec, ProviderRefused, ProviderUnavailable

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
LIVE_KEY = "pdl_" + "live_" + "apikey_" + "test" * 4
ENV = {
    "TJ_PADDLE_ENV": "sandbox",
    "TJ_PADDLE_API_KEY": API_KEY,
    "TJ_PADDLE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "TJ_PADDLE_CLIENT_TOKEN": CLIENT_TOKEN,
    "TJ_PADDLE_PRODUCT_ID": PRODUCT,
    "TJ_PADDLE_PRICE_ID": PRICE,
}


@pytest.fixture
def api():
    return FakePaddleApi(clock=lambda: NOW, wall=lambda: NOW.timestamp())


@pytest.fixture
def paddle(api):
    return api.provider()


def calls(api):
    return [(method, where) for method, where, _ in api.requests]


# ---- the settings --------------------------------------------------------------------------------------------------------


def test_the_web_server_takes_all_six_settings_and_builds_the_paddle_connector():
    config = ApiConfig.from_env({**ENV, "TJ_BILLING_PROVIDER": "paddle"})
    provider = provider_for(config, clock=lambda: NOW)
    assert isinstance(provider, PaddleBilling) and provider.settings.api_url == "https://sandbox-api.paddle.com"
    live = PaddleSettings.from_env(
        {
            **ENV,
            "TJ_PADDLE_ENV": "production",
            "TJ_PADDLE_API_KEY": LIVE_KEY,
            "TJ_PADDLE_CLIENT_TOKEN": "live_" + "token" * 4,
        }
    )
    assert live is not None and live.api_url == "https://api.paddle.com"


@pytest.mark.parametrize("missing", sorted(ENV))
def test_the_web_server_refuses_paddle_with_any_setting_missing(missing):
    env = {k: v for k, v in ENV.items() if k != missing}
    with pytest.raises(ConfigError, match="TJ_PADDLE"):
        ApiConfig.from_env({**env, "TJ_BILLING_PROVIDER": "paddle"})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"TJ_PADDLE_ENV": "production"}, "sandbox key, but TJ_PADDLE_ENV is production"),
        ({"TJ_PADDLE_API_KEY": LIVE_KEY}, "live key, but TJ_PADDLE_ENV is sandbox"),
        ({"TJ_PADDLE_CLIENT_TOKEN": "live_" + "token" * 4}, "must start with test_"),
        ({"TJ_PADDLE_ENV": "staging"}, "'sandbox' or 'production'"),
        ({"TJ_PADDLE_WEBHOOK_SECRET": "whsec_" + "x" * 10}, "pdl_ntfset_"),
        ({"TJ_PADDLE_PRODUCT_ID": "prod_123"}, "pro_"),
        ({"TJ_PADDLE_PRICE_ID": "price_123"}, "pri_"),
        ({"TJ_PADDLE_CLIENT_TOKEN": "test_<script>"}, "characters"),
    ],
)
def test_a_mixed_or_mistyped_set_stops_the_server_and_names_the_setting_not_its_value(changes, message):
    with pytest.raises(ConfigError, match=message) as refused:
        ApiConfig.from_env({**ENV, **changes, "TJ_BILLING_PROVIDER": "paddle"})
    assert API_KEY not in str(refused.value) and LIVE_KEY not in str(refused.value)


def test_the_key_and_the_webhook_secret_are_never_shown():
    config = ApiConfig.from_env({**ENV, "TJ_BILLING_PROVIDER": "paddle"})
    assert API_KEY not in repr(config) and API_KEY not in repr(config.paddle)
    assert WEBHOOK_SECRET not in repr(config) and WEBHOOK_SECRET not in repr(config.paddle)


def test_a_scheduled_job_needs_only_the_environment_and_the_key():
    provider = provider_from_env(
        {"TJ_BILLING_PROVIDER": "paddle", "TJ_PADDLE_ENV": "sandbox", "TJ_PADDLE_API_KEY": API_KEY}
    )
    assert isinstance(provider, PaddleBilling)
    with pytest.raises(ValueError, match="TJ_PADDLE_ENV and TJ_PADDLE_API_KEY"):
        provider_from_env({"TJ_BILLING_PROVIDER": "paddle"})
    with pytest.raises(ValueError, match="TJ_PADDLE_ENV and TJ_PADDLE_API_KEY"):
        provider_from_env({"TJ_BILLING_PROVIDER": "paddle", "TJ_PADDLE_API_KEY": API_KEY})


# ---- customers -----------------------------------------------------------------------------------------------------------


def test_a_customer_is_made_with_the_address_and_the_persons_id_using_the_key(api, paddle):
    user = uuid4()
    customer = paddle.find_or_create_customer(email="sam@example.test", user_id=user)
    method, where, body = api.requests[0]
    assert (method, where) == ("POST", "/customers")
    assert body == {"email": "sam@example.test", "custom_data": {"roundturn_user_id": str(user)}}
    assert customer in api.customers


def test_someone_paddle_already_knows_gets_their_customer_back_from_the_conflict(api, paddle):
    first = paddle.find_or_create_customer(email="sam@example.test", user_id=uuid4())
    again = paddle.find_or_create_customer(email="Sam@Example.test", user_id=uuid4())
    assert again == first and calls(api) == [("POST", "/customers"), ("POST", "/customers")]


def test_a_conflict_that_does_not_name_the_customer_is_answered_by_looking_them_up(api, paddle):
    known = paddle.find_or_create_customer(email="sam@example.test", user_id=uuid4())
    api.answers.append(error(409, "customer_already_exists", "customer email conflicts with an existing customer"))
    assert paddle.find_or_create_customer(email="sam@example.test", user_id=uuid4()) == known
    assert calls(api)[-1] == ("GET", "/customers?email=sam%40example.test")


def test_any_other_refusal_is_the_providers_no(api, paddle):
    api.answers.append(error(400, "customer_email_invalid", "Cannot create or update customer with an invalid email."))
    with pytest.raises(PaddleRefused) as refused:
        paddle.find_or_create_customer(email="bad@example.test", user_id=uuid4())
    assert (
        refused.value.code == "customer_email_invalid"
        and refused.value.reason == "paddle_create_customer_customer_email_invalid"
    )


# ---- checkouts -----------------------------------------------------------------------------------------------------------


def test_with_no_free_days_the_checkout_is_the_catalog_price_with_the_persons_id(api, paddle):
    user = uuid4()
    customer = paddle.find_or_create_customer(email="a@example.test", user_id=user)
    checkout = paddle.create_checkout(customer_id=customer, user_id=user, free_days=0)
    _, where, body = api.requests[-1]
    assert where == "/transactions"
    assert body == {
        "items": [{"price_id": PRICE, "quantity": 1}],
        "customer_id": customer,
        "currency_code": "USD",
        "collection_mode": "automatic",
        "custom_data": {"roundturn_user_id": str(user)},
    }
    assert checkout.pay_url == f"/billing/pay?_ptxn={checkout.checkout_id}" and checkout.checkout_id in api.transactions


def test_with_free_days_the_first_charge_waits_for_them_and_the_card_is_asked_for_now(api, paddle):
    user = uuid4()
    customer = paddle.find_or_create_customer(email="a@example.test", user_id=user)
    paddle.create_checkout(customer_id=customer, user_id=user, free_days=5, discount_id="dsc_" + "0" * 24 + "07")
    body = api.requests[-1][2]
    assert body["items"] == [
        {
            "quantity": 1,
            "price": {
                "product_id": PRODUCT,
                "description": "Roundturn Pro monthly, first charge after the person's free days",
                "name": "Monthly",
                "billing_cycle": {"interval": "month", "frequency": 1},
                "trial_period": {"interval": "day", "frequency": 5, "requires_payment_method": True},
                "unit_price": {"amount": "1500", "currency_code": "USD"},
            },
        }
    ]
    assert body["discount_id"] == "dsc_" + "0" * 24 + "07"


def test_more_than_a_year_of_free_days_is_refused_before_paddle_is_asked(api, paddle):
    with pytest.raises(ProviderRefused, match="paddle_free_days_too_long"):
        paddle.create_checkout(customer_id="ctm_" + "0" * 26, user_id=uuid4(), free_days=366)
    assert api.requests == []


# ---- the provider's page, cancel, resume -----------------------------------------------------------------------------------


def subscribed(api, paddle, *, free_days=0):
    user = uuid4()
    customer = paddle.find_or_create_customer(email=f"{user}@example.test", user_id=user)
    checkout = paddle.create_checkout(customer_id=customer, user_id=user, free_days=free_days)
    api.complete(checkout.checkout_id)
    return customer, next(reversed(api.subscriptions))


def test_the_providers_page_is_a_fresh_signed_in_link_for_the_customer_and_their_subscription(api, paddle):
    customer, sub = subscribed(api, paddle)
    first = paddle.portal_url(customer_id=customer, subscription_id=sub)
    second = paddle.portal_url(customer_id=customer, subscription_id=None)
    assert first.startswith("https://customer-portal.paddle.com/") and first != second
    assert api.requests[-2][1:] == (f"/customers/{customer}/portal-sessions", {"subscription_ids": [sub]})
    assert api.requests[-1][2] == {}


def test_cancel_waits_for_the_period_end_resume_removes_it_and_cancel_now_ends_it(api, paddle):
    _, sub = subscribed(api, paddle)
    period_end = datetime.fromisoformat(api.subscriptions[sub]["current_billing_period"]["ends_at"])
    cancelled = paddle.cancel(sub)
    assert (cancelled.status, cancelled.cancel_at_period_end, cancelled.current_period_end) == (
        "active",
        True,
        period_end,
    )
    assert api.requests[-1][1:] == (f"/subscriptions/{sub}/cancel", {"effective_from": "next_billing_period"})
    resumed = paddle.resume(sub)
    assert resumed.cancel_at_period_end is False
    assert api.requests[-1] == ("PATCH", f"/subscriptions/{sub}", {"scheduled_change": None})
    ended = paddle.cancel_now(sub)
    assert (ended.status, ended.ended_at, ended.current_period_end) == ("canceled", NOW, None)
    assert api.requests[-1][2] == {"effective_from": "immediately"}
    assert paddle.cancel_now(sub).status == "canceled"  # ending one already ended is not an error
    with pytest.raises(PaddleRefused):
        paddle.resume(sub)  # a canceled Paddle subscription can never come back


def test_cancel_now_that_paddle_refuses_on_a_live_subscription_is_still_an_error(api, paddle):
    _, sub = subscribed(api, paddle)
    api.answers.append(error(400, "subscription_locked_pending_changes", "pending changes"))
    with pytest.raises(PaddleRefused):
        paddle.cancel_now(sub)
    assert calls(api)[-1] == ("GET", f"/subscriptions/{sub}")


def test_fetch_reads_the_subscription_and_an_unknown_one_is_refused(api, paddle):
    _, sub = subscribed(api, paddle, free_days=7)
    fetched = paddle.fetch_subscription(sub)
    assert (fetched.status, fetched.current_period_end) == ("trialing", NOW + timedelta(days=7))
    with pytest.raises(PaddleRefused) as refused:
        paddle.fetch_subscription("sub_" + "9" * 26)
    assert refused.value.code == "not_found"


@pytest.mark.parametrize("bad", ["sub_x/../customers", "sub_ABC" + "0" * 20, "ctm_" + "0" * 26 + "?a=b", ""])
def test_an_id_that_is_not_paddles_shape_never_reaches_a_request(api, paddle, bad):
    with pytest.raises(ProviderRefused, match="paddle_id_malformed"):
        paddle.cancel(bad)
    with pytest.raises(ProviderRefused, match="paddle_id_malformed"):
        paddle.portal_url(customer_id=bad, subscription_id=None)
    assert api.requests == []


# ---- discount codes ------------------------------------------------------------------------------------------------------


def test_a_discount_is_a_percentage_off_the_pro_product_usable_at_checkout_with_the_apps_code(api, paddle):
    expires = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    spec = DiscountSpec("LAUNCH20", 20, "repeating", months=3, max_uses=50, expires_at=expires, note="Launch week")
    discount = paddle.create_discount(spec)
    method, where, body = api.requests[-1]
    assert (method, where) == ("POST", "/discounts") and discount in api.discounts
    assert body == {
        "description": "LAUNCH20: Launch week",
        "type": "percentage",
        "amount": "20",
        "enabled_for_checkout": True,
        "code": "LAUNCH20",
        "recur": True,
        "usage_limit": 50,
        "expires_at": "2026-12-31T23:59:59Z",
        "restrict_to": [PRODUCT],  # the product: every price of it, a checkout's own price with free days included
        "maximum_recurring_intervals": 3,
    }


@pytest.mark.parametrize(
    ("duration", "months", "recurs"),
    [("once", None, {"recur": False}), ("forever", None, {"recur": True, "maximum_recurring_intervals": None})],
)
def test_once_does_not_recur_and_forever_recurs_without_a_limit(api, paddle, duration, months, recurs):
    paddle.create_discount(DiscountSpec("X" + duration.upper()[:5], 10, duration, months=months))
    body = api.requests[-1][2]
    assert {k: body[k] for k in recurs} == recurs
    if duration == "once":
        assert "maximum_recurring_intervals" not in body


def test_a_discount_paddle_already_holds_is_found_by_its_code_and_an_archived_one_is_not(api, paddle):
    assert paddle.find_discount("LAUNCH20") is None
    made = paddle.create_discount(DiscountSpec("LAUNCH20", 20, "once"))
    assert paddle.find_discount("launch20") == made
    assert api.requests[-1][1] == "/discounts?code=launch20&status=active"
    paddle.archive_discount(made)
    assert api.requests[-1] == ("PATCH", f"/discounts/{made}", {"status": "archived"})
    assert api.discounts[made]["status"] == "archived" and paddle.find_discount("LAUNCH20") is None


def test_a_code_paddle_already_has_is_paddles_no(api, paddle):
    paddle.create_discount(DiscountSpec("LAUNCH20", 20, "once"))
    with pytest.raises(PaddleRefused) as refused:
        paddle.create_discount(DiscountSpec("LAUNCH20", 30, "once"))
    assert refused.value.code == "discount_code_conflict"


# ---- when Paddle is busy, down or odd --------------------------------------------------------------------------------------


def test_busy_or_down_is_tried_again_twice_then_unavailable(api, paddle):
    api.answers += [error(503, "service_unavailable", "x"), httpx.Response(429), ok({"id": "ctm_" + "1" * 26})]
    assert paddle.find_or_create_customer(email="a@example.test", user_id=uuid4()) == "ctm_" + "1" * 26
    api.answers += [error(500, "internal_error", "x")] * 3
    with pytest.raises(ProviderUnavailable, match="paddle_create_customer_500"):
        paddle.find_or_create_customer(email="a@example.test", user_id=uuid4())
    assert len(api.requests) == 6


def test_a_request_that_never_left_is_tried_again_but_one_that_timed_out_is_not(api, paddle):
    api.answers += [httpx.ConnectError("refused"), ok({"id": "ctm_" + "1" * 26})]
    assert paddle.find_or_create_customer(email="a@example.test", user_id=uuid4()) == "ctm_" + "1" * 26
    api.answers.append(httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderUnavailable, match="timeout"):
        paddle.create_checkout(customer_id="ctm_" + "1" * 26, user_id=uuid4(), free_days=0)
    assert len(api.requests) == 3  # the timed-out checkout was asked for once: Paddle may have made it


def test_a_key_paddle_refuses_is_unavailable_never_the_persons_fault(api, caplog):
    paddle = api.provider()
    api.answers.append(error(403, "forbidden", "You aren't permitted to perform this request."))
    with caplog.at_level(logging.INFO, logger="tj.billing"), pytest.raises(ProviderUnavailable, match="403"):
        paddle.find_or_create_customer(email="sam@example.test", user_id=uuid4())
    assert "refused the API key" in caplog.text


@pytest.mark.parametrize(
    "answer", [httpx.Response(200, content=b"<html>"), httpx.Response(200, json={"meta": {}}), ok({"id": 5})]
)
def test_an_answer_that_cannot_be_read_is_unavailable(api, paddle, answer):
    api.answers.append(answer)
    with pytest.raises(ProviderUnavailable):
        paddle.find_or_create_customer(email="a@example.test", user_id=uuid4())


def test_logs_name_the_call_the_status_and_the_request_but_never_the_key_an_email_or_paddles_words(api, paddle, caplog):
    with caplog.at_level(logging.DEBUG):
        paddle.find_or_create_customer(email="sam@example.test", user_id=uuid4())
        paddle.find_or_create_customer(email="sam@example.test", user_id=uuid4())
    assert "paddle create_customer: 201 (request 00000000-0000-4000-8000-000000000001)" in caplog.text
    assert API_KEY not in caplog.text and "sam@example.test" not in caplog.text and "conflicts" not in caplog.text


# ---- notifications -------------------------------------------------------------------------------------------------------


def test_a_paddle_notification_is_checked_then_read_into_the_connectors_words(api, paddle):
    user = uuid4()
    customer = paddle.find_or_create_customer(email="a@example.test", user_id=user)
    checkout = paddle.create_checkout(customer_id=customer, user_id=user, free_days=7)
    delivery = api.complete(checkout.checkout_id)
    event = paddle.verify_webhook(delivery.headers, delivery.body)
    sub = event.subscription
    assert (event.event_id, event.type, event.kind, event.occurred_at) == (
        delivery.event_id,
        "subscription.created",
        "subscription",
        NOW,
    )
    assert (sub.status, sub.customer_id, sub.current_period_end, sub.user_ref, sub.checkout_id) == (
        "trialing",
        customer,
        NOW + timedelta(days=7),
        str(user),
        checkout.checkout_id,
    )


def test_headers_are_read_whatever_their_case(api, paddle):
    _, sub = subscribed(api, paddle)
    delivery = api.announce(sub)
    lowered = {k.lower(): v for k, v in delivery.headers.items()}
    assert paddle.verify_webhook(lowered, delivery.body).subscription.subscription_id == sub


def test_a_bad_signature_a_missing_one_or_no_secret_configured_is_refused(api, paddle):
    _, sub = subscribed(api, paddle)
    delivery = api.announce(sub)
    with pytest.raises(BadSignature):
        paddle.verify_webhook({}, delivery.body)
    with pytest.raises(BadSignature):
        paddle.verify_webhook(delivery.headers, delivery.body.replace(b"active", b"paused"))
    other = FakePaddleApi(clock=lambda: NOW, wall=lambda: NOW.timestamp(), secret="pdl_" + "ntfset_" + "other" * 3)
    with pytest.raises(BadSignature):
        paddle.verify_webhook(other.deliver("subscription.updated", api.subscriptions[sub]).headers, delivery.body)
    job = api.provider(webhook_secret=None)  # the sweep's connector has no secret: it never reads a notification
    with pytest.raises(BadSignature):
        job.verify_webhook(delivery.headers, delivery.body)
    late = PaddleBilling(settings(), wall=lambda: NOW.timestamp() + 6)
    with pytest.raises(BadSignature):
        late.verify_webhook(delivery.headers, delivery.body)


def test_a_signed_body_that_is_not_a_notification_is_refused(api, paddle):
    for body in (
        b"not json",
        json.dumps({"event_type": "subscription.updated"}).encode(),
        b'{"event_id":"e","event_type":"x","occurred_at":"yesterday","data":{}}',
    ):
        headers = {"Paddle-Signature": signature.sign(WEBHOOK_SECRET, body, int(NOW.timestamp()))}
        with pytest.raises(BadSignature):
            paddle.verify_webhook(headers, body)


def test_a_scheduled_cancel_ends_at_its_effective_time_and_a_canceled_one_has_no_period():
    data = {
        "id": "sub_" + "0" * 26,
        "status": "active",
        "customer_id": "ctm_" + "0" * 26,
        "current_billing_period": {"starts_at": "2026-10-01T00:00:00Z", "ends_at": "2026-11-01T00:00:00.123456789Z"},
        "scheduled_change": {"action": "cancel", "effective_at": "2026-11-01T00:00:00Z", "resume_at": None},
        "canceled_at": None,
        "custom_data": None,
    }
    cancelling = subscription_from_paddle(data)
    assert (cancelling.cancel_at_period_end, cancelling.current_period_end) == (True, datetime(2026, 11, 1, tzinfo=UTC))
    paused_soon = subscription_from_paddle(
        {**data, "scheduled_change": {"action": "pause", "effective_at": "2026-11-01T00:00:00Z"}}
    )
    assert paused_soon.cancel_at_period_end is False
    assert paused_soon.current_period_end == datetime(
        2026, 11, 1, 0, 0, 0, 123456, tzinfo=UTC
    )  # nine digits read as six
    ended = subscription_from_paddle(
        {
            **data,
            "status": "canceled",
            "current_billing_period": None,
            "scheduled_change": None,
            "canceled_at": "2026-10-20T08:00:00Z",
        }
    )
    assert (ended.status, ended.current_period_end, ended.ended_at) == (
        "canceled",
        None,
        datetime(2026, 10, 20, 8, tzinfo=UTC),
    )


def test_a_refund_or_a_chargeback_is_read_as_one_and_a_payment_as_a_payment(api, paddle):
    _, sub = subscribed(api, paddle)
    delivery = api.refund(sub, amount="1500", action="chargeback")
    event = paddle.verify_webhook(delivery.headers, delivery.body)
    assert (
        event.kind,
        event.refund.action,
        event.refund.amount,
        event.refund.currency,
        event.refund.subscription_id,
    ) == (
        "refund",
        "chargeback",
        "1500",
        "USD",
        sub,
    )
    paid = api.deliver(
        "transaction.completed",
        {"id": "txn_" + "5" * 26, "status": "completed", "subscription_id": sub, "customer_id": "ctm_x"},
    )
    assert paddle.verify_webhook(paid.headers, paid.body).kind == "payment"


def test_what_is_kept_holds_ids_statuses_dates_and_amounts_and_never_a_name_an_email_or_an_address():
    data = {
        "id": "sub_" + "0" * 26,
        "status": "past_due",
        "customer_id": "ctm_" + "0" * 26,
        "address_id": "add_" + "0" * 26,
        "current_billing_period": {"starts_at": "2026-10-01T00:00:00Z", "ends_at": "2026-11-01T00:00:00Z"},
        "scheduled_change": None,
        "custom_data": {"roundturn_user_id": "u-1", "note": "kept out"},
        "billing_details": {"additional_information": "Sam Smith, 1 Main St"},
        "items": [{"product": {"name": "Roundturn Pro"}}],
    }
    event = read_event("evt_1", "subscription.past_due", NOW, data)
    kept = json.dumps(event.payload)
    assert event.subscription.status == "past_due" and "roundturn_user_id" in kept
    for leaked in ("Sam Smith", "Main St", "kept out", "add_", "items"):
        assert leaked not in kept
    customer = read_event("evt_2", "customer.updated", NOW, {"id": "ctm_1", "email": "sam@example.test", "name": "Sam"})
    assert customer.kind == "unknown" and customer.payload == {"event_type": "customer.updated"}


def test_a_signed_subscription_of_a_shape_not_read_is_kept_as_unknown():
    for data in (
        {"id": "sub_" + "0" * 26, "status": "weird"},
        {"id": "not-a-sub", "status": "active"},
        {"status": "active"},
    ):
        assert read_event("evt_1", "subscription.updated", NOW, data).kind == "unknown"
