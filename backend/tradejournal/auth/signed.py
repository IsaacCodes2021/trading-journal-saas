"""A small signed value for the sign-in state cookie: JSON, a timestamp, and an HMAC tag.

The sign-in state (`state`, `nonce`, the PKCE verifier, where to go next, the invite code) lives in a short-lived cookie
the callback reads back. It is signed so the browser cannot edit it, and it expires after ten minutes. It is not
encrypted: the cookie is `HttpOnly`, so only the person's own browser holds it, and every value in it is one that
browser is meant to present to the identity provider anyway.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from typing import Any

MAX_AGE_SECONDS = 600


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _tag(secret: bytes, purpose: str, body: str) -> str:
    # The purpose is part of what is signed, so a value signed for one cookie cannot be presented as another.
    return _b64(hmac.new(secret, f"{purpose}.{body}".encode(), hashlib.sha256).digest())


def sign(secret: bytes, purpose: str, payload: dict[str, Any], *, now: Callable[[], float] = time.time) -> str:
    body = _b64(json.dumps({**payload, "iat": int(now())}, separators=(",", ":")).encode())
    return f"{body}.{_tag(secret, purpose, body)}"


def unsign(
    secret: bytes,
    purpose: str,
    value: str | None,
    *,
    max_age: int = MAX_AGE_SECONDS,
    now: Callable[[], float] = time.time,
) -> dict[str, Any] | None:
    """The payload, or None if the value is missing, malformed, signed with another key or purpose, or too old."""
    if not value or len(value) > 4096 or value.count(".") != 1:
        return None
    body, tag = value.split(".")
    if not hmac.compare_digest(tag.encode(), _tag(secret, purpose, body).encode()):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, UnicodeDecodeError):
        return None
    issued = payload.get("iat") if isinstance(payload, dict) else None
    if not isinstance(issued, int) or not 0 <= now() - issued <= max_age:
        return None
    return payload
