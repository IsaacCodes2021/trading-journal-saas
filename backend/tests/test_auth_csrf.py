import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tradejournal.auth.csrf import check_csrf, csrf_token

ORIGIN = "https://app.example.com"
TOKEN = csrf_token(b"s" * 32, "session-1")


@pytest.fixture
def client():
    app = FastAPI()

    @app.post("/write")
    def write(request: Request) -> dict[str, bool]:
        check_csrf(request, TOKEN, ORIGIN)
        return {"ok": True}

    return TestClient(app)


GOOD = {"sec-fetch-site": "same-origin", "x-csrf-token": TOKEN, "content-type": "application/json"}


def post(client, **overrides):
    headers = {**GOOD, **overrides}
    headers = {key: value for key, value in headers.items() if value is not None}
    return client.post("/write", content=b"{}", headers=headers)


def test_a_same_origin_json_write_with_the_token_passes(client):
    assert post(client).status_code == 200


def test_origin_alone_is_enough_when_the_browser_sends_no_fetch_metadata(client):
    assert post(client, **{"sec-fetch-site": None, "origin": ORIGIN}).status_code == 200


@pytest.mark.parametrize(
    "overrides",
    [
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},  # a sibling subdomain is still not us
        {"sec-fetch-site": "none"},
        {"sec-fetch-site": None, "origin": "https://evil.example"},
        {"sec-fetch-site": None, "origin": "null"},  # sandboxed pages and some redirects
        {"sec-fetch-site": "same-origin", "origin": "https://evil.example"},  # contradictory: trust neither
        {"sec-fetch-site": "cross-site", "origin": ORIGIN},
        {"sec-fetch-site": None},  # neither header: a browser always sends one of them, so this is not a browser
    ],
)
def test_requests_that_are_not_clearly_same_origin_are_refused(client, overrides):
    assert post(client, **overrides).status_code == 403


@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN[:-1], TOKEN + "0", TOKEN.upper()])
def test_the_csrf_token_must_match_exactly(client, token):
    assert post(client, **{"x-csrf-token": token}).status_code == 403


def test_a_non_ascii_token_is_a_403_not_a_server_error(client):
    # hmac.compare_digest raises TypeError on non-ASCII str, and the header value is attacker-controlled.
    assert post(client, **{"x-csrf-token": b"\xe9\xff"}).status_code == 403


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "application/xml"])
def test_only_json_and_multipart_bodies_are_accepted(client, content_type):
    # A plain HTML form can send exactly these three types cross-site without a preflight.
    assert post(client, **{"content-type": content_type}).status_code == 415


def test_multipart_uploads_pass(client):
    assert post(client, **{"content-type": "multipart/form-data; boundary=xyz"}).status_code == 200


def test_a_write_with_no_body_passes(client):
    response = client.post("/write", headers={"sec-fetch-site": "same-origin", "x-csrf-token": TOKEN})
    assert response.status_code == 200


def test_tokens_are_per_session_and_per_secret_and_stable():
    secret = b"k" * 32
    assert csrf_token(secret, "a") == csrf_token(secret, "a")
    assert csrf_token(secret, "a") != csrf_token(secret, "b")
    assert csrf_token(secret, "a") != csrf_token(b"x" * 32, "a")
    assert len(csrf_token(secret, "a")) == 64
