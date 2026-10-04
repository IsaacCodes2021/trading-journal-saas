"""Rate limits through the real app: who is counted, what the refusal says, and what is never counted (no database)."""

from __future__ import annotations

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.api.ratelimit import RateLimiter, RateLimits, Rule
from tradejournal.auth.csrf import csrf_token

ORIGIN = "https://app.example.com"
SECRET = b"k" * 32


class Clock:
    def __init__(self) -> None:
        self.now = 5000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def world(tmp_path):
    services, sessions, accounts = make_services()
    clock = Clock()

    def build(**limits):
        config = ApiConfig(
            app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=SECRET, rate_limits=RateLimits(**limits)
        )
        return TestClient(create_app(config, services, limiter=RateLimiter(clock=clock)), base_url=ORIGIN)

    def person(email):
        account = accounts.add(email)
        token, session = sessions.sign_in(account.user.id)
        return {"cookie": f"{SESSION_COOKIE}={token}"}, session

    return build, person, clock


def test_a_person_gets_their_allowance_then_a_plain_refusal_that_says_when_to_come_back(world):
    build, person, _ = world
    client = build(api=Rule("api", 3, 60))
    ann, _ = person("ann@example.test")
    assert [client.get("/api/v1/session", headers=ann).status_code for _ in range(3)] == [200, 200, 200]
    refused = client.get("/api/v1/session", headers=ann)
    assert refused.status_code == 429
    assert refused.headers["content-type"] == "application/problem+json"
    assert refused.headers["retry-after"] == "20"  # three per minute: one back every twenty seconds
    assert refused.json()["detail"] == "Too many requests. Try again in 20 seconds."


def test_it_is_per_person_and_comes_back_with_time(world):
    build, person, clock = world
    client = build(api=Rule("api", 2, 60))
    ann, _ = person("ann@example.test")
    bob, _ = person("bob@example.test")
    for _ in range(2):
        client.get("/api/v1/session", headers=ann)
    assert client.get("/api/v1/session", headers=ann).status_code == 429
    assert client.get("/api/v1/session", headers=bob).status_code == 200  # someone else's allowance is their own
    clock.now += 30
    assert client.get("/api/v1/session", headers=ann).status_code == 200  # one place came back


def test_strangers_and_bad_cookies_are_turned_away_without_costing_anyone_a_place(world):
    build, person, _ = world
    client = build(api=Rule("api", 2, 60))
    ann, _ = person("ann@example.test")
    for headers in ({}, {"cookie": f"{SESSION_COOKIE}=not-a-token"}) * 10:
        assert client.get("/api/v1/session", headers=headers).status_code == 401
    assert [client.get("/api/v1/session", headers=ann).status_code for _ in range(2)] == [200, 200]


def test_a_refused_request_does_not_run_the_route_or_change_anything(world):
    build, person, clock = world
    client = build(api=Rule("api", 2, 60))
    ann, session = person("ann@example.test")
    write = {**ann, "sec-fetch-site": "same-origin", "x-csrf-token": csrf_token(SECRET, session.id)}
    assert client.patch("/api/v1/settings", json={"theme": "light"}, headers=write).status_code == 200
    assert client.get("/api/v1/session", headers=ann).status_code == 200  # the second and last place
    refused = client.patch("/api/v1/settings", json={"theme": "dark"}, headers=write)
    assert refused.status_code == 429
    clock.now += 60  # the allowance is back
    assert (
        client.get("/api/v1/session", headers=ann).json()["settings"]["theme"] == "light"
    )  # the refused change never ran


def test_turning_the_limits_off_turns_them_off(world):
    build, person, _ = world
    client = build(enabled=False, api=Rule("api", 1, 60))
    ann, _ = person("ann@example.test")
    assert [client.get("/api/v1/session", headers=ann).status_code for _ in range(20)] == [200] * 20


def test_the_default_allowance_is_enough_for_a_person_using_the_app(world):
    build, person, _ = world
    client = build()
    ann, _ = person("ann@example.test")
    assert all(client.get("/api/v1/session", headers=ann).status_code == 200 for _ in range(100))  # a busy minute


# ---- configuration ------------------------------------------------------------------------------------------------------


def test_the_limits_can_be_switched_off_from_the_environment_and_are_on_otherwise():
    assert ApiConfig.from_env({}).rate_limits.enabled is True
    for value in ("off", "OFF", "0", "false", "no"):
        assert ApiConfig.from_env({"TJ_RATE_LIMITS": value}).rate_limits.enabled is False
    for value in ("on", "1", "yes", "", "anything else"):
        assert ApiConfig.from_env({"TJ_RATE_LIMITS": value}).rate_limits.enabled is True


def test_the_client_address_header_is_a_header_name_and_nothing_else():
    assert ApiConfig.from_env({}).client_ip_header is None
    assert ApiConfig.from_env({"TJ_CLIENT_IP_HEADER": "CF-Connecting-IP"}).client_ip_header == "CF-Connecting-IP"
    for bad in ("CF Connecting IP", "x:y", "a" * 65, "x\r\nSet-Cookie: a=b", "ünï"):
        with pytest.raises(ConfigError, match="TJ_CLIENT_IP_HEADER"):
            ApiConfig.from_env({"TJ_CLIENT_IP_HEADER": bad})
