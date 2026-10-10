"""What the sign-in flow needs from an identity provider, as an interface (doc 03 section 4, ADR-003).

The flow (`auth/flow.py`) depends only on this, so it is tested with a fake provider and the real one (Kinde, in
`auth/kinde.py`) can be swapped for another that speaks OIDC. An implementation does the two things only it can:
build the address to send the browser to, and, when the browser comes back with a code, turn that code into a
*validated* identity. "Validated" means every check of the OIDC rules has already passed: the token's signature, its
issuer, audience and expiry, and the nonce this attempt sent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Identity:
    subject: str  # the provider's stable id for the person (`sub`)
    email: str
    email_verified: bool
    name: str | None = None
    acr: str | None = None  # "mfa" when the person completed a second factor in this sign-in
    amr: tuple[str, ...] = ()  # the methods used, for example ("email", "otp:sms"): not the same as "mfa happened"
    auth_time: datetime | None = None  # when the person last actually authenticated, by the provider's clock


class IdentityError(Exception):
    """The provider refused, or what it returned could not be trusted. `reason` is a short code for the audit log
    ("token", "nonce", "network"); the message is never shown to the person and never contains a token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class IdentityProvider(Protocol):
    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
        step_up: bool = False,
        reauth: bool = False,
        login_hint: str | None = None,
        create: bool = False,
    ) -> str:
        """Where to send the browser. With `step_up`, ask for a fresh second factor now (`acr_values=mfa` with `max_age=0`,
        which Kinde honours: spike S1b), without ending the session. With `reauth`, ask the person to authenticate again
        now (`prompt=login`, `max_age=0`), whatever second factor they have or lack; the flow still checks the answer's
        `auth_time` and does not take the provider's word that it asked. `login_hint` is an address to pre-fill on the
        provider's page (a sign-up code is tied to one address); it is a convenience, never a check. With `create`, open the
        provider's registration screen instead of its sign-in screen (`prompt=create`, the OpenID Connect registration
        extension): for someone who arrives with a valid sign-up code, so the provider's page need not offer "create an
        account" to everyone who reaches its sign-in screen."""
        ...

    def complete(self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str) -> Identity:
        """Exchange the code (server to server) and validate the ID token against `nonce`. Raises IdentityError."""
        ...

    def logout_url(self, *, return_to: str) -> str | None:
        """Where to send the browser to end the provider's own session too, or None if it has no such page."""
        ...


class IdentityDirectory(Protocol):
    """Removing a person's identity at the provider (the deletion sweep; not sign-in)."""

    def delete_user(self, subject: str) -> bool:
        """Remove it. True when it is gone, including when it was already absent (so a repeat is safe). Raises
        IdentityError with a short reason when it could not be removed, and the caller tries again later."""
        ...
