"""The Stripe connector (billing/stripe.py) against a stand-in for Stripe's API (tests/stripe_world.py): its settings, every
call it makes, how it retries and logs, the webhook's signature, and how each event is read. Built beside the Paddle
connector; the shared contract tests run both through the whole app (tests/pg/test_pg_billing_stripe.py)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from stripe_world import API_KEY, LIVE_KEY, ORIGIN, PRICE, PRODUCT, WEBHOOK_SECRET, FakeStripeApi, settings

from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.billing.factory import provider_for, provider_from_env
from tradejournal.billing.stripe import (
    API_VERSION,
    MIN_TRIAL_DAYS,
    StripeBilling,
    StripeSettings,
    form_pairs,
    read_event,
    sign,
    subscription_from_stripe,
    verify_signature,
)
from tradejournal.billing.types import (
    BadSignature,
    DiscountSpec,
    ProviderRefused,
    ProviderUnavailable,
)

PERSON = UUID("11111111-2222-4333-8444-555555555555")
OTHER = UUID("66666666-7777-4888-8999-000000000000")
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
FULL_ENV = {
    "TJ_BILLING_PROVIDER": "stripe",
    "TJ_STRIPE_ENV": "test",
    "TJ_STRIPE_API_KEY": API_KEY,
    "TJ_STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET,
    "TJ_STRIPE_PRODUCT_ID": PRODUCT,
    "TJ_STRIPE_PRICE_ID": PRICE,
}


@pytest.fixture
def api():
    return FakeStripeApi(clock=lambda: NOW)


@pytest.fixture
def stripe(api):
    return api.provider()


def checkout_of(api, stripe, *, free_days=0, discount=None):
    customer = stripe.find_or_create_customer(email="alice@example.com", user_id=PERSON)
    checkout = stripe.create_checkout(customer_id=customer, user_id=PERSON, free_days=free_days, discount_id=discount)
    return customer, checkout, api.sessions[checkout.checkout_id]["form"]


def subscribed(api, stripe, **kwargs):
    _, checkout, _ = checkout_of(api, stripe, **kwargs)
    api.complete(checkout.checkout_id)
    return next(reversed(api.subscriptions))


# ---- settings -------------------------------------------------------------------------------------------------------


def test_the_web_server_takes_the_stripe_settings_and_builds_the_stripe_connector():
    config = ApiConfig.from_env(FULL_ENV)
    assert config.billing_provider == "stripe" and config.stripe is not None and config.stripe.complete_for_web
    connector = provider_for(config, clock=lambda: NOW)
    assert isinstance(connector, StripeBilling) and connector.name == "stripe"
    assert (config.stripe.product_id, config.stripe.price_id, config.stripe.managed_payments) == (PRODUCT, PRICE, True)


@pytest.mark.parametrize(
    "missing", ["TJ_STRIPE_WEBHOOK_SECRET", "TJ_STRIPE_PRODUCT_ID", "TJ_STRIPE_PRICE_ID", "TJ_STRIPE_ENV"]
)
def test_the_web_server_refuses_stripe_with_any_setting_missing(missing):
    env = {k: v for k, v in FULL_ENV.items() if k != missing}
    with pytest.raises(ConfigError, match="TJ_STRIPE"):
        ApiConfig.from_env(env)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"TJ_STRIPE_ENV": "live"}, "is a test key, but TJ_STRIPE_ENV is live"),
        ({"TJ_STRIPE_API_KEY": LIVE_KEY}, "is a live key, but TJ_STRIPE_ENV is test"),
        ({"TJ_STRIPE_API_KEY": "pk_" + "test_" + "x" * 20}, "secret key \\(sk_...\\) or a restricted key"),
        ({"TJ_STRIPE_ENV": "sandbox"}, "'test' or 'live'"),
        ({"TJ_STRIPE_WEBHOOK_SECRET": "secret"}, "signing secret \\(whsec_"),
        ({"TJ_STRIPE_PRODUCT_ID": "pro_123"}, "product id \\(prod_"),
        ({"TJ_STRIPE_PRICE_ID": "pri_123"}, "price id \\(price_"),
        ({"TJ_STRIPE_MANAGED_PAYMENTS": "maybe"}, "true or false"),
    ],
)
def test_a_mixed_or_mistyped_set_stops_the_server_and_names_the_setting_not_its_value(changes, message):
    with pytest.raises(ConfigError, match=message) as problem:
        ApiConfig.from_env({**FULL_ENV, **changes})
    assert API_KEY not in str(problem.value) and WEBHOOK_SECRET not in str(problem.value)


def test_managed_payments_is_on_unless_switched_off():
    assert StripeSettings.from_env(FULL_ENV).managed_payments is True
    assert StripeSettings.from_env({**FULL_ENV, "TJ_STRIPE_MANAGED_PAYMENTS": "false"}).managed_payments is False


def test_the_key_and_the_webhook_secret_are_never_shown():
    config = ApiConfig.from_env(FULL_ENV)
    for text in (repr(config), repr(config.stripe)):
        assert API_KEY not in text and WEBHOOK_SECRET not in text


def test_a_scheduled_job_needs_only_the_environment_and_the_key():
    connector = provider_from_env(
        {"TJ_BILLING_PROVIDER": "stripe", "TJ_STRIPE_ENV": "test", "TJ_STRIPE_API_KEY": API_KEY}
    )
    assert isinstance(connector, StripeBilling)
    with pytest.raises(ValueError, match="needs TJ_STRIPE_ENV and TJ_STRIPE_API_KEY"):
        provider_from_env({"TJ_BILLING_PROVIDER": "stripe"})
    with pytest.raises(ValueError, match="'fake', 'paddle' or 'stripe'"):
        provider_from_env({"TJ_BILLING_PROVIDER": "lemonsqueezy"})


def test_paddle_is_still_built_when_it_is_the_one_named():
    from paddle_world import settings as paddle_settings

    from tradejournal.billing.paddle import PaddleBilling

    config = ApiConfig(billing_provider="paddle", paddle=paddle_settings(), stripe=settings())
    assert isinstance(provider_for(config, clock=lambda: NOW), PaddleBilling)


# ---- customers and checkouts ----------------------------------------------------------------------------------------


def test_a_customer_is_made_once_per_person_with_the_persons_id(api, stripe):
    first = stripe.find_or_create_customer(email="alice@example.com", user_id=PERSON)
    method, path, form, key = api.requests[-1]
    assert (method, path) == ("POST", "/v1/customers") and key == f"rt-customer-{PERSON}"
    assert form == {"email": "alice@example.com", "metadata[roundturn_user_id]": str(PERSON)}
    assert stripe.find_or_create_customer(email="alice@example.com", user_id=PERSON) == first  # found by the id
    assert api.requests[-1][0] == "GET" and len(api.customers) == 1


def test_with_no_free_days_the_checkout_charges_now_for_the_price_with_the_persons_id(api, stripe):
    _, checkout, form = checkout_of(api, stripe)
    assert checkout.pay_url == f"/billing/stripe/pay?cs={checkout.checkout_id}"
    assert form["mode"] == "subscription" and form["line_items[0][price]"] == PRICE
    assert form["line_items[0][quantity]"] == "1" and form["client_reference_id"] == str(PERSON)
    assert form["subscription_data[metadata][roundturn_user_id]"] == str(PERSON)
    assert "subscription_data[trial_period_days]" not in form
    assert form["success_url"] == f"{ORIGIN}/settings/plan?checkout=done"
    assert form["cancel_url"] == f"{ORIGIN}/settings/plan"
    assert form["managed_payments[enabled]"] == "true"
    assert not [k for k in form if k.startswith(("allowed_payment_method_types", "automatic_tax", "customer_update"))]


def test_with_free_days_the_first_charge_waits_for_them_and_one_day_left_is_given_as_two(api, stripe):
    assert checkout_of(api, stripe, free_days=5)[2]["subscription_data[trial_period_days]"] == "5"
    assert checkout_of(api, stripe, free_days=1)[2]["subscription_data[trial_period_days]"] == str(MIN_TRIAL_DAYS)


def test_more_than_a_year_of_free_days_is_refused_before_stripe_is_asked(api, stripe):
    customer = stripe.find_or_create_customer(email="alice@example.com", user_id=PERSON)
    asked = len(api.requests)
    with pytest.raises(ProviderRefused) as problem:
        stripe.create_checkout(customer_id=customer, user_id=PERSON, free_days=366)
    assert "more than a year" in problem.value.detail and len(api.requests) == asked


def test_when_roundturn_sells_the_checkout_adds_tax_the_address_and_cards_only(api):
    stripe = api.provider(managed_payments=False)
    form = checkout_of(api, stripe)[2]
    assert form["managed_payments[enabled]"] == "false" and form["automatic_tax[enabled]"] == "true"
    assert form["billing_address_collection"] == "required" and form["allowed_payment_method_types[0]"] == "card"


def test_a_new_checkout_closes_the_persons_open_one_so_two_cannot_both_be_paid(api, stripe):
    _, first, _ = checkout_of(api, stripe)
    _, second, _ = checkout_of(api, stripe)
    assert (
        api.sessions[first.checkout_id]["status"] == "expired" and api.sessions[second.checkout_id]["status"] == "open"
    )
    assert ("POST", f"/v1/checkout/sessions/{first.checkout_id}/expire") in [r[:2] for r in api.requests]


def test_every_post_carries_an_idempotency_key_and_the_pinned_version(api, stripe):
    seen = []
    transport = httpx.MockTransport(lambda request: (seen.append(request), api.handle(request))[1])
    connector = StripeBilling(settings(), app_origin=ORIGIN, http=httpx.Client(transport=transport))
    checkout_of(api, connector)
    assert all(r.headers["stripe-version"] == API_VERSION for r in seen)
    assert all(r.headers.get("idempotency-key") for r in seen if r.method == "POST")


def test_without_an_app_origin_no_checkout_or_portal_link_is_made(api):
    stripe = api.provider(origin=None)
    customer = stripe.find_or_create_customer(email="alice@example.com", user_id=PERSON)
    with pytest.raises(ProviderUnavailable, match="not_configured"):
        stripe.create_checkout(customer_id=customer, user_id=PERSON, free_days=0)
    with pytest.raises(ProviderUnavailable, match="not_configured"):
        stripe.portal_url(customer_id=customer, subscription_id=None)


# ---- the payment link -----------------------------------------------------------------------------------------------


def test_the_payment_link_goes_to_stripes_page_only_for_the_persons_open_checkout(api, stripe):
    _, checkout, _ = checkout_of(api, stripe)
    assert stripe.checkout_destination(checkout.checkout_id, PERSON).startswith("https://checkout.stripe.com/")
    assert stripe.checkout_destination(checkout.checkout_id, OTHER) == "/settings/plan"
    api.complete(checkout.checkout_id)
    assert stripe.checkout_destination(checkout.checkout_id, PERSON) == "/settings/plan?checkout=done"


def test_an_expired_checkout_goes_back_to_the_plan_page(api, stripe):
    _, checkout, _ = checkout_of(api, stripe)
    api.sessions[checkout.checkout_id].update(status="expired", url=None)
    assert stripe.checkout_destination(checkout.checkout_id, PERSON) == "/settings/plan"


@pytest.mark.parametrize("bad", ["cs_test_short", "cs_live_../../v1/customers", "sub_" + "a" * 20, ""])
def test_a_checkout_id_that_is_not_stripes_shape_never_reaches_a_request(api, stripe, bad):
    asked = len(api.requests)
    with pytest.raises(ProviderRefused):
        stripe.checkout_destination(bad, PERSON)
    assert len(api.requests) == asked


# ---- managing a subscription ----------------------------------------------------------------------------------------


def test_the_providers_page_is_a_fresh_portal_link_for_the_customer(api, stripe):
    customer, _, _ = checkout_of(api, stripe)
    url = stripe.portal_url(customer_id=customer, subscription_id=None)
    assert url.startswith("https://billing.stripe.com/")
    assert api.requests[-1][2] == {"customer": customer, "return_url": f"{ORIGIN}/settings/plan"}


def test_cancel_waits_for_the_period_end_resume_removes_it_and_cancel_now_ends_it(api, stripe):
    sub = subscribed(api, stripe, free_days=5)
    cancelled = stripe.cancel(sub)
    assert api.requests[-1][:3] == ("POST", f"/v1/subscriptions/{sub}", {"cancel_at_period_end": "true"})
    assert (cancelled.status, cancelled.cancel_at_period_end, cancelled.ended_at) == ("trialing", True, None)
    assert cancelled.current_period_end == NOW + timedelta(days=5)  # Stripe's canceled_at is not the end
    resumed = stripe.resume(sub)
    assert (resumed.cancel_at_period_end, resumed.current_period_end) == (False, NOW + timedelta(days=5))
    ended = stripe.cancel_now(sub)
    assert (ended.status, ended.current_period_end, ended.ended_at) == ("canceled", None, NOW)
    assert api.requests[-1][:2] == ("DELETE", f"/v1/subscriptions/{sub}")


def test_cancel_now_on_one_already_ended_reads_it_back_and_counts_it_done(api, stripe):
    sub = subscribed(api, stripe)
    stripe.cancel_now(sub)
    again = stripe.cancel_now(sub)  # Stripe: 404 "No such subscription"; a read still shows it
    assert again.status == "canceled" and api.requests[-1][:2] == ("GET", f"/v1/subscriptions/{sub}")


def test_cancel_now_that_stripe_refuses_on_a_live_subscription_is_still_an_error(api, stripe):
    from stripe_world import error

    sub = subscribed(api, stripe)
    api.answers.append(error(400, "resource_locked"))
    with pytest.raises(ProviderRefused):
        stripe.cancel_now(sub)


def test_resume_of_an_ended_subscription_is_stripes_no(api, stripe):
    sub = subscribed(api, stripe)
    stripe.cancel_now(sub)
    with pytest.raises(ProviderRefused):
        stripe.resume(sub)


def test_fetch_reads_the_subscription_and_an_unknown_one_is_refused(api, stripe):
    sub = subscribed(api, stripe)
    fetched = stripe.fetch_subscription(sub)
    assert (fetched.subscription_id, fetched.status, fetched.user_ref) == (sub, "active", str(PERSON))
    with pytest.raises(ProviderRefused):
        stripe.fetch_subscription("sub_" + "Unknown" * 3)


@pytest.mark.parametrize("bad", ["sub_x", "cus_" + "a" * 10, "sub_../../customers", "SUB_" + "a" * 10])
def test_a_subscription_id_that_is_not_stripes_shape_never_reaches_a_request(api, stripe, bad):
    with pytest.raises(ProviderRefused):
        stripe.cancel(bad)
    assert api.requests == []


# ---- discount codes -------------------------------------------------------------------------------------------------


def spec(**changes):
    values = {"code": "LAUNCH20", "percent_off": 20, "duration": "repeating", "months": 3, "max_uses": 50}
    return DiscountSpec(**{**values, "expires_at": datetime(2027, 1, 1, tzinfo=UTC), **changes})


def test_a_discount_is_a_coupon_for_the_pro_product_and_a_promotion_code_people_type(api, stripe):
    promotion = stripe.create_discount(spec())
    coupon = api.coupons["rt-launch20"]
    assert (coupon["percent_off"], coupon["duration"], coupon["duration_in_months"]) == (20.0, "repeating", 3)
    assert coupon["applies_to"]["products"] == [PRODUCT] and coupon["max_redemptions"] == 50
    assert coupon["redeem_by"] == int(datetime(2027, 1, 1, tzinfo=UTC).timestamp())
    code = api.promotions[promotion]
    assert (code["code"], code["promotion"]["coupon"], code["active"]) == ("LAUNCH20", "rt-launch20", True)
    form = checkout_of(api, stripe, discount=promotion)[2]
    assert form["discounts[0][promotion_code]"] == promotion


@pytest.mark.parametrize(("duration", "months"), [("once", None), ("forever", None)])
def test_once_and_forever_have_no_months(api, stripe, duration, months):
    stripe.create_discount(spec(duration=duration, months=months))
    assert api.coupons["rt-launch20"]["duration"] == duration
    assert "duration_in_months" not in api.requests[0][2]


def test_a_retry_after_a_timeout_adopts_the_coupon_it_made_but_refuses_one_with_other_terms(api, stripe):
    stripe.create_discount(spec())
    api.promotions.clear()  # the promotion code never got made: the retry makes it, and reuses the coupon
    assert stripe.create_discount(spec()) in api.promotions
    api.promotions.clear()
    with pytest.raises(ProviderRefused, match="coupon_conflict"):
        stripe.create_discount(spec(percent_off=50))


def test_a_discount_stripe_already_holds_is_found_by_its_code_and_a_switched_off_one_is_not(api, stripe):
    promotion = stripe.create_discount(spec())
    assert stripe.find_discount("launch20") == promotion
    stripe.archive_discount(promotion)
    assert api.promotions[promotion]["active"] is False and stripe.find_discount("LAUNCH20") is None
    stripe.archive_discount(promotion)  # switching off one already off is not an error


def test_a_code_stripe_already_has_active_is_stripes_no(api, stripe):
    stripe.create_discount(spec())
    api.coupons.pop("rt-launch20")
    with pytest.raises(ProviderRefused):
        stripe.create_discount(spec())


# ---- retries, refusals and logs -------------------------------------------------------------------------------------


def test_busy_or_down_is_tried_again_twice_with_the_same_key_then_unavailable(api, stripe):
    api.answers += [httpx.Response(503), httpx.Response(429), httpx.Response(500)]
    with pytest.raises(ProviderUnavailable, match="stripe_portal_session_500"):
        stripe.portal_url(customer_id="cus_" + "Fake" * 4, subscription_id=None)
    keys = [r[3] for r in api.requests]
    assert len(keys) == 3 and keys[0] and len(set(keys)) == 1


def test_stripe_saying_do_not_retry_is_obeyed(api, stripe):
    api.answers.append(httpx.Response(500, headers={"Stripe-Should-Retry": "false"}))
    with pytest.raises(ProviderUnavailable):
        stripe.fetch_subscription("sub_" + "Fake" * 4)
    assert len(api.requests) == 1


def test_no_answer_is_tried_again_with_the_same_idempotency_key(api, stripe):
    api.answers += [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")]
    customer = stripe.find_or_create_customer(email="alice@example.com", user_id=PERSON)
    posts = [r for r in api.requests if r[0] == "POST"]
    assert customer in api.customers and len(api.customers) == 1
    assert {r[3] for r in posts} == {f"rt-customer-{PERSON}"}


def test_a_key_stripe_refuses_is_unavailable_never_the_persons_fault(api, caplog):
    stripe = StripeBilling(
        StripeSettings(environment="test", api_key="sk_" + "test_" + "Other" * 5),
        app_origin=ORIGIN,
        http=httpx.Client(transport=httpx.MockTransport(api.handle)),
        sleep=lambda _s: None,
    )
    with caplog.at_level(logging.ERROR, logger="tj.billing"), pytest.raises(ProviderUnavailable, match="401"):
        stripe.fetch_subscription("sub_" + "Fake" * 4)
    assert "refused the API key" in caplog.text


@pytest.mark.parametrize("answer", [httpx.Response(200, text="<html>"), httpx.Response(200, json=[1, 2])])
def test_an_answer_that_cannot_be_read_is_unavailable(api, stripe, answer):
    api.answers.append(answer)
    with pytest.raises(ProviderUnavailable):
        stripe.fetch_subscription("sub_" + "Fake" * 4)


def test_logs_name_the_call_the_status_and_the_request_never_the_key_an_email_or_stripes_words(api, stripe, caplog):
    with caplog.at_level(logging.INFO, logger="tj.billing"):
        checkout_of(api, stripe)
        with pytest.raises(ProviderRefused):
            stripe.fetch_subscription("sub_" + "Unknown" * 3)
    assert "stripe create_checkout: 200 (request req_Fake0000000001)" in caplog.text
    for secret in (API_KEY, "alice@example.com", "never read by the connector"):
        assert secret not in caplog.text


def test_form_fields_are_stripes_bracketed_names():
    assert form_pairs({"a": {"b": [{"c": 1}, {"c": True}]}, "d": None, "e": False}) == [
        ("a[b][0][c]", "1"),
        ("a[b][1][c]", "true"),
        ("e", "false"),
    ]


# ---- the webhook ----------------------------------------------------------------------------------------------------


def test_a_stripe_event_is_checked_then_read_into_the_connectors_words(api, stripe):
    sub = subscribed(api, stripe, free_days=5)
    delivery = api.announce(sub, "customer.subscription.created")
    event = stripe.verify_webhook(delivery.headers, delivery.body)
    assert (event.event_id, event.type, event.kind, event.occurred_at) == (
        delivery.event_id,
        "customer.subscription.created",
        "subscription",
        NOW,
    )
    got = event.subscription
    assert (got.subscription_id, got.status, got.user_ref, got.checkout_id) == (sub, "trialing", str(PERSON), None)
    assert got.customer_id in api.customers and got.current_period_end == NOW + timedelta(days=5)


def test_headers_are_read_whatever_their_case(api, stripe):
    delivery = api.announce(subscribed(api, stripe))
    headers = {k.lower(): v for k, v in delivery.headers.items()}
    assert stripe.verify_webhook(headers, delivery.body).kind == "subscription"


def test_a_bad_signature_an_old_one_a_missing_one_or_no_secret_configured_is_refused(api, stripe):
    delivery = api.announce(subscribed(api, stripe))
    with pytest.raises(BadSignature):
        stripe.verify_webhook(delivery.headers, delivery.body + b" ")
    with pytest.raises(BadSignature):
        stripe.verify_webhook({}, delivery.body)
    with pytest.raises(BadSignature):
        api.provider(webhook_secret=None).verify_webhook(delivery.headers, delivery.body)
    old = sign(WEBHOOK_SECRET, delivery.body, int(api.wall()) - 301)
    with pytest.raises(BadSignature):
        stripe.verify_webhook({"Stripe-Signature": old}, delivery.body)


def test_during_a_secret_roll_either_signature_is_enough():
    body, now = b'{"id": "evt_x"}', 1_800_000_000
    header = sign("whsec_" + "Old" * 6, body, now) + "," + sign(WEBHOOK_SECRET, body, now).split(",")[1]
    assert verify_signature(header, body, WEBHOOK_SECRET, now=now)
    assert not verify_signature(header, body, "whsec_" + "Other" * 5, now=now)


def test_a_signed_body_that_is_not_an_event_is_refused(api, stripe):
    body = json.dumps({"hello": "world"}).encode()
    headers = {"Stripe-Signature": sign(WEBHOOK_SECRET, body, int(api.wall()))}
    with pytest.raises(BadSignature, match="malformed"):
        stripe.verify_webhook(headers, body)


def test_an_event_of_the_other_mode_is_kept_but_never_applied(api, stripe, caplog):
    sub = subscribed(api, stripe)
    delivery = api.deliver("customer.subscription.updated", api.subscriptions[sub], livemode=True)
    with caplog.at_level(logging.WARNING, logger="tj.billing"):
        event = stripe.verify_webhook(delivery.headers, delivery.body)
    assert (event.kind, event.subscription, event.payload["ignored"]) == ("unknown", None, "other_mode")
    assert "other mode" in caplog.text


def sub_data(**changes):
    period_end = int((NOW + timedelta(days=30)).timestamp())
    data = {
        "id": "sub_" + "Fake" * 4,
        "status": "active",
        "customer": "cus_" + "Fake" * 4,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "canceled_at": None,
        "ended_at": None,
        "trial_end": None,
        "items": {"data": [{"current_period_end": period_end}]},
        "metadata": {"roundturn_user_id": str(PERSON)},
    }
    return {**data, **changes}


def at(days):
    return int((NOW + timedelta(days=days)).timestamp())


@pytest.mark.parametrize(
    ("changes", "status", "cancelling", "end"),
    [
        ({}, "active", False, NOW + timedelta(days=30)),
        ({"status": "trialing"}, "trialing", False, NOW + timedelta(days=30)),
        (
            {"cancel_at_period_end": True, "cancel_at": at(30), "canceled_at": at(0)},
            "active",
            True,
            NOW + timedelta(days=30),
        ),
        ({"cancel_at": at(20)}, "active", True, NOW + timedelta(days=20)),  # a cancel set by date, from the portal
        ({"status": "past_due"}, "past_due", False, NOW + timedelta(days=30)),
        ({"status": "unpaid"}, "past_due", False, NOW + timedelta(days=30)),
        ({"status": "canceled", "ended_at": at(0), "canceled_at": at(0)}, "canceled", False, None),
        ({"status": "incomplete_expired"}, "canceled", False, None),
        ({"status": "paused"}, "paused", False, NOW + timedelta(days=30)),
    ],
)
def test_stripes_states_in_roundturns_words(changes, status, cancelling, end):
    got = subscription_from_stripe(sub_data(**changes))
    assert (got.status, got.cancel_at_period_end, got.current_period_end) == (status, cancelling, end)


def test_an_ended_subscription_says_when_it_ended_not_when_the_cancel_was_asked_for():
    got = subscription_from_stripe(sub_data(status="canceled", canceled_at=at(-10), ended_at=at(0)))
    assert got.ended_at == NOW and subscription_from_stripe(sub_data(canceled_at=at(-1))).ended_at is None


def test_during_free_days_with_no_item_period_the_trial_end_is_the_period_end():
    got = subscription_from_stripe(sub_data(status="trialing", items={"data": []}, trial_end=at(5)))
    assert got.current_period_end == NOW + timedelta(days=5)


def test_an_incomplete_subscription_is_kept_as_unknown_and_grants_nothing():
    event = read_event("evt_" + "Fake" * 4, "customer.subscription.created", NOW, sub_data(status="incomplete"))
    assert (event.kind, event.subscription) == ("unknown", None)


def test_a_refund_or_a_chargeback_is_read_as_one_and_an_invoice_or_a_checkout_as_a_payment(api, stripe):
    sub = subscribed(api, stripe)
    refund = stripe.verify_webhook(*_parts(api.refund(sub)))
    assert (refund.kind, refund.refund.action, refund.refund.amount, refund.refund.currency) == (
        "refund",
        "refund",
        "1500",
        "USD",
    )
    dispute = read_event(
        "evt_" + "Fake" * 4,
        "charge.dispute.created",
        NOW,
        {"id": "dp_" + "Fake" * 4, "amount": 1500, "currency": "usd"},
    )
    assert (dispute.kind, dispute.refund.action) == ("refund", "chargeback")
    invoice = read_event("evt_" + "Fake" * 4, "invoice.payment_failed", NOW, _invoice())
    assert invoice.kind == "payment" and invoice.payload["data"]["subscription"] == "sub_" + "Fake" * 4
    session = api.sessions[next(iter(api.sessions))]["id"]
    completed = stripe.verify_webhook(*_parts(api.checkout_completed(session)))
    assert completed.kind == "payment" and completed.payload["data"]["subscription"] == sub


def test_what_is_kept_holds_ids_statuses_dates_and_amounts_and_never_a_name_an_email_or_an_address(api, stripe):
    sub = subscribed(api, stripe)
    session = next(iter(api.sessions))
    kept = [
        stripe.verify_webhook(*_parts(api.announce(sub))).payload,
        stripe.verify_webhook(*_parts(api.refund(sub))).payload,
        stripe.verify_webhook(*_parts(api.checkout_completed(session))).payload,
        read_event("evt_" + "Fake" * 4, "invoice.paid", NOW, _invoice()).payload,
    ]
    text = json.dumps(kept)
    for personal in ("alice@example.com", "someone@example.com", "Some One", "1 Main St", "customer_details"):
        assert personal not in text


def test_a_signed_event_of_a_type_not_read_is_kept_as_unknown():
    event = read_event("evt_" + "Fake" * 4, "customer.created", NOW, {"id": "cus_x", "email": "alice@example.com"})
    assert event.kind == "unknown" and event.payload == {"event_type": "customer.created"}


def _parts(delivery):
    return delivery.headers, delivery.body


def _invoice():
    return {
        "id": "in_" + "Fake" * 4,
        "status": "open",
        "billing_reason": "subscription_cycle",
        "amount_due": 1500,
        "amount_paid": 0,
        "currency": "usd",
        "attempt_count": 1,
        "customer": "cus_" + "Fake" * 4,
        "customer_email": "alice@example.com",
        "customer_name": "Alice",
        "parent": {"subscription_details": {"subscription": "sub_" + "Fake" * 4, "metadata": {}}},
    }
