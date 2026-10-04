"""Launch-gate checks that are generated from the OpenAPI document, so a route added tomorrow is covered without anyone
remembering to write its test (doc 04 section 4: "CSRF enforced on every unsafe route", deny by default)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from api_fakes import make_services
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE, SessionDep
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
UNSAFE = {"post", "put", "patch", "delete"}
# (method, path) pairs that may be called without signing in. Empty on purpose: every route needs a session until
# someone adds one here deliberately, in a pull request that someone reads.
PUBLIC: set[tuple[str, str]] = set()


def build_app(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    # An operator who has just confirmed two-step verification, so the positive control below (a correct request must not
    # be refused) holds for the operator routes as well; the checks that matter for them (strangers, ordinary people, a
    # stale verification) are in test_api_admin_contract.py.
    operator = accounts.add("gate@example.test", is_admin=True)
    token, session = sessions.sign_in(operator.user.id, mfa_at=datetime.now(UTC))
    return create_app(config, services), token, session


def sample_url(path: str, operation: dict) -> str:
    """Fill `{id}`-style path parameters with a value of the right shape; the answer will be a 404 or 422, which is fine."""
    for parameter in operation.get("parameters", []):
        if parameter["in"] == "path":
            kind = parameter["schema"]
            value = str(uuid4()) if kind.get("format") == "uuid" else "1" if kind.get("type") == "integer" else "x"
            path = path.replace("{" + parameter["name"] + "}", value)
    return path


def violations(app: FastAPI, token: str, session) -> list[str]:
    client = TestClient(app, base_url=ORIGIN)
    cookie = {"cookie": f"{SESSION_COOKIE}={token}"}
    good = {**cookie, "sec-fetch-site": "same-origin", "x-csrf-token": csrf_token(SECRET, session.id)}
    found = []
    for path, item in app.openapi()["paths"].items():
        for method, operation in item.items():
            where = f"{method.upper()} {path}"
            url = sample_url(path, operation)

            def call(headers, method=method, url=url):
                return client.request(method, url, headers=headers, json={})

            if (method, path) not in PUBLIC and (status := call({}).status_code) != 401:
                found.append(f"{where}: an anonymous request got {status}, not 401")
            if method not in UNSAFE:
                continue
            for label, headers in {
                "no CSRF token or origin": cookie,
                "a wrong CSRF token": {**good, "x-csrf-token": "wrong"},
                "a cross-site request": {**good, "sec-fetch-site": "cross-site"},
                "a foreign origin": {**cookie, "origin": "https://evil.example", "x-csrf-token": good["x-csrf-token"]},
            }.items():
                if (status := call(headers).status_code) != 403:
                    found.append(f"{where}: {label} got {status}, not 403")
            # Positive control: the checks above would also "pass" if the route simply always answered 403.
            if (status := call(good).status_code) in (401, 403, 415):
                found.append(f"{where}: a correct same-origin request with the token was refused ({status})")
    return found


def test_every_route_in_the_api_denies_by_default_and_checks_csrf_on_writes(tmp_path):
    app, token, session = build_app(tmp_path)
    paths = app.openapi()["paths"]
    assert paths, "the OpenAPI document has no routes; this test would pass for the wrong reason"
    assert violations(app, token, session) == []


def test_the_check_really_catches_a_route_that_forgets_its_guards(tmp_path):
    app, token, session = build_app(tmp_path)

    @app.post("/api/v1/rogue-write")  # signs in, but never checks CSRF
    def rogue_write(_: SessionDep) -> dict[str, bool]:
        return {"ok": True}

    def rogue_read() -> dict[str, bool]:  # not even signed in
        return {"ok": True}

    # GET routes added after the app is built sit behind the SPA's catch-all, so put this one in front.
    app.router.routes.insert(0, APIRoute("/api/v1/rogue-read", rogue_read, methods=["GET"]))
    app.openapi_schema = None  # the schema is cached; routes were added since
    found = violations(app, token, session)
    assert any("POST /api/v1/rogue-write: no CSRF token or origin got 200" in line for line in found), found
    assert any("GET /api/v1/rogue-read: an anonymous request got 200" in line for line in found), found


def test_no_cors_middleware_and_preflights_get_nothing(tmp_path):
    app, token, _ = build_app(tmp_path)
    assert not any(middleware.cls is CORSMiddleware for middleware in app.user_middleware)
    preflight = TestClient(app, base_url=ORIGIN).options(
        "/api/v1/settings",
        headers={"origin": "https://evil.example", "access-control-request-method": "PATCH"},
    )
    assert not [name for name in preflight.headers if name.startswith("access-control-")]


@pytest.mark.parametrize("path", ["/api/v1/session", "/api/v1/settings"])
def test_api_responses_are_never_cacheable_even_for_errors(tmp_path, path):
    app, _, _ = build_app(tmp_path)
    response = TestClient(app, base_url=ORIGIN).get(path)
    assert response.headers["cache-control"] == "no-store"
