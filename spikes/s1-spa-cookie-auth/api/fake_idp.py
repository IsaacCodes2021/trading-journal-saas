"""A stand-in OIDC provider for the S1 spike: authorize page, authorization code with PKCE, ID-token claims.

It does not sign JWTs or publish JWKS; validating a real provider's tokens is spike S1b's job. What it does
exercise is everything on *our* side of the redirect: state, nonce, PKCE, `email_verified`, the MFA claim and
the `next` handling.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

# who -> identity. `mfa` becomes amr=["pwd","mfa"]; carol's email is not verified.
IDENTITIES = {
    "alice": {"sub": "idp|alice", "email": "alice@example.test", "name": "Alice", "mfa": False, "verified": True},
    "bob": {"sub": "idp|bob", "email": "bob@example.test", "name": "Bob", "mfa": True, "verified": True},
    "carol": {"sub": "idp|carol", "email": "carol@example.test", "name": "Carol", "mfa": False, "verified": False},
}


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class FakeIdP:
    def __init__(self, redirect_uri: str) -> None:
        self.redirect_uri = redirect_uri  # the single registered redirect URI (exact match)
        self._codes: dict[str, dict] = {}

    def exchange(self, code: str, verifier: str) -> dict | None:
        """Server-to-server step: code + PKCE verifier in, ID-token claims out (single use)."""
        entry = self._codes.pop(code, None)
        if entry is None or entry["expires"] < time.time():
            return None
        if not hmac.compare_digest(pkce_challenge(verifier), entry["challenge"]):
            return None
        who = IDENTITIES[entry["who"]]
        return {
            "sub": who["sub"],
            "email": who["email"],
            "email_verified": who["verified"],
            "name": who["name"],
            "nonce": entry["nonce"],
            "amr": ["pwd", "mfa"] if who["mfa"] else ["pwd"],
            "iat": int(time.time()),
        }

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/fake-idp", include_in_schema=False)

        @router.get("/authorize")
        def authorize(
            redirect_uri: str, state: str, nonce: str, code_challenge: str, code_challenge_method: str = "S256"
        ) -> HTMLResponse:
            if redirect_uri != self.redirect_uri:
                raise HTTPException(400, "redirect_uri is not registered")  # exact-match allow-list
            if code_challenge_method != "S256":
                raise HTTPException(400, "PKCE S256 required")
            links = []
            for who, ident in IDENTITIES.items():
                query = urlencode(
                    {"who": who, "redirect_uri": redirect_uri, "state": state, "nonce": nonce, "challenge": code_challenge}
                )
                note = "MFA" if ident["mfa"] else ("email not verified" if not ident["verified"] else "no MFA")
                links.append(f'<li><a id="signin-{who}" href="/fake-idp/approve?{html.escape(query)}">Sign in as {ident["name"]} ({note})</a></li>')
            page = f"<!doctype html><meta charset=utf-8><title>Fake IdP</title><h1>Fake identity provider</h1><ul>{''.join(links)}</ul>"
            return HTMLResponse(page)

        @router.get("/approve")
        def approve(who: str, redirect_uri: str, state: str, nonce: str, challenge: str) -> RedirectResponse:
            if redirect_uri != self.redirect_uri or who not in IDENTITIES:
                raise HTTPException(400, "bad request")
            code = secrets.token_urlsafe(16)
            self._codes[code] = {"who": who, "nonce": nonce, "challenge": challenge, "expires": time.time() + 60}
            return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", 302)

        return router
