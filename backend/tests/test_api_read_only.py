"""Read-only after the beta: every route that creates, changes or deletes the person's data refuses an account whose
time-limited access ended, and the few routes a person needs to leave or come back do not. Generated from the API description,
so a route added tomorrow is covered without anyone remembering to write its test."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
WRITES = {"post", "put", "patch", "delete"}
READ_ONLY = "read-only"  # what the refusal says

# The writes an ended account may still make, each for a reason: leaving (delete the account, sign out, end other sessions)
# and coming back (redeem a code, subscribe, and manage a subscription: the billing design). Operator routes are the
# operator's own, not the person's journal. Everything else is refused.
EXEMPT = {
    ("post", "/api/v1/account/deletion"),
    ("post", "/api/v1/grants/redeem"),
    ("post", "/api/v1/billing/checkout"),
    ("post", "/api/v1/billing/cancel"),
    ("post", "/api/v1/billing/resume"),
    ("post", "/api/v1/billing/portal"),
    ("delete", "/api/v1/sessions"),
    ("delete", "/api/v1/sessions/{session_id}"),
    ("post", "/auth/logout"),
}


def build(tmp_path, *, ended: bool):
    services, sessions, accounts = make_services()
    person = accounts.add("ended@example.test")
    if ended:
        accounts.end_access(person.user.id)
    token, session = sessions.sign_in(person.user.id, authenticated_at=datetime.now(UTC))
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    app = create_app(config, services)
    client = TestClient(app, base_url=ORIGIN)
    headers = {
        "cookie": f"{SESSION_COOKIE}={token}",
        "sec-fetch-site": "same-origin",
        "x-csrf-token": csrf_token(SECRET, session.id),
    }
    return app, client, headers


def url_for(path: str, operation: dict) -> str:
    for parameter in operation.get("parameters", []):
        if parameter["in"] == "path":
            kind = parameter["schema"]
            value = (
                str(uuid4()) if kind.get("format") == "uuid" else "1" if kind.get("type") == "integer" else "2026-10-09"
            )
            path = path.replace("{" + parameter["name"] + "}", value)
    return path


def person_writes(app):
    for path, item in app.openapi()["paths"].items():
        if path.startswith("/api/admin/"):
            continue
        for method, operation in item.items():
            if method in WRITES:
                yield method, path, operation


def test_every_write_to_the_journal_refuses_an_account_whose_access_ended(tmp_path):
    app, client, headers = build(tmp_path, ended=True)
    seen, failures = 0, []
    for method, path, operation in person_writes(app):
        if (method, path) in EXEMPT:
            continue
        seen += 1
        response = client.request(method, url_for(path, operation), headers=headers, json={})
        if response.status_code != 403 or READ_ONLY not in response.text:
            failures.append(f"{method.upper()} {path}: {response.status_code} {response.text[:80]}")
    assert seen >= 25, "the API description has too few writes; this test would pass for the wrong reason"
    assert failures == []


def test_the_same_writes_are_not_refused_for_that_reason_when_access_has_not_ended(tmp_path):
    app, client, headers = build(tmp_path, ended=False)
    failures = []
    for method, path, operation in person_writes(app):
        response = client.request(method, url_for(path, operation), headers=headers, json={})
        if READ_ONLY in response.text:
            failures.append(f"{method.upper()} {path}")
    assert failures == []


def test_the_routes_a_person_needs_to_leave_or_come_back_are_not_refused_for_it(tmp_path):
    app, client, headers = build(tmp_path, ended=True)
    paths = app.openapi()["paths"]
    for method, path in sorted(EXEMPT):
        assert path in paths and method in paths[path], f"{method} {path} is not in the API description any more"
        response = client.request(method, url_for(path, paths[path][method]), headers=headers, json={})
        assert READ_ONLY not in response.text, (method, path, response.status_code)


def test_reading_everything_still_works_and_the_session_says_why_it_is_read_only(tmp_path):
    app, client, headers = build(tmp_path, ended=True)
    body = client.get("/api/v1/session", headers=headers).json()
    assert body["access"] == {"state": "ended", "ends_at": "2026-11-08T00:00:00Z", "reason": "expired"}
    for path, item in app.openapi()["paths"].items():
        if "get" in item and "{" not in path and not path.startswith("/api/admin/") and path != "/api/v1/session":
            response = client.get(path, headers=headers)
            assert READ_ONLY not in response.text, path  # never refused for this reason


def test_an_account_with_no_end_date_reports_active_access(tmp_path):
    _, client, headers = build(tmp_path, ended=False)
    assert client.get("/api/v1/session", headers=headers).json()["access"] == {
        "state": "active",
        "ends_at": None,
        "reason": None,
    }


@pytest.mark.parametrize("reason", ["expired", "revoked"])
def test_the_reason_is_passed_on(tmp_path, reason):
    services, sessions, accounts = make_services()
    person = accounts.add("r@example.test")
    accounts.end_access(person.user.id, reason=reason)
    token, session = sessions.sign_in(person.user.id)
    app = create_app(ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET), services)
    body = (
        TestClient(app, base_url=ORIGIN).get("/api/v1/session", headers={"cookie": f"{SESSION_COOKIE}={token}"}).json()
    )
    assert body["access"]["reason"] == reason
