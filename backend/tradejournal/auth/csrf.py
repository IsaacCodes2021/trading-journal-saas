"""CSRF defense for unsafe requests: the layers of ADR-011 that live in the API.

Layer 1 (`SameSite=Lax` on the session cookie) is set where the cookie is. This module is layers 2 to 4:
`Sec-Fetch-Site`/`Origin` must say same-origin, a per-session token must come back in `X-CSRF-Token`, and the body
must be JSON or multipart (a plain HTML form cannot send either cross-site without a preflight, and no CORS is
configured, so no preflight ever succeeds).
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import HTTPException, Request

CSRF_HEADER = "x-csrf-token"
ALLOWED_CONTENT_TYPES = ("application/json", "multipart/form-data", "")  # "" is a request with no body


def csrf_token(secret: bytes, session_id: str) -> str:
    """HMAC of the session id: nothing is stored, and a token only ever works for its own session."""
    return hmac.new(secret, session_id.encode(), hashlib.sha256).hexdigest()


def check_csrf(request: Request, expected_token: str, app_origin: str) -> None:
    """Raise 403 (or 415) unless this unsafe request clearly came from our own page."""
    fetch_site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    if origin is not None and origin != app_origin:
        raise HTTPException(403, "foreign origin refused")  # also catches "Origin: null" from sandboxed pages
    if fetch_site is not None:
        if fetch_site != "same-origin":
            raise HTTPException(403, "cross-site request refused")
    elif origin is None:
        raise HTTPException(403, "origin missing")  # a browser always sends one of the two on unsafe methods
    # Compared as bytes: `compare_digest` raises TypeError on a str with non-ASCII characters, which a client controls.
    if not hmac.compare_digest(request.headers.get(CSRF_HEADER, "").encode(), expected_token.encode()):
        raise HTTPException(403, "CSRF token missing or wrong")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(415, "unsupported content type")
