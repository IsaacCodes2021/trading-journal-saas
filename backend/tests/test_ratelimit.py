"""The rate limiter on its own: a token bucket per rule and key, with a clock the test moves (no database)."""

from __future__ import annotations

import threading

import pytest

from tradejournal.api.ratelimit import RateLimiter, RateLimits, Rule, client_address, wait_text

RULE = Rule("thing", 5, 60)  # five at once, then one every twelve seconds


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def limiter(clock: Clock | None = None, **kwargs) -> tuple[RateLimiter, Clock]:
    clock = clock or Clock()
    return RateLimiter(clock=clock, **kwargs), clock


def test_a_burst_up_to_the_limit_is_allowed_and_the_next_is_refused_with_how_long_to_wait():
    bucket, _ = limiter()
    assert [bucket.hit(RULE, "ann") for _ in range(5)] == [0.0] * 5
    wait = bucket.hit(RULE, "ann")
    assert wait == pytest.approx(12.0)  # one place comes back every window / limit seconds


def test_places_come_back_at_the_stated_rate_and_a_refusal_does_not_slow_that_down():
    bucket, clock = limiter()
    for _ in range(5):
        bucket.hit(RULE, "ann")
    clock.advance(6)
    assert bucket.hit(RULE, "ann") == pytest.approx(6.0)  # half a place so far: six seconds to go
    clock.advance(6)
    assert bucket.hit(RULE, "ann") == 0.0  # twelve seconds in: exactly one place
    assert bucket.hit(RULE, "ann") == pytest.approx(12.0)


def test_asking_again_while_refused_does_not_push_the_wait_further_out():
    bucket, clock = limiter()
    for _ in range(5):
        bucket.hit(RULE, "ann")
    first = bucket.hit(RULE, "ann")
    clock.advance(4)
    second = bucket.hit(RULE, "ann")
    assert second == pytest.approx(first - 4)


def test_a_whole_window_of_quiet_restores_the_full_burst_and_no_more():
    bucket, clock = limiter()
    for _ in range(5):
        bucket.hit(RULE, "ann")
    clock.advance(60 * 100)  # a long time: still only five
    assert [bucket.hit(RULE, "ann") for _ in range(5)] == [0.0] * 5
    assert bucket.hit(RULE, "ann") > 0


def test_each_key_and_each_rule_has_its_own_allowance():
    bucket, _ = limiter()
    other = Rule("other", 5, 60)
    for _ in range(5):
        bucket.hit(RULE, "ann")
    assert bucket.hit(RULE, "ann") > 0
    assert bucket.hit(RULE, "bob") == 0.0  # another person
    assert bucket.hit(other, "ann") == 0.0  # another rule, the same person


def test_a_clock_that_goes_backwards_neither_gives_places_back_nor_locks_anyone_out_for_long():
    bucket, clock = limiter()
    for _ in range(5):
        bucket.hit(RULE, "ann")
    clock.advance(-1000)  # a monotonic clock does not do this; if one ever did, the damage must stay small
    assert bucket.hit(RULE, "ann") == pytest.approx(12.0)  # still refused, and the wait is the usual one, not minutes
    clock.advance(12)
    assert bucket.hit(RULE, "ann") == 0.0


def test_a_rule_must_allow_something():
    for limit, window in ((0, 60), (-1, 60), (5, 0), (5, -3)):
        with pytest.raises(ValueError, match="positive"):
            Rule("bad", limit, window)


def test_the_table_is_bounded_and_the_oldest_keys_are_dropped_first():
    bucket, _ = limiter(max_keys=100)
    for n in range(1000):
        bucket.hit(RULE, f"key-{n}")
    assert bucket.size() == 100
    for _ in range(5):
        bucket.hit(RULE, "key-999")  # a recent key kept its count
    assert bucket.hit(RULE, "key-999") > 0
    assert (
        bucket.hit(RULE, "key-0") == 0.0
    )  # a dropped key starts again with a full burst: the limit is a brake, not a ledger


def test_many_threads_at_once_get_exactly_the_allowance():
    bucket, _ = limiter()  # the clock never moves: no refill
    rule = Rule("busy", 50, 60)
    allowed = []

    def attempt() -> None:
        allowed.append(bucket.hit(rule, "ann") == 0.0)

    threads = [threading.Thread(target=attempt) for _ in range(300)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(allowed) == 50 and len(allowed) == 300


def test_the_defaults_are_enabled_and_every_rule_is_usable():
    limits = RateLimits()
    assert limits.enabled is True
    for rule in (
        limits.api,
        limits.export,
        limits.import_preview,
        limits.import_commit,
        limits.login,
        limits.callback,
        limits.invite_attempts,
    ):
        assert rule.limit >= 1 and rule.window > 0
    assert limits.api.limit / limits.api.window >= 5  # at least five requests a second: a page load is a handful
    assert limits.invite_attempts.limit <= 20 and limits.invite_attempts.window >= 600  # guessing codes is expensive


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (0.2, "1 second"),
        (1, "1 second"),
        (1.01, "2 seconds"),
        (30, "30 seconds"),
        (119, "119 seconds"),
        (121, "3 minutes"),
        (720, "12 minutes"),
        (7080, "118 minutes"),
        (7199, "2 hours"),
        (7201, "3 hours"),
        (3600 * 5, "5 hours"),
    ],
)
def test_the_wait_is_said_in_plain_words_and_rounded_up(seconds, text):
    assert wait_text(seconds) == text


@pytest.mark.parametrize(
    ("headers", "socket", "header", "expected"),
    [
        ({}, "10.0.0.1", None, "10.0.0.1"),
        ({"cf-connecting-ip": "203.0.113.9"}, "10.0.0.1", None, "10.0.0.1"),  # a header nobody asked for is ignored
        ({"cf-connecting-ip": "203.0.113.9"}, "10.0.0.1", "cf-connecting-ip", "203.0.113.9"),
        ({"x-forwarded-for": "198.51.100.7, 10.0.0.2"}, "10.0.0.1", "x-forwarded-for", "198.51.100.7"),
        ({"cf-connecting-ip": ""}, "10.0.0.1", "cf-connecting-ip", "10.0.0.1"),
        ({"cf-connecting-ip": " , 1.2.3.4"}, "10.0.0.1", "cf-connecting-ip", "10.0.0.1"),
        ({}, None, None, "unknown"),
        ({}, "", "cf-connecting-ip", "unknown"),
    ],
)
def test_the_client_address_comes_from_the_named_header_only_when_one_is_named(headers, socket, header, expected):
    assert client_address(headers, socket, header) == expected


def test_a_hostile_header_cannot_make_an_unbounded_key():
    key = client_address({"cf-connecting-ip": "9" * 5000}, "10.0.0.1", "cf-connecting-ip")
    assert len(key) == 64
