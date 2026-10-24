"""`GET /billing/stripe/pay`, the Stripe payment link (routers/stripe_pay.py): with billing on and the Stripe connector it sends
the person signed in to Stripe's page for their own open checkout, and anyone else back to Plan & billing. Beside Paddle's
`/billing/pay`, which answers 404 while Stripe is the connector, and the reverse."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient
from paddle_world import FakePaddleApi
from paddle_world import settings as paddle_settings
from stripe_world import ORIGIN, FakeStripeApi, settings

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.security import CSP


def app(tmp_path, *, enabled=True, provider="stripe", api=None):
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=dist,
        billing_enabled=enabled,
        billing_provider=provider,
        stripe=settings() if provider == "stripe" else None,
        paddle=paddle_settings() if provider == "paddle" else None,
    )
    connector = (api or FakeStripeApi()).provider() if provider == "stripe" else FakePaddleApi().provider()
    services, sessions, _ = make_services()
    services = replace(services, billing=SimpleNamespace(provider=connector))
    client = TestClient(create_app(config, services, billing_provider=connector), base_url=ORIGIN)
    return client, sessions, connector


def signed_in(sessions, user):
    token, _ = sessions.sign_in(user)
    return {"cookie": f"{SESSION_COOKIE}={token}"}


@pytest.fixture
def api():
    return FakeStripeApi()


def opened(connector, user):
    customer = connector.find_or_create_customer(email="alice@example.com", user_id=user)
    return connector.create_checkout(customer_id=customer, user_id=user, free_days=0)


def test_the_person_goes_to_stripes_page_for_their_own_open_checkout(tmp_path, api):
    client, sessions, connector = app(tmp_path, api=api)
    alice = uuid4()
    checkout = opened(connector, alice)
    response = client.get(checkout.pay_url, headers=signed_in(sessions, alice), follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("https://checkout.stripe.com/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == CSP  # the app's own strict policy: no Stripe script here


def test_someone_elses_checkout_or_no_sign_in_goes_back_to_the_plan_page(tmp_path, api):
    client, sessions, connector = app(tmp_path, api=api)
    checkout = opened(connector, uuid4())
    for headers in (signed_in(sessions, uuid4()), {}):
        response = client.get(checkout.pay_url, headers=headers, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/settings/plan")


def test_a_paid_checkout_comes_back_to_the_plan_page_that_waits_for_stripe(tmp_path, api):
    client, sessions, connector = app(tmp_path, api=api)
    alice = uuid4()
    checkout = opened(connector, alice)
    api.complete(checkout.checkout_id)
    response = client.get(checkout.pay_url, headers=signed_in(sessions, alice), follow_redirects=False)
    assert response.headers["location"] == "/settings/plan?checkout=done"


@pytest.mark.parametrize("cs", ["", "nonsense", "cs_test_../../v1/customers"])
def test_a_missing_or_malformed_checkout_goes_back_to_the_plan_page_without_asking_stripe(tmp_path, api, cs):
    client, sessions, _ = app(tmp_path, api=api)
    response = client.get(f"/billing/stripe/pay?cs={cs}", headers=signed_in(sessions, uuid4()), follow_redirects=False)
    assert (response.status_code, response.headers["location"]) == (303, "/settings/plan")
    assert api.requests == []


def test_stripe_away_goes_back_to_the_plan_page(tmp_path, api):
    import httpx

    client, sessions, connector = app(tmp_path, api=api)
    alice = uuid4()
    checkout = opened(connector, alice)
    api.answers += [httpx.Response(503)] * 3
    response = client.get(checkout.pay_url, headers=signed_in(sessions, alice), follow_redirects=False)
    assert response.headers["location"] == "/settings/plan"


def test_the_link_is_404_with_billing_off_or_with_paddle_and_paddles_page_is_404_with_stripe(tmp_path):
    off, _, _ = app(tmp_path, enabled=False)
    assert off.get("/billing/stripe/pay?cs=x", follow_redirects=False).status_code == 404
    paddle, _, _ = app(tmp_path, provider="paddle")
    assert paddle.get("/billing/stripe/pay?cs=x", follow_redirects=False).status_code == 404
    stripe, _, _ = app(tmp_path)
    assert stripe.get("/billing/pay?_ptxn=txn_" + "0" * 26).status_code == 404


def test_the_link_is_not_in_the_api_document(tmp_path):
    client, _, _ = app(tmp_path)
    paths = client.app.openapi()["paths"]
    assert "/api/v1/billing/checkout" in paths and "/billing/stripe/pay" not in paths
