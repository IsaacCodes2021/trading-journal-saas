"""Shared helpers for spike S1b. Throwaway: nothing here is meant to be merged.

Reads backend/.env.kinde (gitignored). Never prints a secret, a token or an authorization code.

Signature checking is plain RSA arithmetic (RS256 is PKCS#1 v1.5 over SHA-256), so the spike needs no new
dependency. It verifies public-key signatures only, so there is no timing concern; production should use a vetted
library (pyjwt or authlib) and the spike's notes say which questions that choice still has to answer.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx

ENV_FILE = Path(__file__).resolve().parents[2] / "backend" / ".env.kinde"
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")  # DigestInfo for SHA-256


def load_env() -> dict[str, str]:
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def issuer_url(env: dict[str, str]) -> str:
    domain = env["KINDE_DOMAIN"].strip().rstrip("/")
    return domain if domain.startswith("http") else f"https://{domain}"


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64url_json(text: str) -> dict:
    return json.loads(b64url_decode(text))


def discovery(env: dict[str, str]) -> dict:
    response = httpx.get(f"{issuer_url(env)}/.well-known/openid-configuration", timeout=15)
    response.raise_for_status()
    return response.json()


def jwks(meta: dict) -> list[dict]:
    response = httpx.get(meta["jwks_uri"], timeout=15)
    response.raise_for_status()
    return response.json()["keys"]


def verify_rs256(token: str, keys: list[dict]) -> tuple[bool, str]:
    """(valid, reason). The algorithm must be RS256 and the key must be one the issuer published."""
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = b64url_json(header_b64)
    except (ValueError, json.JSONDecodeError):
        return False, "not a three-part JWT"
    if header.get("alg") != "RS256":
        return False, f"algorithm {header.get('alg')!r} is not RS256"
    key = next((k for k in keys if k.get("kid") == header.get("kid") and k.get("kty") == "RSA"), None)
    if key is None:
        return False, f"no published key with kid {header.get('kid')!r}"
    n = int.from_bytes(b64url_decode(key["n"]), "big")
    e = int.from_bytes(b64url_decode(key["e"]), "big")
    signature = b64url_decode(signature_b64)
    size = (n.bit_length() + 7) // 8
    if len(signature) != size or int.from_bytes(signature, "big") >= n:
        return False, "signature has the wrong length"
    recovered = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    digest = hashlib.sha256(f"{header_b64}.{payload_b64}".encode()).digest()
    padding = b"\xff" * (size - len(SHA256_PREFIX) - len(digest) - 3)
    expected = b"\x00\x01" + padding + b"\x00" + SHA256_PREFIX + digest
    return (True, "signature valid") if hmac.compare_digest(recovered, expected) else (False, "signature mismatch")


def check_claims(claims: dict, *, issuer: str, audience: str, nonce: str | None, leeway: int = 60) -> list[str]:
    """What is wrong with the claims, as a list of reasons (empty means every check passed)."""
    now = time.time()
    problems = []
    if claims.get("iss") != issuer:
        problems.append(f"iss is {claims.get('iss')!r}, expected {issuer!r}")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if audience not in audiences:
        problems.append("aud does not contain this client")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - leeway:
        problems.append("exp missing or in the past")
    if not isinstance(claims.get("iat"), (int, float)) or claims["iat"] > now + leeway:
        problems.append("iat missing or in the future")
    if nonce is not None and claims.get("nonce") != nonce:
        problems.append("nonce does not match the one sent")
    return problems


def tamper(token: str) -> str:
    """The same token with one claim changed and the signature untouched: it must not verify."""
    header_b64, payload_b64, signature_b64 = token.split(".")
    payload = b64url_json(payload_b64)
    payload["sub"] = "kp_attacker"
    changed = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"{header_b64}.{changed}.{signature_b64}"


def unsigned(token: str) -> str:
    """The same claims with alg set to none and no signature: it must not be accepted."""
    header_b64, payload_b64, _ = token.split(".")
    header = b64url_json(header_b64)
    header["alg"] = "none"
    changed = base64.urlsafe_b64encode(json.dumps(header, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return f"{changed}.{payload_b64}."
