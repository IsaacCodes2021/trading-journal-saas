"""`GET /billing/pay`, the payment page: served only with billing on and the Paddle connector, filled with the public client
token and the environment, under a Content-Security-Policy of its own that no other page gets (routers/pay.py, security.py)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient
from paddle_world import CLIENT_TOKEN, FakePaddleApi, settings

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.security import CSP, PAY_CSP
from tradejournal.billing.fake import FakeBilling

ORIGIN = "https://app.example.com"
PAGE = """<!doctype html><html><head>
<meta name="tj-paddle-token" content="__TJ_PADDLE_TOKEN__" />
<meta name="tj-paddle-env" content="__TJ_PADDLE_ENV__" />
<script type="module" src="/assets/pay-x.js"></script></head><body><main>Roundturn Pro</main></body></html>"""


def client(tmp_path, *, enabled=True, provider="paddle", page=PAGE):
    dist = tmp_path / "dist"
    dist.mkdir()
    if page is not None:
        (dist / "pay.html").write_text(page, encoding="utf-8")
        (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    paddle = settings()
    config = ApiConfig(
        app_origin=ORIGIN,
        web_dist=dist,
        billing_enabled=enabled,
        billing_provider=provider,
        paddle=paddle if provider == "paddle" else None,
    )
    connector = FakePaddleApi().provider() if provider == "paddle" else FakeBilling(secret="s" * 8)
    services, _, _ = make_services()
    services = replace(services, billing=SimpleNamespace(provider=connector))
    return TestClient(create_app(config, services, billing_provider=connector), base_url=ORIGIN)


def test_the_page_is_filled_with_the_public_token_and_the_environment_and_nothing_else(tmp_path):
    response = client(tmp_path).get("/billing/pay?_ptxn=txn_" + "0" * 26)
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert f'content="{CLIENT_TOKEN}"' in response.text and 'content="sandbox"' in response.text
    assert "__TJ_PADDLE" not in response.text
    assert settings().api_key not in response.text and settings().webhook_secret not in response.text


def test_only_this_page_may_load_paddles_script_and_frame_its_checkout(tmp_path):
    app = client(tmp_path)
    page = app.get("/billing/pay")
    assert page.headers["content-security-policy"] == PAY_CSP
    directives = dict(part.split(" ", 1) for part in PAY_CSP.split("; "))
    assert directives["script-src"] == "'self' https://cdn.paddle.com"  # no inline code, no other site's script
    # Paddle.js styles the checkout it opens: its stylesheet (live and sandbox) and inline styles, on this page only.
    assert directives["style-src"] == "'self' 'unsafe-inline' https://cdn.paddle.com https://sandbox-cdn.paddle.com"
    assert directives["frame-ancestors"] == "'none'" and "'unsafe-eval'" not in PAY_CSP
    assert page.headers["cross-origin-opener-policy"] == "same-origin-allow-popups"
    assert page.headers["cache-control"] == "no-store"
    for elsewhere in ("/", "/settings/plan", "/billing/payx", "/healthz"):
        other = app.get(elsewhere, headers={"accept": "text/html"})
        assert other.headers["content-security-policy"] == CSP
        assert other.headers["cross-origin-opener-policy"] == "same-origin"
    assert "paddle" not in CSP


def test_a_head_request_answers_like_a_get(tmp_path):
    assert client(tmp_path).head("/billing/pay").status_code == 200


@pytest.mark.parametrize(("enabled", "provider"), [(False, "paddle"), (True, "fake"), (False, None)])
def test_there_is_no_page_with_billing_off_or_without_paddle(tmp_path, enabled, provider):
    assert client(tmp_path, enabled=enabled, provider=provider).get("/billing/pay").status_code == 404


@pytest.mark.parametrize("page", [None, "<html>an old build with no slots</html>"])
def test_a_server_without_the_built_page_says_so_rather_than_serving_a_broken_one(tmp_path, page):
    response = client(tmp_path, page=page).get("/billing/pay")
    assert response.status_code == 503 and "not available" in response.text
