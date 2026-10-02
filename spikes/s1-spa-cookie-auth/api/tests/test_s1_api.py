"""S1 spike: the API-side questions, answered by tests."""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from conftest import ORIGIN, csrf_headers, sign_in
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

# ---- sign-in flow and cookie ------------------------------------------------------------------------------------


def test_unauthenticated_session_is_a_401_problem_document(client):
    r = client.get("/api/v1/session")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 401


def test_login_sets_a_host_prefixed_session_cookie_and_redirects_to_next(client):
    r = sign_in(client, "alice", next_="/trades?preset=7d")
    assert r.status_code == 302 and r.headers["location"] == "/trades?preset=7d"
    cookies = [c for c in r.headers.get_list("set-cookie") if c.startswith("__Host-tj_session=")]
    assert len(cookies) == 1
    attrs = cookies[0].lower()
    assert "secure" in attrs and "httponly" in attrs and "samesite=lax" in attrs and "path=/" in attrs
    assert "domain=" not in attrs  # the __Host- prefix forbids it
    me = client.get("/api/v1/session")
    assert me.status_code == 200 and me.json()["user"]["email"] == "alice@example.test"
    assert me.json()["flags"]["mfa_fresh"] is False


def test_mfa_claim_is_recorded_for_users_who_used_it(client):
    sign_in(client, "bob")
    assert client.get("/api/v1/session").json()["flags"]["mfa_fresh"] is True


@pytest.mark.parametrize("hostile", ["//evil.test/x", "https://evil.test", "/\\evil.test", "javascript:alert(1)", ""])
def test_open_redirect_in_next_is_neutralized(client, hostile):
    assert sign_in(client, "alice", next_=hostile).headers["location"] == "/"


def test_unverified_email_cannot_sign_in(client):
    r = sign_in(client, "carol")
    assert r.status_code == 403
    assert client.get("/api/v1/session").status_code == 401


def test_callback_rejects_wrong_state_and_replayed_codes(client):
    r = client.get("/auth/login", follow_redirects=False)
    authorize = client.get(r.headers["location"], follow_redirects=False)
    assert authorize.status_code == 200
    bad = client.get("/auth/callback", params={"code": "x", "state": "wrong"}, follow_redirects=False)
    assert bad.status_code == 400
    # a code works once
    first = sign_in(client, "alice")
    assert first.status_code == 302
    replay_target = first.request.url.path + "?" + first.request.url.query.decode()
    assert client.get(replay_target, follow_redirects=False).status_code == 400


def test_each_login_rotates_the_token_and_logout_revokes_it(client):
    sign_in(client, "alice")
    old = client.cookies.get("__Host-tj_session")
    sign_in(client, "alice")
    new = client.cookies.get("__Host-tj_session")
    assert old != new
    r = client.post("/auth/logout", headers=csrf_headers(client))
    assert r.status_code == 204
    client.cookies.set("__Host-tj_session", new, domain="testserver.local", path="/")
    assert client.get("/api/v1/session").status_code == 401


# ---- tenant isolation -------------------------------------------------------------------------------------------


def test_each_user_only_sees_their_own_trades_and_foreign_ids_are_404(app):
    alice, bob = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)
    sign_in(alice, "alice")
    sign_in(bob, "bob")
    a, b = alice.get("/api/v1/trades").json(), bob.get("/api/v1/trades").json()
    assert a["total"] == b["total"] == 7
    a_ids = {t["id"] for t in a["items"]}
    b_ids = {t["id"] for t in b["items"]}
    assert a_ids.isdisjoint(b_ids)
    foreign = next(iter(b_ids))
    assert alice.get(f"/api/v1/trades/{foreign}").status_code == 404
    assert alice.put(f"/api/v1/trades/{foreign}/notes", json={"notes": "x"}, headers=csrf_headers(alice)).status_code == 404
    assert bob.get(f"/api/v1/trades/{foreign}").json()["notes"] == ""  # untouched


# ---- CSRF -------------------------------------------------------------------------------------------------------


