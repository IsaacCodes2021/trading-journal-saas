"""The operator's invites, plan codes, audit log and overview with no database: what they accept and pass on (doc 07 section 6)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from admin_fakes import World

from tradejournal.api.deps import SESSION_COOKIE  # noqa: F401
from tradejournal.api.ports import AdminRefusal
from tradejournal.auth.iphash import hash_ip

BASE = "/api/admin/v1"
READS = [f"{BASE}/invites", f"{BASE}/grant-codes", f"{BASE}/audit", f"{BASE}/metrics/overview"]
WRITES = [
    ("POST", f"{BASE}/invites", {"label": "Sam"}),
    ("POST", f"{BASE}/invites/7/revoke", {"reason": "sent to the wrong person"}),
    ("POST", f"{BASE}/grant-codes", {"label": "Beta testers", "days": 30}),
    ("POST", f"{BASE}/grant-codes/3/revoke", {"reason": "leaked"}),
]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def call(world, method, path, headers, body=None):
    return world.client.request(method, path, headers=headers, json=body)


# ---- the guard, on these routes too ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", READS)
def test_a_stranger_an_ordinary_person_and_an_operator_without_fresh_verification_are_refused_on_every_read(
    world, path
):
    _, ordinary = world.person(admin=False, mfa_ago=timedelta(seconds=1), email="o@example.test")
    _, unverified = world.person(mfa_ago=None, email="u@example.test")
    _, stale = world.person(mfa_ago=timedelta(minutes=16), email="s@example.test")
    assert call(world, "GET", path, {}).status_code == 401
    assert [call(world, "GET", path, h).status_code for h in (ordinary, unverified, stale)] == [403, 403, 403]
    assert world.admin.calls == []


@pytest.mark.parametrize("method, path, body", WRITES)
def test_the_same_for_every_write_and_a_forged_write_is_refused_even_from_a_real_operator(world, method, path, body):
    _, ordinary = world.person(admin=False, mfa_ago=timedelta(seconds=1), email="o@example.test")
    operator, good = world.person(email="op@example.test")
    forged = {k: v for k, v in good.items() if k != "x-csrf-token"}
    assert call(world, method, path, {"sec-fetch-site": "same-origin"}, body).status_code == 401
    assert call(world, method, path, ordinary, body).status_code == 403
    assert call(world, method, path, forged, body).status_code == 403
    assert call(world, method, path, {**good, "x-csrf-token": "forged"}, body).status_code == 403
    assert call(world, method, path, {**good, "sec-fetch-site": "cross-site"}, body).status_code == 403
    assert world.admin.calls == []
    assert call(world, method, path, good, body).status_code == 200  # and the real thing gets through
    assert len(world.admin.calls) == 1 and operator is not None


# ---- invites ------------------------------------------------------------------------------------------------------------------


def test_the_invite_list_passes_the_page_and_shows_what_each_invite_is(world):
    _, headers = world.person()
    response = call(world, "GET", f"{BASE}/invites?limit=20&offset=40", headers)
    assert response.status_code == 200
    assert world.admin.calls == [("list_invites", 20, 40, {})]
    [row] = response.json()["invites"]
    assert row["status"] == "works" and row["used"] == 0 and "code" not in row and "code_hash" not in row


def test_making_an_invite_passes_everything_on_and_shows_the_code_once(world):
    operator, headers = world.person()
    body = {
        "label": "  Sam, futures group  ",
        "email": "  Sam@Example.test ",
        "max_uses": 3,
        "days": 14,
        "grant_code_id": 5,
    }
    response = call(world, "POST", f"{BASE}/invites", headers, body)
    assert response.status_code == 200
    assert response.json()["code"] == "TJ-ABCD-EFGH" and response.json()["invite"]["id"] == 7
    [(name, actor, label, email, max_uses, days, grant_id, kwargs)] = world.admin.calls
    assert (name, actor, label, email, max_uses, days, grant_id) == (
        "create_invite",
        operator.user.id,
        "Sam, futures group",
        "Sam@Example.test",
        3,
        14,
        5,
    )
    assert kwargs["ip_hash"] == hash_ip(b"k" * 32, "testclient")


def test_an_invite_needs_nothing_but_defaults_to_one_use_and_no_end(world):
    _, headers = world.person()
    assert call(world, "POST", f"{BASE}/invites", headers, {}).status_code == 200
    [(_, _, label, email, max_uses, days, grant_id, _)] = world.admin.calls
    assert (label, email, max_uses, days, grant_id) == ("", None, 1, None, None)


@pytest.mark.parametrize(
    "body",
    [
        {"label": "x" * 101},
        {"email": "not an address"},
        {"email": "a@b@c"},
        {"email": "@example.test"},
        {"email": "a" * 320 + "@x.test"},
        {"max_uses": 0},
        {"max_uses": 1001},
        {"max_uses": "many"},
        {"days": 0},
        {"days": 366},
        {"days": -1},
        {"grant_code_id": 0},
        {"grant_code_id": 2**63},
        {"grant_code_id": "x"},
        {"label": None},
        {"unknown": 1},
    ],
)
def test_an_invite_that_makes_no_sense_is_refused_before_anything_is_made(world, body):
    _, headers = world.person()
    response = call(world, "POST", f"{BASE}/invites", headers, body)
    assert response.status_code == 422 or (body == {"unknown": 1} and response.status_code == 200)
    if response.status_code == 422:
        assert world.admin.calls == []


def test_revoking_an_invite_needs_a_reason_and_passes_on_the_id(world):
    operator, headers = world.person()
    assert (
        call(world, "POST", f"{BASE}/invites/7/revoke", headers, {"reason": "  wrong person "}).json()["status"]
        == "revoked"
    )
    [(name, actor, invite_id, reason, _)] = world.admin.calls
    assert (name, actor, invite_id, reason) == ("revoke_invite", operator.user.id, 7, "wrong person")
    for bad in ({}, {"reason": ""}, {"reason": "  "}, {"reason": "x" * 501}):
        assert call(world, "POST", f"{BASE}/invites/7/revoke", headers, bad).status_code == 422
    assert len(world.admin.calls) == 1


@pytest.mark.parametrize("bad_id", ["x", "1.5", "-", "0x1", "%27", "9" * 30])
def test_an_invite_is_named_by_a_number_and_nothing_else(world, bad_id):
    _, headers = world.person()
    response = call(world, "POST", f"{BASE}/invites/{bad_id}/revoke", headers, {"reason": "x"})
    assert response.status_code in (404, 422) and world.admin.calls == []


# ---- plan codes ---------------------------------------------------------------------------------------------------------------


def test_making_a_plan_code_passes_it_on_and_shows_the_code_once(world):
    operator, headers = world.person()
    body = {"label": " Beta testers ", "days": 30, "max_redemptions": 25, "expires_in_days": 60}
    response = call(world, "POST", f"{BASE}/grant-codes", headers, body)
    assert response.status_code == 200 and response.json()["code"] == "GR-ABCD-EFGH"
    [(name, actor, label, plan, days, max_redemptions, ends, code, ends_on, _)] = world.admin.calls
    assert (name, actor, label, plan, days, max_redemptions, ends, code, ends_on) == (
        "create_grant_code",
        operator.user.id,
        "Beta testers",
        "pro",
        30,
        25,
        60,
        None,  # no code typed: a random one is made
        None,
    )


@pytest.mark.parametrize(("typed", "stored"), [("FREE30", "FREE30"), ("free30", "FREE30"), (" Launch7 ", "LAUNCH7")])
def test_a_typed_promo_code_is_passed_on_in_capitals_and_needs_no_note(world, typed, stored):
    _, headers = world.person()
    body = {"code": typed, "days": 30, "expires_on": "2026-12-31"}
    assert call(world, "POST", f"{BASE}/grant-codes", headers, body).status_code == 200
    [call_made] = world.admin.calls
    assert call_made[2] == "" and call_made[7] == stored and str(call_made[8]) == "2026-12-31"


@pytest.mark.parametrize(
    "code",
    ["AB", "A" * 21, "FREE-30", "FREE 30", "FREE_30", "FRÉE30", "FREE30!", "１２３"],
)
def test_a_promo_code_of_the_wrong_shape_is_refused_before_anything_is_made(world, code):
    _, headers = world.person()
    body = {"code": code, "label": "x", "days": 30}
    assert call(world, "POST", f"{BASE}/grant-codes", headers, body).status_code == 422
    assert world.admin.calls == []


def test_a_code_cannot_end_on_a_date_and_after_some_days_at_once(world):
    _, headers = world.person()
    body = {"code": "FREE30", "days": 30, "expires_in_days": 5, "expires_on": "2026-12-31"}
    assert call(world, "POST", f"{BASE}/grant-codes", headers, body).status_code == 422
    assert world.admin.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"label": "x"},
        {"days": 30},
        {"label": "", "days": 30},
        {"label": "  ", "days": 30},
        {"label": "x" * 101, "days": 30},
        {"label": "x", "days": 0},
        {"label": "x", "days": 31},
        {"label": "x", "days": 30, "plan": "free"},
        {"label": "x", "days": 30, "plan": "gold"},
        {"label": "x", "days": 30, "max_redemptions": 0},
        {"label": "x", "days": 30, "max_redemptions": 1_000_001},
        {"label": "x", "days": 30, "expires_in_days": 0},
        {"label": "x", "days": 30, "expires_in_days": 3651},
        {"code": "", "days": 30},  # an empty code is a random one, and a random one needs a note
        {"label": "x", "days": 30, "kind": "discount"},  # discounts arrive with the payment provider
        {"label": "x", "days": 30, "expires_on": "31/12/2026"},
    ],
)
def test_a_plan_code_that_makes_no_sense_is_refused_before_anything_is_made(world, body):
    _, headers = world.person()
    assert call(world, "POST", f"{BASE}/grant-codes", headers, body).status_code == 422
    assert world.admin.calls == []


def test_the_plan_code_list_and_revoke_pass_things_on(world):
    operator, headers = world.person()
    assert call(world, "GET", f"{BASE}/grant-codes?limit=5&offset=10", headers).status_code == 200
    assert (
        call(world, "POST", f"{BASE}/grant-codes/3/revoke", headers, {"reason": "leaked"}).json()["status"] == "revoked"
    )
    assert world.admin.calls[0][:4] == ("list_grant_codes", 5, 10, None)
    assert world.admin.calls[1][:4] == ("revoke_grant_code", operator.user.id, 3, "leaked")


@pytest.mark.parametrize("show", ["works", "ended", "revoked"])
def test_the_code_list_can_show_one_kind_of_code(world, show):
    _, headers = world.person()
    assert call(world, "GET", f"{BASE}/grant-codes?show={show}", headers).status_code == 200
    assert world.admin.calls[0][:4] == ("list_grant_codes", 50, 0, show)


def test_the_code_list_refuses_a_filter_it_does_not_know(world):
    _, headers = world.person()
    assert call(world, "GET", f"{BASE}/grant-codes?show=everything", headers).status_code == 422
    assert world.admin.calls == []


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=x", "offset=-1", "offset=1000001"])
@pytest.mark.parametrize("path", [f"{BASE}/invites", f"{BASE}/grant-codes"])
def test_a_list_outside_its_bounds_is_refused(world, path, query):
    _, headers = world.person()
    assert call(world, "GET", f"{path}?{query}", headers).status_code == 422
    assert world.admin.calls == []


# ---- the audit log and the overview ----------------------------------------------------------------------------------------


def test_the_audit_log_passes_every_filter_on_and_defaults_to_fifty(world):
    _, headers = world.person()
    call(world, "GET", f"{BASE}/audit", headers)
    call(world, "GET", f"{BASE}/audit?action=admin_suspended&q=dana&actor=admin&days=7&limit=20&before=900", headers)
    assert world.admin.calls == [
        ("audit", None, None, None, None, 50, None, {}),
        ("audit", "admin_suspended", "dana", "admin", 7, 20, 900, {}),
    ]


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "days=0",
        "days=3651",
        "before=0",
        "before=x",
        "before=" + str(2**63),
        "action=" + "x" * 65,
        "q=" + "x" * 101,
        "actor=" + "x" * 65,
    ],
)
def test_an_audit_request_outside_its_bounds_is_refused_before_anything_is_read(world, query):
    _, headers = world.person()
    assert call(world, "GET", f"{BASE}/audit?{query}", headers).status_code == 422
    assert world.admin.calls == []


def test_the_overview_has_the_numbers_and_nothing_a_person_wrote(world):
    _, headers = world.person()
    body = call(world, "GET", f"{BASE}/metrics/overview", headers).json()
    assert body == {
        "accounts": 64,
        "signups_7d": 18,
        "active_7d": 41,
        "active": 60,
        "suspended": 2,
        "pending_deletion": 2,
        "pro": 7,
        "free": 57,
        "open_invites": 3,
        "open_grant_codes": 1,
        "grants_ending_7d": 0,
        "on_trial": 0,
        "paying": 0,
    }


def test_a_refusal_from_the_service_reaches_the_client_as_a_problem(world):
    _, headers = world.person()
    world.admin.refuse = AdminRefusal(409, "That invite is already revoked.")
    response = call(world, "POST", f"{BASE}/invites/7/revoke", headers, {"reason": "x"})
    assert response.status_code == 409 and response.json()["detail"] == "That invite is already revoked."


def test_none_of_it_is_cached(world):
    _, headers = world.person()
    for path in READS:
        assert call(world, "GET", path, headers).headers["cache-control"] == "no-store"
