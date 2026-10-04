"""`POST /api/v1/grants/redeem`: use a plan code (no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.ports import GrantResult
from tradejournal.api.schemas import PlanOut
from tradejournal.auth.csrf import csrf_token
from tradejournal.auth.iphash import hash_ip

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
PRO_UNTIL = datetime(2027, 1, 12, 9, 30, tzinfo=UTC)
PRO = PlanOut(effective="pro", source="grant", expires_at=PRO_UNTIL)
REFUSED = "That code did not work. It may be wrong, already used, expired, or revoked."


def build(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    client = TestClient(create_app(config, services), base_url=ORIGIN)

    def person(email):
        account = accounts.add(email)
        token, session = sessions.sign_in(account.user.id)
        headers = {"cookie": f"{SESSION_COOKIE}={token}", **SAME_ORIGIN, "x-csrf-token": csrf_token(SECRET, session.id)}
        return account, headers

    return client, accounts, person


@pytest.fixture
def world(tmp_path):
    client, accounts, person = build(tmp_path)
    alice, headers = person("alice@example.test")
    return client, accounts, alice, headers


def redeem(client, headers, code="GR-ABCD-EFGH"):
    return client.post("/api/v1/grants/redeem", json={"code": code}, headers=headers)


def test_a_code_that_works_answers_the_plan_the_person_is_on_afterwards(world):
    client, accounts, alice, headers = world
    accounts.codes["GR-ABCD-EFGH"] = GrantResult("ok", plan=PRO)
    response = redeem(client, headers)
    assert response.status_code == 200
    assert response.json() == {"effective": "pro", "source": "grant", "expires_at": "2027-01-12T09:30:00Z"}
    assert accounts.load(alice.user.id).plan == PRO  # and that is the plan the session now reports


def test_the_code_is_passed_as_sent_for_the_service_to_tidy_and_the_address_is_hashed(world):
    client, accounts, alice, headers = world
    accounts.codes["  gr abcd efgh "] = GrantResult("ok", plan=PRO)
    assert redeem(client, headers, "  gr abcd efgh ").status_code == 200
    [(who, code, ip_hash)] = accounts.redeemed
    assert who == alice.user.id and code == "  gr abcd efgh "
    assert ip_hash == hash_ip(SECRET, "testclient") and "testclient" not in str(ip_hash)


def test_a_code_that_does_not_work_is_one_answer_whatever_the_reason(world):
    client, _, _, headers = world
    responses = [redeem(client, headers, code) for code in ("GR-NOPE-NOPE", "x", "GR-USED-CODE")]
    assert [r.status_code for r in responses] == [422, 422, 422]
    assert {r.json()["detail"] for r in responses} == {REFUSED}


def test_a_person_with_a_plan_from_a_code_is_told_when_it_ends(world):
    client, accounts, alice, headers = world
    accounts.codes["GR-ABCD-EFGH"] = GrantResult("active", ends_at=datetime(2026, 12, 3, 8, 0, tzinfo=UTC))
    response = redeem(client, headers)
    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "You already have a plan from a code, until Dec 3, 2026. Use another code when it ends."
    )
    assert accounts.load(alice.user.id).plan.effective == "free"  # nothing changed


@pytest.mark.parametrize(
    "body", [{}, {"code": ""}, {"code": "x" * 65}, {"code": 7}, {"code": None}, {"other": "GR-AAAA-BBBB"}]
)
def test_a_body_that_is_not_a_code_is_refused_before_anything_is_tried(world, body):
    client, accounts, _, headers = world
    assert client.post("/api/v1/grants/redeem", json=body, headers=headers).status_code == 422
    assert accounts.redeemed == []


def test_it_needs_a_session_and_the_csrf_token(world):
    client, accounts, _, headers = world
    accounts.codes["GR-ABCD-EFGH"] = GrantResult("ok", plan=PRO)
    body = {"code": "GR-ABCD-EFGH"}
    assert client.post("/api/v1/grants/redeem", json=body, headers=SAME_ORIGIN).status_code == 401
    without_token = {k: v for k, v in headers.items() if k != "x-csrf-token"}
    assert client.post("/api/v1/grants/redeem", json=body, headers=without_token).status_code == 403
    assert accounts.redeemed == []


def test_a_person_may_try_ten_codes_an_hour_and_the_eleventh_waits(world):
    client, accounts, _, headers = world
    answers = [redeem(client, headers, f"GR-TRY{i}-CODE").status_code for i in range(11)]
    assert answers == [422] * 10 + [429]
    assert len(accounts.redeemed) == 10  # the eleventh never reached the service


def test_one_persons_tries_do_not_use_up_anothers(tmp_path):
    client, accounts, person = build(tmp_path)
    _, alice = person("alice@example.test")
    _, bob = person("bob@example.test")
    for i in range(10):
        redeem(client, alice, f"GR-ALCE-{i}")
    assert redeem(client, alice).status_code == 429
    assert redeem(client, bob).status_code == 422  # bob's own allowance is untouched
