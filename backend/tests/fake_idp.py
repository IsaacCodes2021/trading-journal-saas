"""A pretend identity provider for testing the sign-in flow without a network (doc 05: "a fake IdP").

It keeps what a real one keeps: the `state`, `nonce` and PKCE challenge of each attempt, and a single-use code the
"person" gets after signing in. `complete()` then does what the real provider does on the token request (checks the PKCE
verifier against the challenge, refuses an unknown or reused code) and hands back an identity for the nonce that was sent,
so a test can also break each of those on purpose.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlsplit

from tradejournal.auth.idp import Identity, IdentityError


@dataclass
class Attempt:
    state: str
    nonce: str
    challenge: str
    redirect_uri: str
    step_up: bool
    reauth: bool = False


@dataclass
class Grant:
    attempt: Attempt
    identity: Identity
    reported_nonce: str | None = None  # what the "token" says its nonce is; None means the one that was sent


class FakeIdp:
    def __init__(self) -> None:
        self.attempts: dict[str, Attempt] = {}  # by state
        self.grants: dict[str, Grant] = {}  # by code
        self.completed: list[str] = []
        self.fail_with: str | None = None  # make the next complete() raise IdentityError(this reason)

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        step_up: bool = False,
        reauth: bool = False,
    ) -> str:
        self.attempts[state] = Attempt(state, nonce, code_challenge, redirect_uri, step_up, reauth)
        return f"https://idp.test/authorize?state={state}&nonce={nonce}&step_up={int(step_up)}&reauth={int(reauth)}"

    def attempt_from(self, authorize_url: str) -> Attempt:
        """The attempt a browser started, found from the address it was sent to."""
        return self.attempts[parse_qs(urlsplit(authorize_url).query)["state"][0]]

    def sign_in(self, authorize_url: str, identity: Identity, *, reported_nonce: str | None = None) -> str:
        """The person authenticates at the provider; the browser is sent back with this code."""
        attempt = self.attempt_from(authorize_url)
        code = "code-" + secrets.token_urlsafe(16)
        self.grants[code] = Grant(attempt, identity, reported_nonce)
        return code

    def complete(self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str) -> Identity:
        if self.fail_with:
            reason, self.fail_with = self.fail_with, None
            raise IdentityError(reason)
        grant = self.grants.pop(code, None)  # single use
        if grant is None:
            raise IdentityError("token_exchange")
        self.completed.append(code)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if challenge != grant.attempt.challenge or redirect_uri != grant.attempt.redirect_uri:
            raise IdentityError("token_exchange")
        if (grant.reported_nonce or grant.attempt.nonce) != nonce:
            raise IdentityError("nonce")
        return grant.identity

    def logout_url(self, *, return_to: str) -> str | None:
        return f"https://idp.test/logout?redirect={return_to}"


def person(subject: str = "kp_alice", email: str = "alice@example.test", **changes) -> Identity:
    return replace(Identity(subject=subject, email=email, email_verified=True, name="A Person"), **changes)
