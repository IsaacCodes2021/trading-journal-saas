"""Removing a person's identity at Kinde, through its Management API (spike S1b proved the calls on a live tenant).

This is not sign-in: it uses a separate machine-to-machine client (`client_credentials`, scope to delete users), and it is
only ever used by the deletion sweep. The adapter does two things and is careful about both:

- `delete_user` is **idempotent**: a person who is already gone counts as removed, so a sweep that failed after Kinde had
  acted (or ran twice) finishes instead of failing forever. S1b: a deleted user answers 400 to a lookup, not 404, so both mean
  "gone"; any other answer is an error and the removal is tried again later.
- It never puts a token, a client secret or a provider message in an error or a log: only a short `reason` code.
"""

from __future__ import annotations

import time
from urllib.parse import quote

import httpx

from .idp import IdentityError


class KindeManagement:
    def __init__(
        self,
        *,
        domain: str,
        client_id: str,
        client_secret: str,
        http: httpx.Client | None = None,
        monotonic=time.monotonic,
    ) -> None:
        self._domain = domain.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx.Client(timeout=10.0)
        self._monotonic = monotonic
        self._token: tuple[str, float] | None = None

    def _bearer(self) -> str:
        if self._token and self._monotonic() < self._token[1]:
            return self._token[0]
        try:
            response = self._http.post(
                f"{self._domain}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "audience": f"{self._domain}/api",
                },
            )
        except httpx.HTTPError:
            raise IdentityError("management_network") from None
        if response.status_code != 200:
            raise IdentityError("management_token")
        try:
            body = response.json()
            token, lifetime = str(body["access_token"]), float(body.get("expires_in", 60))
        except (ValueError, KeyError, TypeError):
            raise IdentityError("management_token") from None
        self._token = (
            token,
            self._monotonic() + max(0.0, lifetime - 60),
        )  # a minute early: never hand out a dying token
        return token

    def delete_user(self, subject: str) -> bool:
        """Remove the identity. True when it is gone, whether this call removed it or it was already absent."""
        if not subject or len(subject) > 200:
            raise IdentityError("management_subject")
        headers = {"Authorization": f"Bearer {self._bearer()}"}
        url = f"{self._domain}/api/v1/user?id={quote(subject, safe='')}"
        try:
            lookup = self._http.get(url, headers=headers)
            if lookup.status_code in (400, 404):
                return True  # already gone
            if lookup.status_code != 200:
                raise IdentityError(f"management_lookup_{lookup.status_code}")
            removal = self._http.delete(url, headers=headers)
        except httpx.HTTPError:
            raise IdentityError("management_network") from None
        if removal.status_code in (200, 204, 404):
            return True
        raise IdentityError(f"management_delete_{removal.status_code}")
