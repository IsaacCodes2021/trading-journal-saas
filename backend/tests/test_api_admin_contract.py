"""Contract checks over the whole operator API, read from the OpenAPI document, so a route added later cannot skip them
(doc 04 gates "admin routes require MFA within 15 minutes", "admin responses contain no trade, note or screenshot fields")."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import ADMIN_MFA_FOR, SESSION_COOKIE
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
PREFIX = "/api/admin/v1"
SOMEONE = UUID("11111111-2222-3333-4444-555555555555")
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

# What an operator may send: the words and numbers that describe a decision or a code, never free-form data.
ALLOWED_INPUT = {
    "reason", "plan", "expires_at", "label", "email", "max_uses", "days", "grant_code_id", "max_redemptions", "expires_in_days",
}  # fmt: skip
# The firms' rule templates are the one thing an operator edits in full: a rule set's values and flags, and the research
# CSV as a file. They hold the firms' published rules, which every person reads to pick their account's rules, and never
# anything a person wrote in their journal; a person's verified rules are a copy the operator cannot reach.
TEMPLATE_ROUTES = f"{PREFIX}/rule-templates"
TEMPLATE_INPUT = {"values", "flags", "field", "reason", "file"}

# Words that mean something a person wrote or traded. A counter may be named after what it counts ("trades": 40), but
# no property of an operator response may be one of these, and nothing may carry a list or an object of them.
FORBIDDEN = {
    "notes", "note", "day_notes", "notes_html", "body", "content", "text",
    "fills", "fill_pairs", "pnl", "net_pnl", "gross_pnl", "entry_price", "exit_price", "symbol", "side", "quantity",
    "stored_name", "object_key", "thumb_key", "image", "images", "file", "files", "filename", "url", "src",
    "tags", "tag_groups", "account_name", "broker_account",
}  # fmt: skip


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path_factory.mktemp("web") / "none", session_secret=SECRET)
    app = create_app(config, services, clock=lambda: NOW)
    app.state.test = (sessions, accounts)
    return app


@pytest.fixture(scope="module")
def spec(app):
    return app.openapi()


def admin_operations(spec):
    return [
        (method.upper(), path, operation)
        for path, item in spec["paths"].items()
        if path.startswith(PREFIX)
        for method, operation in item.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def resolve(spec, schema, seen=None):
    """Every property name reachable from a schema, following references."""
    seen = seen if seen is not None else set()
    names: set[str] = set()
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return names
        seen.add(name)
        return resolve(spec, spec["components"]["schemas"][name], seen)
    for key in ("anyOf", "oneOf", "allOf"):
        for part in schema.get(key, []):
            names |= resolve(spec, part, seen)
    if "items" in schema:
        names |= resolve(spec, schema["items"], seen)
    if isinstance(schema.get("additionalProperties"), dict):
        names |= resolve(spec, schema["additionalProperties"], seen)
    for prop, sub in schema.get("properties", {}).items():
        names.add(prop)
        names |= resolve(spec, sub, seen)
    return names


def response_property_names(spec, operation):
    names: set[str] = set()
    for response in operation["responses"].values():
        for media in response.get("content", {}).values():
            names |= resolve(spec, media.get("schema", {}))
    return names


def test_there_are_operator_routes_to_check(spec):
    paths = {(m, p) for m, p, _ in admin_operations(spec)}
    assert ("GET", f"{PREFIX}/users") in paths and ("GET", f"{PREFIX}/users/{{user_id}}") in paths
    assert len(paths) >= 17, "the contract checks pass because they found almost nothing"


def test_no_operator_response_carries_anything_a_person_wrote_or_traded(spec):
    for method, path, operation in admin_operations(spec):
        found = response_property_names(spec, operation) & FORBIDDEN
        assert not found, f"{method} {path} responds with {sorted(found)}"


def test_the_scan_would_notice_a_forbidden_field(spec):
    """The scan is only worth anything if it can fail: plant a trade note in a copy of a response and see it caught."""
    planted = {
        "components": {
            "schemas": {**spec["components"]["schemas"], "Leak": {"properties": {"notes": {"type": "string"}}}}
        }
    }
    assert resolve(planted, {"$ref": "#/components/schemas/Leak"}) == {"notes"}
    nested = {
        "components": {
            "schemas": {
                "Outer": {"properties": {"rows": {"items": {"$ref": "#/components/schemas/Leak"}}}},
                "Leak": {"properties": {"symbol": {}}},
            }
        }
    }
    assert resolve(nested, {"$ref": "#/components/schemas/Outer"}) == {"rows", "symbol"}


def test_every_operator_request_body_is_small_and_has_no_free_form_object(spec):
    """An operator can send a reason, a plan, a date, a label and the numbers of a code: nothing that could carry a tenant's data back in."""
    for method, path, operation in admin_operations(spec):
        body = operation.get("requestBody")
        if body is None:
            continue
        [schema] = [media["schema"] for media in body["content"].values()]
        names = resolve(spec, schema)
        allowed = ALLOWED_INPUT | TEMPLATE_INPUT if path.startswith(TEMPLATE_ROUTES) else ALLOWED_INPUT
        assert names <= allowed, f"{method} {path} accepts {sorted(names - allowed)}"


