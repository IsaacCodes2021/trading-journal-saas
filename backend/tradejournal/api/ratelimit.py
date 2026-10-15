"""Rate limits (doc 04 T2, T5 and T18; doc 02 `ratelimit.py`).

A token bucket per (rule, key): a rule says how many requests are allowed in a burst and how fast the allowance comes back
(`limit` per `window` seconds). The key is the signed-in person for anything behind a session, and the client's address for
what happens before one (sign-in, trying invite codes). Taking a place is O(1) and the table is bounded, so a flood of
different keys cannot grow memory without limit (the oldest keys are dropped first).

**This is per process.** With two instances a person gets twice the allowance; it is a brake on abuse and accidents, not
an exact meter. The design moves it to Postgres or Redis when load needs that (doc 02). Cloudflare's own rules sit in front
of it for floods.

**The client address** is the socket's, unless the host says which header carries the real one (`TJ_CLIENT_IP_HEADER`,
for example `CF-Connecting-IP` behind Cloudflare). Name a header only if every request comes through a proxy that sets it,
because otherwise a caller could choose their own address and so their own bucket.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int  # the burst allowed at once, and the number allowed per `window` once it is used up
    window: float  # seconds

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window <= 0:
            raise ValueError("a rate limit needs a positive limit and window")


@dataclass(frozen=True)
class RateLimits:
    """The rules, as configuration. The defaults are generous for a person using the app and tight for a script."""

    enabled: bool = True
    api: Rule = Rule("api", 600, 60)  # everything behind a session, per person: a safety net, not a plan limit
    export: Rule = Rule("export", 5, 3600)  # an export reads every table
    import_preview: Rule = Rule("import_preview", 60, 3600)
    import_commit: Rule = Rule("import_commit", 30, 3600)
    screenshot_upload: Rule = Rule(
        "screenshot_upload", 120, 3600
    )  # one image per request: a review session of a few dozen
    login: Rule = Rule("login", 30, 60)  # starting a sign-in, per client address
    beta_join: Rule = Rule("beta_join", 5, 3600)  # asking to join the beta, per client address
    callback: Rule = Rule("callback", 30, 60)
    invite_attempts: Rule = Rule("invite_attempts", 10, 900)  # sign-in attempts that carry an invite code
    redeem: Rule = Rule("redeem", 10, 3600)  # plan codes a person tries, right or wrong
    billing: Rule = Rule("billing", 10, 3600)  # checkout, cancel, resume and the provider's page, per person


class RateLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 200_000) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[tuple[str, str], tuple[float, float]] = OrderedDict()  # (rule, key): (tokens, at)
        self._lock = threading.Lock()

    def hit(self, rule: Rule, key: str) -> float:
        """Take one place. Returns 0.0 when it is allowed, otherwise how many seconds until a place is free."""
        rate = rule.limit / rule.window
        now = self._clock()
        slot = (rule.name, key)
        with self._lock:
            tokens, at = self._buckets.get(slot, (float(rule.limit), now))
            tokens = min(float(rule.limit), tokens + max(0.0, now - at) * rate)
            if tokens >= 1.0:
                result, tokens = 0.0, tokens - 1.0
            else:
                result = (1.0 - tokens) / rate
            self._buckets[slot] = (tokens, now)
            self._buckets.move_to_end(slot)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return result

    def size(self) -> int:
        with self._lock:
            return len(self._buckets)


def wait_text(seconds: float) -> str:
    """ "Try again in 30 seconds", "in 12 minutes", "in 2 hours": rounded up, so it is never too soon."""
    whole = max(1, math.ceil(seconds))
    if whole < 120:
        return f"{whole} second{'s' if whole != 1 else ''}"
    minutes = math.ceil(whole / 60)
    if minutes < 120:
        return f"{minutes} minutes"
    return f"{math.ceil(minutes / 60)} hours"


def client_address(headers: Mapping[str, str], socket_host: str | None, header: str | None) -> str:
    """The client's address for rate limiting: from the named header if the host configured one and it is there (its first
    entry, bounded in length), else the socket's. Never empty."""
    if header:
        first = headers.get(header, "").split(",")[0].strip()[:64]
        if first:
            return first
    return socket_host or "unknown"
