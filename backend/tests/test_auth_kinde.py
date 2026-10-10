"""The Kinde adapter against a pretend Kinde: every way an ID token can be wrong must be refused (doc 04 GATE: "Callback
validates state, nonce, PKCE, ID-token iss/aud/exp; requires email_verified").

No network: the discovery document, the keys and the token endpoint are served by an `httpx.MockTransport`, and tokens are
signed with a key made for the test. The attacks below are the ones that matter for JWTs: a changed payload, another
signing key, `alg: none`, and an HMAC token "signed" with the public key (algorithm confusion).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tradejournal.auth.idp import IdentityError
from tradejournal.auth.kinde import JWKS_REFETCH_EVERY, KindeProvider

DOMAIN = "https://tenant.kinde.test"
CLIENT_ID = "client-123"
CLIENT_SECRET = "very-secret-value"  # noqa: S105
NONCE = "nonce-abc"
NOW = 1_800_000_000


def make_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY, OTHER_KEY = make_key(), make_key()


def jwk(key: rsa.RSAPrivateKey, kid: str) -> dict:
    document = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**document, "kid": kid, "use": "sig", "alg": "RS256"}


def claims(**changes) -> dict:
    base = {
        "iss": DOMAIN,
        "aud": [CLIENT_ID],
        "sub": "kp_abc123",
        "exp": NOW + 3600,
        "iat": NOW,
        "nonce": NONCE,
        "email": "sam@example.test",
        "email_verified": True,
        "name": "Sam Trader",
        "acr": "mfa",
        "amr": ["email", "otp:sms"],
        "auth_time": NOW - 5,
    }
    return {**base, **changes}


def token(payload: dict | None = None, *, key=KEY, kid="k1", algorithm="RS256", headers=None) -> str:
    return jwt.encode(
        payload if payload is not None else claims(), key, algorithm=algorithm, headers={"kid": kid, **(headers or {})}
    )


class Kinde:
    """A pretend tenant: records what it was asked and serves what it is told to."""

    def __init__(self) -> None:
        self.keys = [jwk(KEY, "k1")]
        self.id_token: str | None = token()
        self.token_status = 200
        self.issuer = DOMAIN
        self.requests: list[httpx.Request] = []
        self.network_down = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.network_down:
            raise httpx.ConnectError("down")
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{DOMAIN}/oauth2/auth",
                    "token_endpoint": f"{DOMAIN}/oauth2/token",
                    "jwks_uri": f"{DOMAIN}/.well-known/jwks",
                    "end_session_endpoint": f"{DOMAIN}/logout",
                },
            )
        if path.endswith("/.well-known/jwks"):
            return httpx.Response(200, json={"keys": self.keys})
        if path.endswith("/oauth2/token"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            body = {"access_token": "opaque", "token_type": "bearer"}
            if self.id_token is not None:
                body["id_token"] = self.id_token
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    def count(self, suffix: str) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith(suffix))


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def world():
    kinde, clock = Kinde(), Clock()
    client = httpx.Client(transport=httpx.MockTransport(kinde.handle))
    provider = KindeProvider(
        domain=DOMAIN, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, client=client, clock=clock
    )
    return provider, kinde, clock


def complete(provider, nonce=NONCE):
    return provider.complete(
        code="the-code", code_verifier="v" * 64, redirect_uri="https://app.test/auth/callback", nonce=nonce
    )


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch):
    """PyJWT checks `exp` and `iat` against the real clock; pin it to the tokens' world."""
    real = datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real.fromtimestamp(NOW, tz or UTC)

    monkeypatch.setattr(jwt.api_jwt, "datetime", Frozen)


# ---- the good case --------------------------------------------------------------------------------------------------


def test_a_valid_token_becomes_an_identity_with_what_matters_about_how_they_signed_in(world):
    provider, _, _ = world
    identity = complete(provider)
    assert (identity.subject, identity.email, identity.email_verified, identity.name) == (
        "kp_abc123",
        "sam@example.test",
        True,
        "Sam Trader",
    )
    assert identity.acr == "mfa" and identity.amr == ("email", "otp:sms")  # `amr` lists methods; it does not say "mfa"
    assert identity.auth_time == datetime.fromtimestamp(NOW - 5, UTC)


