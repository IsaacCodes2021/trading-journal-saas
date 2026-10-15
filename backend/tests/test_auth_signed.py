"""The signed sign-in state cookie and the `next` address rule: no database."""

from __future__ import annotations

import pytest

from tradejournal.auth.flow import safe_next
from tradejournal.auth.signed import MAX_AGE_SECONDS, sign, unsign

SECRET = b"s" * 32


def test_a_signed_value_comes_back_and_carries_when_it_was_made():
    value = sign(SECRET, "login-state", {"s": "state", "n": "nonce"}, now=lambda: 1_000)
    assert unsign(SECRET, "login-state", value, now=lambda: 1_100) == {"s": "state", "n": "nonce", "iat": 1_000}


def test_a_value_changed_by_one_character_or_signed_with_another_key_or_purpose_is_refused():
    value = sign(SECRET, "login-state", {"s": "state"}, now=lambda: 1_000)
    body, tag = value.split(".")
    assert (
        unsign(SECRET, "login-state", f"{body}.{tag[:-1]}{'A' if tag[-1] != 'A' else 'B'}", now=lambda: 1_000) is None
    )
    assert (
        unsign(SECRET, "login-state", f"{body[:-1]}{'A' if body[-1] != 'A' else 'B'}.{tag}", now=lambda: 1_000) is None
    )
    assert unsign(b"k" * 32, "login-state", value, now=lambda: 1_000) is None
    assert unsign(SECRET, "session", value, now=lambda: 1_000) is None  # a value for one cookie is not another's


def test_a_value_expires_and_one_from_the_future_is_refused_too():
    value = sign(SECRET, "p", {"x": 1}, now=lambda: 1_000)
    assert unsign(SECRET, "p", value, now=lambda: 1_000 + MAX_AGE_SECONDS) is not None
    assert unsign(SECRET, "p", value, now=lambda: 1_000 + MAX_AGE_SECONDS + 1) is None
    assert (
        unsign(SECRET, "p", value, now=lambda: 999) is None
    )  # a clock that went backwards is not a reason to trust it


@pytest.mark.parametrize("junk", [None, "", ".", "a.b.c", "nodot", "x" * 5000, "e30.", ".AAAA", "!!!.???"])
def test_junk_is_refused_without_an_error(junk):
    assert unsign(SECRET, "p", junk) is None


@pytest.mark.parametrize("good", ["/", "/trades", "/trades/12?preset=30d&firms=Apex", "/settings/accounts?account=3#x"])
def test_a_path_on_this_site_is_kept(good):
    assert safe_next(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "trades",
        "//evil.example",
        "//evil.example/trades",
        "https://evil.example",
        "http://evil.example/",
        "javascript:alert(1)",
        "/\\evil.example",
        "/%0d%0aSet-Cookie:x=1" and "/\r\nSet-Cookie: x=1",
        "/\x00",
        "/auth/login",
        "/auth/callback?code=x",
        "/" + "a" * 600,
    ],
)
def test_anything_else_goes_to_the_home_page(bad):
    assert safe_next(bad) == "/"


def test_the_error_codes_are_the_ones_the_front_end_has_words_for():
    """`/invite?error=<code>` is the only thing the page learns. `frontend/src/lib/auth.ts` has a sentence for each code;
    a code with no sentence would show nothing, and a sentence for a code that is never sent would be dead."""
    import re
    from pathlib import Path

    from tradejournal.auth import flow

    sent = {value for name, value in vars(flow).items() if name.startswith("ERR_")}
    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "auth.ts").read_text(encoding="utf-8")
    block = source.split("export const SIGN_IN_ERRORS = {")[1].split("} as const")[0]
    worded = set(re.findall(r"^  (\w+):", block, flags=re.MULTILINE))
    assert sent == worded and len(sent) == 8
