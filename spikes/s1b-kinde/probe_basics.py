"""S1b probe 1: what the tenant publishes and what the machine-to-machine credential can do. No sign-in needed.

    cd backend
    .venv\\Scripts\\python.exe ..\\spikes\\s1b-kinde\\probe_basics.py

Prints names, counts and statuses only: no secret, token, email address or user id.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kinde  # noqa: E402


def main() -> None:
    env = kinde.load_env()
    base = kinde.issuer_url(env)
    print("== discovery ==")
    meta = kinde.discovery(env)
    for key in (
        "issuer", "authorization_endpoint", "token_endpoint", "userinfo_endpoint", "end_session_endpoint",
        "jwks_uri", "revocation_endpoint", "introspection_endpoint",
    ):  # fmt: skip
        print(f"  {key}: {'present' if key in meta else 'ABSENT'}")
    print(f"  issuer equals the configured domain: {meta['issuer'].rstrip('/') == base}")
    for key in (
        "response_types_supported", "scopes_supported", "claims_supported", "code_challenge_methods_supported",
        "id_token_signing_alg_values_supported", "token_endpoint_auth_methods_supported", "acr_values_supported",
        "prompt_values_supported", "grant_types_supported",
    ):  # fmt: skip
        print(f"  {key}: {meta.get(key, 'not listed')}")

    print("\n== signing keys ==")
    keys = kinde.jwks(meta)
    print(f"  {len(keys)} key(s): " + ", ".join(f"{k.get('kty')}/{k.get('alg')}/{k.get('use')}" for k in keys))

    print("\n== machine-to-machine token ==")
    started = time.perf_counter()
    response = httpx.post(
        meta["token_endpoint"],
        data={
            "grant_type": "client_credentials",
            "client_id": env["KINDE_M2M_CLIENT_ID"],
            "client_secret": env["KINDE_M2M_CLIENT_SECRET"],
            "audience": f"{base}/api",
        },
        timeout=20,
    )
    print(f"  status {response.status_code} in {(time.perf_counter() - started) * 1000:.0f} ms")
    response.raise_for_status()
    token = response.json()
    print(f"  token response fields: {sorted(token)}")
    claims = kinde.b64url_json(token["access_token"].split(".")[1])
    print(f"  access token claims: {sorted(claims)}")
    print(f"  scopes: {claims.get('scp') or claims.get('scope')}")
    print(f"  lifetime: {token.get('expires_in')} s")
    headers = {"Authorization": f"Bearer {token['access_token']}", "Accept": "application/json"}

    print("\n== management API ==")
    listing = httpx.get(f"{base}/api/v1/users", headers=headers, params={"page_size": 5}, timeout=20)
    print(f"  GET /api/v1/users -> {listing.status_code}")
    interesting = {k: v for k, v in listing.headers.items() if "rate" in k.lower() or "retry" in k.lower()}
    print(f"  rate-limit headers on that response: {sorted(interesting) or 'none'}")
    if listing.status_code == 200:
        body = listing.json()
        print(f"  response fields: {sorted(body)}")
        users = body.get("users") or []  # null when the tenant has no users yet
        print(f"  users returned: {len(users)}")
        if users:
            print(f"  fields of one user record: {sorted(users[0])}")

    probe = {
        "profile": {"given_name": "S1b", "family_name": "DeleteMe"},
        "identities": [{"type": "email", "details": {"email": "s1b-delete-me@example.test"}}],
    }
    created = httpx.post(f"{base}/api/v1/user", headers=headers, json=probe, timeout=20)
    print(f"  POST /api/v1/user (a throwaway user) -> {created.status_code}")
    if created.status_code in (200, 201):
        user_id = created.json().get("id")
        print(f"  create response fields: {sorted(created.json())}")
        fetched = httpx.get(f"{base}/api/v1/user", headers=headers, params={"id": user_id}, timeout=20)
        print(f"  GET /api/v1/user?id= -> {fetched.status_code}; fields: {sorted(fetched.json()) if fetched.status_code == 200 else '-'}")
        removed = httpx.delete(f"{base}/api/v1/user", headers=headers, params={"id": user_id}, timeout=20)
        print(f"  DELETE /api/v1/user?id= -> {removed.status_code}")
        again = httpx.get(f"{base}/api/v1/user", headers=headers, params={"id": user_id}, timeout=20)
        print(f"  the deleted user, fetched again -> {again.status_code} (404 or similar means it is gone)")
    else:
        print(f"  create failed; body field names: {sorted(created.json()) if created.headers.get('content-type', '').startswith('application/json') else 'not json'}")


if __name__ == "__main__":
    main()