def test_the_token_request_carries_the_code_the_verifier_and_the_secret_in_the_body_only(world):
    provider, kinde, _ = world
    complete(provider)
    [request] = [r for r in kinde.requests if r.url.path.endswith("/oauth2/token")]
    form = parse_qs(request.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
    assert (
        form["code_verifier"] == ["v" * 64]
        and form["client_id"] == [CLIENT_ID]
        and form["client_secret"] == [CLIENT_SECRET]
    )
    assert (
        CLIENT_SECRET not in str(request.url) and "authorization" not in request.headers
    )  # never in the address or a header


def test_the_authorization_address_asks_for_pkce_and_openid_and_a_step_up_only_when_told_to(world):
    provider, _, _ = world
    plain = urlsplit(
        provider.authorization_url(state="s", nonce="n", code_challenge="c", redirect_uri="https://app.test/cb")
    )
    query = parse_qs(plain.query)
    assert f"{plain.scheme}://{plain.netloc}{plain.path}" == f"{DOMAIN}/oauth2/auth"
    assert query["response_type"] == ["code"] and query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid email profile"] and query["state"] == ["s"] and query["nonce"] == ["n"]
    assert "acr_values" not in query and "max_age" not in query and CLIENT_SECRET not in plain.query
    stepped = parse_qs(
        urlsplit(
            provider.authorization_url(state="s", nonce="n", code_challenge="c", redirect_uri="x", step_up=True)
        ).query
    )
    assert stepped["acr_values"] == ["mfa"] and stepped["max_age"] == ["0"]
    assert "prompt" not in stepped  # a step-up asks for a second factor, not for the person to sign in again
    asked_again = parse_qs(
        urlsplit(
            provider.authorization_url(state="s", nonce="n", code_challenge="c", redirect_uri="x", reauth=True)
        ).query
    )
    assert asked_again["prompt"] == ["login"] and asked_again["max_age"] == ["0"]
    assert "acr_values" not in asked_again  # whatever factors the person has: the flow checks `auth_time`, not `acr`
    assert provider.logout_url(return_to="https://app.test/") == f"{DOMAIN}/logout?redirect=https%3A%2F%2Fapp.test%2F"


def test_the_registration_screen_is_asked_for_only_for_someone_making_an_account(world):
    provider, _, _ = world

    def query(**kw):
        url = provider.authorization_url(state="s", nonce="n", code_challenge="c", redirect_uri="x", **kw)
        return parse_qs(urlsplit(url).query)

    assert query(create=True)["prompt"] == ["create"]
    assert "prompt" not in query()  # an ordinary sign-in opens the sign-in screen
    assert query(create=True, reauth=True)["prompt"] == ["login"]  # asking someone to prove who they are wins
    assert "prompt" not in query(create=True, step_up=True)  # a step-up is not a sign-up


# ---- every way a token can be wrong ---------------------------------------------------------------------------------


def hs256_with_the_public_key() -> str:
    """The algorithm-confusion attack: an HMAC token whose secret is the public key the issuer publishes."""
    public_pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    b64 = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()  # noqa: E731
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    body = b64(json.dumps(claims()).encode())
    signature = b64(hmac.new(public_pem, f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{signature}"


def unsigned() -> str:
    b64 = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()  # noqa: E731
    return f"{b64(json.dumps({'alg': 'none', 'kid': 'k1'}).encode())}.{b64(json.dumps(claims()).encode())}."


def tampered() -> str:
    head, body, signature = token().split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["sub"] = "kp_attacker"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{head}.{forged}.{signature}"


@pytest.mark.parametrize(
    ("label", "bad_token"),
    [
        ("another key signed it", token(key=OTHER_KEY)),
        ("alg none", unsigned()),
        ("HMAC with the public key (algorithm confusion)", hs256_with_the_public_key()),
        ("payload changed after signing", tampered()),
        ("wrong audience", token(claims(aud=["someone-else"]))),
        ("wrong issuer", token(claims(iss="https://evil.example"))),
        ("expired", token(claims(exp=NOW - 3600))),
        ("issued in the future", token(claims(iat=NOW + 3600))),
        ("no expiry", token({k: v for k, v in claims().items() if k != "exp"})),
        ("no issuer", token({k: v for k, v in claims().items() if k != "iss"})),
        ("no subject", token({k: v for k, v in claims().items() if k != "sub"})),
        ("an unknown key id", token(kid="nobody")),
        ("not a token", "this.is.nonsense"),
    ],
)
def test_a_wrong_token_is_refused_and_the_error_names_no_secret(world, label, bad_token):
    provider, kinde, _ = world
    kinde.id_token = bad_token
    with pytest.raises(IdentityError) as problem:
        complete(provider)
    assert problem.value.reason in ("token", "claims"), label
    assert CLIENT_SECRET not in str(problem.value) and bad_token not in str(problem.value)


def test_a_token_for_another_nonce_is_refused(world):
    provider, kinde, _ = world
    kinde.id_token = token(claims(nonce="not-the-one-we-sent"))
    with pytest.raises(IdentityError, match="nonce"):
        complete(provider)
    kinde.id_token = token({k: v for k, v in claims().items() if k != "nonce"})
    with pytest.raises(IdentityError, match="nonce"):
        complete(provider)


def test_a_token_without_an_email_is_refused_and_an_unverified_one_is_reported_not_trusted(world):
    provider, kinde, _ = world
    kinde.id_token = token({k: v for k, v in claims().items() if k != "email"})
    with pytest.raises(IdentityError, match="claims"):
        complete(provider)
    for unverified in (False, "true", None, 1):
        kinde.id_token = token(claims(email_verified=unverified))
        assert complete(provider).email_verified is False, unverified  # only a real `true` counts


def test_an_odd_amr_or_auth_time_is_ignored_not_trusted(world):
    provider, kinde, _ = world
    kinde.id_token = token(claims(amr="mfa", acr=7, auth_time="yesterday", name=5))
    identity = complete(provider)
    assert identity.amr == () and identity.acr is None and identity.auth_time is None and identity.name is None


# ---- the provider misbehaving -----------------------------------------------------------------------------------------


def test_a_refused_code_a_missing_token_and_a_provider_that_is_down_are_each_an_error(world):
    provider, kinde, _ = world
    kinde.token_status = 400
    with pytest.raises(IdentityError, match="token_exchange"):
        complete(provider)
    kinde.token_status, kinde.id_token = 200, None
    with pytest.raises(IdentityError, match="token_exchange"):
        complete(provider)
    kinde.network_down = True
    with pytest.raises(IdentityError, match="network|discovery"):
        complete(provider)


def test_a_discovery_document_that_names_another_issuer_is_not_trusted(world):
    provider, kinde, _ = world
    kinde.issuer = "https://evil.example"
    with pytest.raises(IdentityError, match="discovery"):
        provider.authorization_url(state="s", nonce="n", code_challenge="c", redirect_uri="x")


def test_the_endpoints_and_keys_are_fetched_once_and_a_rotated_key_is_picked_up_without_being_a_way_to_make_us_poll(
    world,
):
    provider, kinde, clock = world
    complete(provider)
    complete(provider)
    assert kinde.count("openid-configuration") == 1 and kinde.count("/jwks") == 1  # cached
    new_key = make_key()
    kinde.keys = [jwk(KEY, "k1"), jwk(new_key, "k2")]
    kinde.id_token = token(key=new_key, kid="k2")
    with pytest.raises(IdentityError):  # an unknown key id within the minute is refused without another fetch
        complete(provider)
    assert kinde.count("/jwks") == 1
    clock.now += JWKS_REFETCH_EVERY + 1  # a minute later the set is fetched again and the rotated key is found
    assert complete(provider).subject == "kp_abc123" and kinde.count("/jwks") == 2
    kinde.id_token = token(kid="forged")
    for _ in range(5):  # junk tokens cannot make us poll Kinde on every attempt
        with pytest.raises(IdentityError):
            complete(provider)
    assert kinde.count("/jwks") == 2