def test_every_operator_response_says_it_can_be_refused_for_the_three_reasons(spec):
    for method, path, operation in admin_operations(spec):
        assert {"401", "403"} <= set(operation["responses"]), f"{method} {path} does not document 401 and 403"


# ---- the guard on every route, from the document ----------------------------------------------------------------------------


def person(app, *, admin, mfa_ago: timedelta | None):
    sessions, accounts = app.state.test
    account = accounts.add(f"p{len(accounts._accounts)}@example.test", is_admin=admin)
    mfa_at = None if mfa_ago is None else NOW - mfa_ago
    token, session = sessions.sign_in(account.user.id, mfa_at=mfa_at)
    return {
        "cookie": f"{SESSION_COOKIE}={token}",
        "sec-fetch-site": "same-origin",
        "x-csrf-token": csrf_token(SECRET, session.id),
    }


def request(app, method, path, headers, operation):
    client = TestClient(app, base_url=ORIGIN)
    filled = (
        path.replace("{user_id}", str(SOMEONE))
        .replace("{invite_id}", "1")
        .replace("{grant_id}", "1")
        .replace("{signup_id}", "1")
        .replace("{template_id}", "1")
    )
    body = None
    if "requestBody" in operation:
        body = {"reason": "x", "plan": "pro"}
    return client.request(method, filled, headers=headers, json=body)


def test_every_operator_route_refuses_a_stranger_an_ordinary_person_and_an_operator_with_no_fresh_verification(
    app, spec
):
    cases = {
        "stranger": ({"sec-fetch-site": "same-origin"}, 401),
        "ordinary person, even with two-step verification": (
            person(app, admin=False, mfa_ago=timedelta(seconds=1)),
            403,
        ),
        "operator, never verified": (person(app, admin=True, mfa_ago=None), 403),
        "operator, verified too long ago": (person(app, admin=True, mfa_ago=ADMIN_MFA_FOR + timedelta(seconds=1)), 403),
    }
    checked = 0
    for method, path, operation in admin_operations(spec):
        for who, (headers, expected) in cases.items():
            got = request(app, method, path, headers, operation).status_code
            assert got == expected, f"{method} {path} for a {who}: {got}, expected {expected}"
            checked += 1
    assert checked >= 17 * len(cases)


def test_every_operator_write_refuses_a_forged_request_from_a_real_operator(app, spec):
    operator = person(app, admin=True, mfa_ago=timedelta(seconds=1))
    for method, path, operation in admin_operations(spec):
        if method == "GET":
            continue
        for name, bad in (
            ("no token", {k: v for k, v in operator.items() if k != "x-csrf-token"}),
            ("wrong token", {**operator, "x-csrf-token": "forged"}),
            ("cross-site", {**operator, "sec-fetch-site": "cross-site"}),
        ):
            got = request(app, method, path, bad, operation).status_code
            assert got == 403, f"{method} {path} with {name}: {got}"


def test_an_operator_who_passes_every_check_reaches_the_service(app, spec):
    """The other half: the guard lets a real operator through (a 503 here means 'no operator tools configured', which is
    the next layer, so it proves the request got past all of the checks)."""
    operator = person(app, admin=True, mfa_ago=timedelta(minutes=1))
    for method, path, operation in admin_operations(spec):
        assert request(app, method, path, operator, operation).status_code == 503, f"{method} {path}"