def test_csrf_matrix_for_a_json_mutation(client):
    sign_in(client, "alice")
    tid = client.get("/api/v1/trades").json()["items"][0]["id"]
    url, body = f"/api/v1/trades/{tid}/notes", {"notes": "<p>hi</p>"}
    good = csrf_headers(client)
    assert client.put(url, json=body, headers={"X-CSRF-Token": good["X-CSRF-Token"]}).status_code == 403  # no origin
    assert client.put(url, json=body, headers={**good, "Origin": "https://evil.test"}).status_code == 403
    assert client.put(url, json=body, headers={**good, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.put(url, json=body, headers={"Origin": ORIGIN}).status_code == 403  # no token
    assert client.put(url, json=body, headers={**good, "X-CSRF-Token": "0" * 64}).status_code == 403
    assert client.put(url, json=body, headers=good).status_code == 200
    assert client.put(url, json=body, headers={**good, "Origin": "", "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_token_from_another_session_does_not_work(app):
    alice, bob = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)
    sign_in(alice, "alice")
    sign_in(bob, "bob")
    tid = alice.get("/api/v1/trades").json()["items"][0]["id"]
    stolen = bob.get("/api/v1/session").json()["csrf_token"]
    r = alice.put(f"/api/v1/trades/{tid}/notes", json={"notes": "x"}, headers={"Origin": ORIGIN, "X-CSRF-Token": stolen})
    assert r.status_code == 403


def test_csrf_applies_to_multipart_uploads_and_oversize_files_are_cut_off(client):
    sign_in(client, "alice")
    tid = client.get("/api/v1/trades").json()["items"][0]["id"]
    url = f"/api/v1/trades/{tid}/screenshots"
    files = {"file": ("shot.png", b"\x89PNG" + b"0" * 100, "image/png")}
    assert client.post(url, files=files, headers={"Origin": ORIGIN}).status_code == 403  # no token
    ok = client.post(url, files=files, headers=csrf_headers(client))
    assert ok.status_code == 200 and ok.json()["size"] == 104
    big = {"file": ("big.png", b"0" * (2 * 1024 * 1024 + 1), "image/png")}
    assert client.post(url, files=big, headers=csrf_headers(client)).status_code == 413


def test_form_encoded_bodies_are_refused(client):
    sign_in(client, "alice")
    tid = client.get("/api/v1/trades").json()["items"][0]["id"]
    r = client.put(f"/api/v1/trades/{tid}/notes", content="notes=x", headers={**csrf_headers(client), "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415


def test_no_cors_headers_are_ever_sent(client):
    sign_in(client, "alice")
    r = client.options("/api/v1/trades", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "GET"})
    assert not any(h.lower().startswith("access-control-allow") for h in r.headers)
    r = client.get("/api/v1/trades", headers={"Origin": "https://evil.test"})
    assert "access-control-allow-origin" not in {h.lower() for h in r.headers}


# ---- money ------------------------------------------------------------------------------------------------------


def test_money_is_a_string_on_the_wire_and_json_numbers_are_rejected(client):
    sign_in(client, "alice")
    h = csrf_headers(client)
    assert client.post("/api/v1/ledger", json={"amount": 12.5, "kind": "cost"}, headers=h).status_code == 422
    assert client.post("/api/v1/ledger", json={"amount": True, "kind": "cost"}, headers=h).status_code == 422
    assert client.post("/api/v1/ledger", json={"amount": "12,5", "kind": "cost"}, headers=h).status_code == 422
    r = client.post("/api/v1/ledger", json={"amount": "123456789012345678.123456789", "kind": "payout"}, headers=h)
    assert r.status_code == 201
    assert '"amount":"123456789012345678.123456789"' in r.text.replace(" ", "")  # exact, and a string
    trades = client.get("/api/v1/trades").json()["items"]
    assert all(isinstance(t["net_pnl"], str) for t in trades)


def test_summary_sums_exactly(client):
    sign_in(client, "alice")
    s = client.get("/api/v1/stats/summary").json()
    assert Decimal(s["net_pnl"]) == Decimal("125.50") - Decimal("80.00") + Decimal("310.25") - Decimal("45.75") + Decimal(
        "200.00"
    ) - Decimal("120.00") + Decimal("95.00")
    assert s["fees"] == "28.84"


def test_a_plain_decimal_leaks_number_into_the_input_schema():
    """The reason `Money` exists: demonstrate the default behavior this spike set out to avoid."""

    class Naive(BaseModel):
        amount: Decimal

    naive = FastAPI()

    @naive.post("/x")
    def post_x(body: Naive) -> Naive:
        return body

    schemas = naive.openapi()["components"]["schemas"]
    # FastAPI splits input and output schemas when they differ, and the input side admits a JSON number.
    request_side = schemas["Naive-Input"]["properties"]["amount"]
    assert {p.get("type") for p in request_side.get("anyOf", [])} == {"number", "string"}
    assert schemas["Naive-Output"]["properties"]["amount"]["type"] == "string"


# ---- dates ------------------------------------------------------------------------------------------------------


def test_date_presets_are_resolved_in_each_users_own_timezone(app):
    alice, bob = TestClient(app, base_url=ORIGIN), TestClient(app, base_url=ORIGIN)
    sign_in(alice, "alice")
    sign_in(bob, "bob")
    now = datetime.now(UTC)
    for client, tz in ((alice, "America/Los_Angeles"), (bob, "Australia/Sydney")):
        latest = max(t["trade_date"] for t in client.get("/api/v1/trades").json()["items"])
        assert latest == now.astimezone(ZoneInfo(tz)).date().isoformat()
        counts = {p: client.get("/api/v1/stats/summary", params={"preset": p}).json()["trades"] for p in ("7d", "30d", "all")}
        assert counts == {"7d": 4, "30d": 6, "all": 7}


# ---- headers, SPA serving ---------------------------------------------------------------------------------------


def test_strict_csp_and_friends_are_on_every_kind_of_response(client):
    for path in ("/healthz", "/api/v1/session", "/assets/app.abc123.js"):
        h = client.get(path).headers
        csp = h["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "strict-origin-when-cross-origin"


def test_api_responses_are_never_cached(client):
    sign_in(client, "alice")
    assert client.get("/api/v1/session").headers["cache-control"] == "no-store"


def test_spa_history_fallback_and_cache_headers(client):
    page = client.get("/trades/42", headers={"Accept": "text/html"})
    assert page.status_code == 200 and "<div id=root>" in page.text
    assert page.headers["cache-control"] == "no-cache"
    asset = client.get("/assets/app.abc123.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_the_fallback_never_swallows_api_or_non_html_requests(client):
    api_miss = client.get("/api/v1/nope", headers={"Accept": "text/html"})
    assert api_miss.status_code == 404 and api_miss.headers["content-type"].startswith("application/problem+json")
    assert client.get("/trades/42", headers={"Accept": "application/json"}).status_code == 404


def test_csp_reports_are_accepted(app, client):
    r = client.post("/api/v1/csp-report", json={"csp-report": {"violated-directive": "style-src-attr"}})
    assert r.status_code == 204
    assert app.state.csp_reports[-1]["csp-report"]["violated-directive"] == "style-src-attr"
