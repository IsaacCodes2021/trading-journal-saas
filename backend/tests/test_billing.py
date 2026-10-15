"""Billing without a database: the settings and what they refuse, the webhook signature, the fake provider keeping the
connector's contract, and the price being the one published on roundturn.app."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.billing import price, signature
from tradejournal.billing.factory import provider_for, provider_from_env
from tradejournal.billing.fake import MONTH, FakeBilling
from tradejournal.billing.types import BadSignature, ProviderRefused, ProviderUnavailable

SECRET = "ab" * 32
PRODUCTION = {"TJ_ENV": "production", "TJ_APP_ORIGIN": "https://app.example.com", "TJ_SESSION_SECRET": SECRET}
HOOK_SECRET = "hook-" + "secret"
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[2]


# ---- the settings --------------------------------------------------------------------------------------------------------


def test_billing_is_off_unless_switched_on():
    config = ApiConfig.from_env({})
    assert (config.billing_enabled, config.billing_provider) == (False, None)
    assert provider_for(config, clock=lambda: NOW) is None


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_the_switch_reads_the_usual_words_for_on(value):
    config = ApiConfig.from_env({"TJ_BILLING_ENABLED": value, "TJ_BILLING_PROVIDER": "fake"})
    assert config.billing_enabled and isinstance(provider_for(config, clock=lambda: NOW), FakeBilling)


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"TJ_BILLING_ENABLED": "true"}, "needs TJ_BILLING_PROVIDER"),
        ({"TJ_BILLING_ENABLED": "maybe"}, "true or false"),
        ({"TJ_BILLING_PROVIDER": "stripe"}, "'fake' or 'paddle'"),
        ({"TJ_WEBHOOK_DATABASE_URL": "mysql://x"}, "postgresql://"),
    ],
)
def test_billing_settings_that_make_no_sense_are_refused(env, message):
    with pytest.raises(ConfigError, match=message):
        ApiConfig.from_env(env)


def test_production_refuses_the_fake_provider_and_a_switch_with_no_webhook_role():
    with pytest.raises(ConfigError, match="takes no payment"):
        ApiConfig.from_env({**PRODUCTION, "TJ_BILLING_PROVIDER": "fake"})
    with pytest.raises(ConfigError, match="TJ_WEBHOOK_DATABASE_URL"):
        ApiConfig.from_env({**PRODUCTION, "TJ_BILLING_ENABLED": "true", "TJ_BILLING_PROVIDER": "paddle"})


def test_the_webhook_role_url_is_never_shown():
    config = ApiConfig.from_env({"TJ_WEBHOOK_DATABASE_URL": "postgresql://app_webhook:pw-123@db/x"})
    assert "pw-123" not in repr(config) and "pw-123" not in repr(config.fake_billing_secret[:0])


def test_a_scheduled_job_cannot_use_the_fake_provider():
    assert provider_from_env({}) is None
    with pytest.raises(ValueError, match="scheduled job"):
        provider_from_env({"TJ_BILLING_PROVIDER": "fake"})


# ---- the signature -------------------------------------------------------------------------------------------------------


def test_a_signature_over_the_raw_body_is_accepted_within_the_tolerance():
    body = b'{"event_id":"evt_1"}'
    header = signature.sign(HOOK_SECRET, body, 1_000)
    assert signature.verify(header, body, HOOK_SECRET, now=1_000)
    assert signature.verify(header, body, HOOK_SECRET, now=1_005)
    assert not signature.verify(header, body, HOOK_SECRET, now=1_006)  # too old: a replay
    assert not signature.verify(header, body, HOOK_SECRET, now=994)  # from the future


@pytest.mark.parametrize(
    "case",
    ["wrong secret", "altered body", "no header", "empty", "garbage", "no h1", "ts not a number", "huge header"],
)
def test_every_other_signature_is_refused(case):
    body = b'{"event_id":"evt_1"}'
    good = signature.sign(HOOK_SECRET, body, 1_000)
    header, raw, secret = {
        "wrong secret": (signature.sign("other", body, 1_000), body, HOOK_SECRET),
        "altered body": (good, body + b" ", HOOK_SECRET),
        "no header": (None, body, HOOK_SECRET),
        "empty": ("", body, HOOK_SECRET),
        "garbage": ("hello", body, HOOK_SECRET),
        "no h1": ("ts=1000", body, HOOK_SECRET),
        "ts not a number": (good.replace("ts=1000", "ts=abc"), body, HOOK_SECRET),
        "huge header": (good + ";x=" + "y" * 2000, body, HOOK_SECRET),
    }[case]
    assert not signature.verify(header, raw, secret, now=1_000)


def test_during_a_secret_rotation_either_signature_is_enough():
    body = b"{}"
    old = signature.sign("old-secret", body, 1_000).split(";")[1]
    new = signature.sign(HOOK_SECRET, body, 1_000)
    assert signature.verify(f"{new};{old}", body, HOOK_SECRET, now=1_000)
    assert signature.verify(f"ts=1000;{old};{new.split(';')[1]}", body, HOOK_SECRET, now=1_000)


# ---- the fake provider keeps the connector's contract ---------------------------------------------------------------------


@pytest.fixture
def fake():
    clock = {"now": NOW}
    provider = FakeBilling(secret=HOOK_SECRET, clock=lambda: clock["now"], wall=lambda: NOW.timestamp())
    provider.tick = lambda **kw: clock.__setitem__("now", clock["now"] + timedelta(**kw))  # type: ignore[attr-defined]
    return provider


def test_one_customer_per_address_and_a_checkout_that_waits_for_free_days(fake):
    user = uuid4()
    customer = fake.find_or_create_customer(email="Sam@Example.test", user_id=user)
    assert fake.find_or_create_customer(email="sam@example.test", user_id=user) == customer
    checkout = fake.create_checkout(customer_id=customer, user_id=user, free_days=5)
    assert checkout.pay_url == f"/billing/pay?_ptxn={checkout.checkout_id}" == fake.pay_url(checkout.checkout_id)
    delivery = fake.complete_checkout(checkout.checkout_id)
    event = fake.verify_webhook(delivery.headers, delivery.body)
    assert event.kind == "subscription" and event.type == "subscription.created"
    sub = event.subscription
    assert (sub.status, sub.current_period_end, sub.user_ref, sub.checkout_id) == (
        "trialing",
        NOW + timedelta(days=5),
        str(user),
        checkout.checkout_id,
    )


def test_cancel_resume_cancel_now_and_fetch(fake):
    user = uuid4()
    checkout = fake.create_checkout(
        customer_id=fake.find_or_create_customer(email="a@x.test", user_id=user), user_id=user, free_days=0
    )
    fake.complete_checkout(checkout.checkout_id)
    sub_id = next(iter(fake.subscriptions))
    assert fake.cancel(sub_id).cancel_at_period_end is True
    assert fake.resume(sub_id).cancel_at_period_end is False
    ended = fake.cancel_now(sub_id)
    assert (ended.status, ended.ended_at) == ("canceled", NOW)
    assert fake.cancel_now(sub_id).status == "canceled"  # ending one already ended is not an error
    with pytest.raises(ProviderRefused):
        fake.resume(sub_id)  # a canceled subscription cannot be brought back
    assert fake.fetch_subscription(sub_id).current_period_end == NOW + MONTH
    with pytest.raises(ProviderRefused):
        fake.fetch_subscription("sub_nope")


@pytest.mark.parametrize(("how", "error"), [("down", ProviderUnavailable), ("refuse", ProviderRefused)])
def test_it_fails_on_request_before_acting(fake, how, error):
    fake.fail_next(how)
    with pytest.raises(error):
        fake.find_or_create_customer(email="a@x.test", user_id=uuid4())
    assert fake.customers == {}


def test_acting_then_timing_out_leaves_the_change_made_and_unheard(fake):
    customer = fake.find_or_create_customer(email="a@x.test", user_id=uuid4())
    fake.fail_next("act_then_timeout")
    with pytest.raises(ProviderUnavailable):
        fake.create_checkout(customer_id=customer, user_id=uuid4(), free_days=0)
    assert len(fake.checkouts) == 1


def test_a_webhook_with_a_bad_signature_or_body_is_refused_and_an_unknown_one_is_read_as_unknown(fake):
    delivery = fake.refund("sub_x")
    with pytest.raises(BadSignature):
        fake.verify_webhook({}, delivery.body)
    with pytest.raises(BadSignature):
        fake.verify_webhook(delivery.headers, delivery.body + b" ")
    junk = b"not json"
    with pytest.raises(BadSignature):
        fake.verify_webhook(fake.sign(junk), junk)
    odd = json.dumps(
        {
            "event_id": "e",
            "event_type": "subscription.updated",
            "occurred_at": NOW.isoformat(),
            "data": {"id": "s", "status": "weird"},
        }
    ).encode()
    assert fake.verify_webhook(fake.sign(odd), odd).kind == "unknown"


def test_what_the_fake_says_about_a_refund_and_what_it_keeps(fake):
    delivery = fake.refund("sub_x", amount="700")
    event = fake.verify_webhook(delivery.headers, delivery.body)
    assert (event.kind, event.refund.amount, event.refund.subscription_id) == ("refund", "700", "sub_x")
    assert event.payload["event_type"] == "refund.created"


# ---- the price -----------------------------------------------------------------------------------------------------------


def test_the_price_is_the_one_published_on_roundturn_app():
    assert (price.PRICE_CENTS, price.CURRENCY, price.amount()) == (1500, "USD", "15.00")
    terms = (REPO / "site" / "public" / "terms" / "index.html").read_text(encoding="utf-8")
    assert re.search(r"USD 15 per month", terms), "the Terms state the price; change both together"
    assert "USD 15" in price.PRICE_TEXT
