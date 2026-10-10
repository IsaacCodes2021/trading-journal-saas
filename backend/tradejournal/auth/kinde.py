"""Kinde as the identity provider (ADR-003; proven against a live tenant in spike S1b, 2026-10-04).

OpenID Connect authorization code flow with PKCE (S256), the client secret sent in the token request body
(`client_secret_post`: the only method the tenant advertises), RS256 ID tokens. The ID token is verified with PyJWT
(a vetted library; the spike's hand-written check was never meant to ship): the algorithm is fixed to RS256 (so neither
`alg: none` nor an HMAC token signed with the public key is ever accepted), the signing key is the one the issuer
publishes for the token's `kid`, and `iss`, `aud`, `exp` and `iat` are required and checked, then the nonce this
attempt sent. Nothing in an error ever contains a token, a code or a secret.
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import jwt

from .idp import Identity, IdentityError

DISCOVERY_TTL = 3600.0  # seconds: the endpoints and keys rarely change, and a rotation is handled by a refetch
JWKS_REFETCH_EVERY = 60.0  # an unknown `kid` refetches the keys at most this often, so junk tokens cannot make us poll
LEEWAY_SECONDS = 60


class KindeProvider:
    def __init__(
        self,
        *,
        domain: str,
        client_id: str,
        client_secret: str,
        client: httpx.Client | None = None,
        clock=time.time,
    ) -> None:
        self._issuer = domain.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = client or httpx.Client(timeout=10.0, follow_redirects=False)
        self._clock = clock
        self._lock = threading.Lock()
        self._discovery: dict | None = None
        self._discovered_at = 0.0
        self._keys: dict[str, object] = {}  # kid -> public key
        self._keys_fetched_at = 0.0

    # ---- discovery and keys ---------------------------------------------------------------------------------------

    def _metadata(self) -> dict:
        with self._lock:
            if self._discovery is None or self._clock() - self._discovered_at > DISCOVERY_TTL:
                try:
                    response = self._http.get(f"{self._issuer}/.well-known/openid-configuration")
                    response.raise_for_status()
                    document = response.json()
                except (httpx.HTTPError, ValueError):
                    raise IdentityError("discovery") from None
                if not isinstance(document, dict) or str(document.get("issuer", "")).rstrip("/") != self._issuer:
                    raise IdentityError("discovery")  # a document that names another issuer is not ours to trust
                self._discovery, self._discovered_at = document, self._clock()
            return self._discovery

    def _key_for(self, kid: str) -> object:
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            if self._clock() - self._keys_fetched_at < JWKS_REFETCH_EVERY and self._keys:
                raise IdentityError("token")  # an unknown key, and the set was fetched a moment ago
        metadata = self._metadata()
        try:
            response = self._http.get(metadata["jwks_uri"])
            response.raise_for_status()
            published = response.json().get("keys", [])
        except (httpx.HTTPError, ValueError, KeyError, AttributeError):
            raise IdentityError("jwks") from None
        keys: dict[str, object] = {}
        for jwk in published:
            if isinstance(jwk, dict) and jwk.get("kty") == "RSA" and jwk.get("kid") and jwk.get("use", "sig") == "sig":
                try:
                    keys[jwk["kid"]] = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
                except (jwt.PyJWTError, ValueError):
                    continue
        with self._lock:
            self._keys, self._keys_fetched_at = keys, self._clock()
        if kid not in keys:
            raise IdentityError("token")
        return keys[kid]

    # ---- the interface the flow uses ------------------------------------------------------------------------------

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
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if step_up:
            params.update({"acr_values": "mfa", "max_age": "0"})
        if reauth:
            params.update({"prompt": "login", "max_age": "0"})
        elif create and not step_up:
            params["prompt"] = "create"
        if login_hint:
            params["login_hint"] = login_hint
        return f"{self._metadata()['authorization_endpoint']}?{urlencode(params)}"

    def complete(self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str) -> Identity:
        metadata = self._metadata()
        try:
            response = self._http.post(
                metadata["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": code_verifier,
                },
            )
        except httpx.HTTPError:
            raise IdentityError("network") from None
        if response.status_code != 200:
            raise IdentityError("token_exchange")
        try:
            id_token = response.json()["id_token"]
        except (ValueError, KeyError, TypeError):
            raise IdentityError("token_exchange") from None
        return self._identity(id_token, nonce)

    def _identity(self, id_token: str, nonce: str) -> Identity:
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise IdentityError("token")
            claims = jwt.decode(
                id_token,
                key=self._key_for(header["kid"]),
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except IdentityError:
            raise
        except jwt.PyJWTError:
            raise IdentityError("token") from None
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(
            claims["nonce"].encode(), nonce.encode()
        ):
            raise IdentityError("nonce")
        subject, email = claims.get("sub"), claims.get("email")
        if not isinstance(subject, str) or not subject or not isinstance(email, str) or not email:
            raise IdentityError("claims")
        amr = claims.get("amr")
        auth_time = claims.get("auth_time")
        return Identity(
            subject=subject,
            email=email,
            email_verified=claims.get("email_verified") is True,
            name=claims["name"] if isinstance(claims.get("name"), str) else None,
            acr=claims["acr"] if isinstance(claims.get("acr"), str) else None,
            amr=tuple(a for a in amr if isinstance(a, str)) if isinstance(amr, list) else (),
            auth_time=datetime.fromtimestamp(auth_time, UTC) if isinstance(auth_time, int | float) else None,
        )

    def logout_url(self, *, return_to: str) -> str | None:
        endpoint = self._metadata().get("end_session_endpoint")
        return f"{endpoint}?{urlencode({'redirect': return_to})}" if isinstance(endpoint, str) else None
