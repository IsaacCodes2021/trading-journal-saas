"""Session, CSRF and security-header helpers for the S1 spike (ADR-011).

Everything here is deliberately small enough to read in one sitting.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

SESSION_COOKIE = "__Host-tj_session"
OIDC_COOKIE = "__Host-tj_oidc"
IDLE_SECONDS = 14 * 24 * 3600

# Strict CSP: one external script, no inline anything. `report-uri` / `report-to` let the spike collect
# violations from a real browser instead of guessing.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "report-uri /api/v1/csp-report",
        "report-to csp",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Reporting-Endpoints": 'csp="/api/v1/csp-report"',
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def next_path(value: str | None) -> str:
    """Open-redirect defense: only same-origin absolute paths survive; everything else becomes `/`."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return "/"
    return value


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Session:
    user_id: int
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    mfa_at: float | None = None
    revoked: bool = False


class SessionStore:
    """In-memory stand-in for the `sessions` table: only a hash of the token is kept."""

    def __init__(self, secret: bytes) -> None:
        self._secret = secret
        self._by_hash: dict[str, Session] = {}

    def create(self, user_id: int, mfa: bool) -> str:
        token = secrets.token_urlsafe(32)
        self._by_hash[sha256_hex(token)] = Session(user_id=user_id, mfa_at=time.time() if mfa else None)
        return token

    def lookup(self, token: str | None) -> tuple[str, Session] | None:
        if not token:
            return None
        key = sha256_hex(token)
        session = self._by_hash.get(key)
        if session is None or session.revoked or time.time() - session.last_seen > IDLE_SECONDS:
            return None
        session.last_seen = time.time()
        return key, session

    def revoke(self, token: str | None) -> None:
        if token and (session := self._by_hash.get(sha256_hex(token))):
            session.revoked = True

    def csrf_token(self, session_key: str) -> str:
        """HMAC of the session id: nothing is stored, and a token only ever works for its own session."""
        return hmac.new(self._secret, session_key.encode(), hashlib.sha256).hexdigest()


def check_csrf(request: Request, expected_token: str, app_origin: str) -> None:
    """The layers of ADR-011 that apply to an unsafe request. SameSite=Lax (layer 1) is set on the cookie."""
    fetch_site = request.headers.get("sec-fetch-site")
    origin = request.headers.get("origin")
    if fetch_site is not None:
        if fetch_site != "same-origin":
            raise HTTPException(403, "cross-site request refused")
    elif origin is not None:
        if origin != app_origin:
            raise HTTPException(403, "foreign origin refused")
    else:
        raise HTTPException(403, "origin missing")  # a browser always sends one of the two on unsafe methods
    supplied = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(supplied, expected_token):
        raise HTTPException(403, "CSRF token missing or wrong")
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ("application/json", "multipart/form-data", ""):
        raise HTTPException(415, "unsupported content type")
