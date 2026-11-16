"""`PUT /api/v1/account/username` and `POST /api/v1/account/username/skip`: picking a username (no database)."""

from __future__ import annotations

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.routers.username import TAKEN
from tradejournal.api.schemas.username import is_reserved
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32
SAME_ORIGIN = {"sec-fetch-site": "same-origin"}


def build(tmp_path):
    services, sessions, accounts = make_services()
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET)
    client = TestClient(create_app(config, services), base_url=ORIGIN)

    def person(email, username=None):
        account = accounts.add(email, username=username)
        token, session = sessions.sign_in(account.user.id)
        headers = {"cookie": f"{SESSION_COOKIE}={token}", **SAME_ORIGIN, "x-csrf-token": csrf_token(SECRET, session.id)}
        return account, headers

    return client, accounts, person


@pytest.fixture
def world(tmp_path):
    client, accounts, person = build(tmp_path)
    alice, headers = person("alice@example.test")
    return client, accounts, person, alice, headers


def put(client, headers, username):
    return client.put("/api/v1/account/username", json={"username": username}, headers=headers)


def session_of(client, headers):
    return client.get("/api/v1/session", headers={"cookie": headers["cookie"]}).json()


# ---- picking and changing ------------------------------------------------------------------------------------------


def test_a_person_without_a_username_is_asked_for_one(world):
    client, _, _, _, headers = world
    body = session_of(client, headers)
    assert body["user"]["username"] is None
    assert body["flags"]["ask_username"] is True


def test_picking_a_username_saves_it_and_stops_the_prompt(world):
    client, _, _, _, headers = world
    response = put(client, headers, "Trader_Joe")
    assert response.status_code == 200
    assert response.json() == {"username": "Trader_Joe"}
    body = session_of(client, headers)
    assert body["user"]["username"] == "Trader_Joe"  # the capitals as typed
    assert body["flags"]["ask_username"] is False


def test_spaces_around_the_name_are_dropped(world):
    client, _, _, _, headers = world
    assert put(client, headers, "  joe_99 ").json() == {"username": "joe_99"}


def test_a_username_can_be_changed_at_any_time(world):
    client, _, _, _, headers = world
    for name in ("first_name", "second_name", "Second_Name", "third"):
        assert put(client, headers, name).status_code == 200
        assert session_of(client, headers)["user"]["username"] == name


@pytest.mark.parametrize(
    ("username", "message"),
    [
        ("", "Use at least 3 characters."),
        ("ab", "Use at least 3 characters."),
        ("   ", "Use at least 3 characters."),
        ("a" * 21, "Use at most 20 characters."),
        ("joe smith", "Use only letters, numbers and underscores."),
        ("joe-smith", "Use only letters, numbers and underscores."),
        ("joe.smith", "Use only letters, numbers and underscores."),
        ("jöe", "Use only letters, numbers and underscores."),
        ("joe@x", "Use only letters, numbers and underscores."),
    ],
)
def test_a_username_of_the_wrong_shape_is_refused_with_why(world, username, message):
    client, _, _, _, headers = world
    response = put(client, headers, username)
    assert response.status_code == 422
    assert response.json()["errors"] == [{"field": "username", "message": message}]
    assert session_of(client, headers)["user"]["username"] is None


def test_three_and_twenty_characters_are_the_limits(world):
    client, _, _, _, headers = world
    assert put(client, headers, "abc").status_code == 200
    assert put(client, headers, "a" * 20).status_code == 200
    assert put(client, headers, "b" * 21).status_code == 422


def test_a_field_the_api_does_not_know_is_refused(world):
    client, _, _, _, headers = world
    response = client.put("/api/v1/account/username", json={"username": "joe", "name": "x"}, headers=headers)
    assert response.status_code == 422


# ---- one per account -----------------------------------------------------------------------------------------------


def test_a_username_someone_else_holds_is_taken_whatever_the_capitals(world):
    client, _, person, _, headers = world
    person("bob@example.test", username="Trader_Joe")
    for attempt in ("Trader_Joe", "trader_joe", "TRADER_JOE"):
        response = put(client, headers, attempt)
        assert response.status_code == 409, attempt
        assert response.json()["detail"] == TAKEN
    assert session_of(client, headers)["user"]["username"] is None


def test_a_name_given_up_is_free_for_someone_else(world):
    client, _, person, _, alice = world
    _, bob = person("bob@example.test")
    assert put(client, alice, "shared_name").status_code == 200
    assert put(client, bob, "shared_name").status_code == 409
    assert put(client, alice, "new_name").status_code == 200
    assert put(client, bob, "shared_name").status_code == 200


@pytest.mark.parametrize("username", ["admin", "Admin", "SUPPORT", "roundturn", "RoundTurn_Help", "the_roundturn_team"])
def test_reserved_names_get_the_same_answer_as_a_taken_one(world, username):
    client, _, _, _, headers = world
    response = put(client, headers, username)
    assert response.status_code == 409
    assert response.json()["detail"] == TAKEN


def test_ordinary_names_are_not_reserved():
    assert not any(is_reserved(name) for name in ("adminton", "helpful_hank", "joe", "round_turner", "turn_round"))


# ---- not now ------------------------------------------------------------------------------------------------------


def test_not_now_stops_the_prompt_and_leaves_no_username(world):
    client, accounts, _, alice, headers = world
    response = client.post("/api/v1/account/username/skip", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"ask_username": False}
    assert accounts.skipped == [alice.user.id]
    body = session_of(client, headers)
    assert body["flags"]["ask_username"] is False
    assert body["user"]["username"] is None


def test_a_username_can_still_be_picked_after_not_now(world):
    client, _, _, _, headers = world
    client.post("/api/v1/account/username/skip", headers=headers)
    assert put(client, headers, "later_pick").status_code == 200
    assert session_of(client, headers)["user"]["username"] == "later_pick"


def test_someone_with_a_username_is_not_asked(world):
    client, _, person, _, _ = world
    _, headers = person("carol@example.test", username="carol")
    assert session_of(client, headers)["flags"]["ask_username"] is False


# ---- guards -------------------------------------------------------------------------------------------------------


def test_both_routes_need_sign_in_and_the_csrf_token(world):
    client, accounts, _, _, headers = world
    without_token = {k: v for k, v in headers.items() if k != "x-csrf-token"}
    for call in (
        lambda h: client.put("/api/v1/account/username", json={"username": "joe"}, headers=h),
        lambda h: client.post("/api/v1/account/username/skip", headers=h),
    ):
        assert call(SAME_ORIGIN).status_code == 401
        assert call(without_token).status_code == 403
    assert accounts.skipped == []
    assert session_of(client, headers)["user"]["username"] is None


def test_an_account_whose_access_ended_can_still_pick_a_username(world):
    client, accounts, _, alice, headers = world
    accounts.end_access(alice.user.id)
    assert put(client, headers, "still_here").status_code == 200
    assert client.post("/api/v1/account/username/skip", headers=headers).status_code == 200


def test_a_person_may_try_thirty_names_an_hour_and_the_next_waits(world):
    client, _, person, _, headers = world
    person("bob@example.test", username="taken_name")
    answers = [put(client, headers, "taken_name").status_code for _ in range(31)]
    assert answers == [409] * 30 + [429]


def test_one_persons_tries_do_not_use_up_anothers(tmp_path):
    client, _, person = build(tmp_path)
    _, alice = person("alice@example.test")
    _, bob = person("bob@example.test")
    for i in range(30):
        put(client, alice, f"alice_{i}")
    assert put(client, alice, "alice_x").status_code == 429
    assert put(client, bob, "bob_name").status_code == 200
