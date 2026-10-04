"""S1b probe 2: a real OIDC login against the Kinde tenant, validated by hand. Throwaway.

    cd backend
    $env:S1B_REPORT_DIR = "<a folder outside the repository>"
    .venv\\Scripts\\python.exe -m uvicorn app:app --app-dir ..\\spikes\\s1b-kinde --host 127.0.0.1 --port 8000

Open http://localhost:8000/ and sign in with the throwaway test user. Each run appends a report (claim names, a few
claim values, every check, every negative control) to s1b_runs.jsonl in S1B_REPORT_DIR. No token, code or secret is
written anywhere, and the page never shows one.

Login variants are query parameters on /login: acr_values, max_age, prompt, label. The spike's question is which of
them the live service honours, not which ones the documentation describes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from html import escape
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kinde  # noqa: E402

ENV = kinde.load_env()
BASE = kinde.issuer_url(ENV)
META = kinde.discovery(ENV)
KEYS = kinde.jwks(META)
REDIRECT = "http://localhost:8000/api/v1/auth/callback"
HOME = "http://localhost:8000/"
REPORT = Path(os.environ.get("S1B_REPORT_DIR", ".")) / "s1b_runs.jsonl"

PENDING: dict[str, dict] = {}  # state -> what this login attempt needs when it comes back
SESSIONS: dict[str, dict] = {}  # our own session id -> {sub, mfa_at, created}

app = FastAPI()


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>S1b</title><body style='font-family:system-ui;max-width:60rem;"
        f"margin:2rem auto;padding:0 1rem'><h1>{escape(title)}</h1>{body}</body>"
    )


@app.get("/")
def home(request: Request):
    session = SESSIONS.get(request.cookies.get("s1b_session", ""))
    state = "signed in to this spike" if session else "not signed in to this spike"
    links = [
        ("/login?label=plain", "1. Plain login"),
        ("/login?label=acr_mfa&acr_values=mfa", "2. acr_values=mfa"),
        ("/login?label=max_age_0&max_age=0", "3. max_age=0"),
        ("/login?label=prompt_login&prompt=login", "4. prompt=login"),
        ("/login?label=stepup&acr_values=mfa&max_age=0", "5. acr_values=mfa and max_age=0 (the step-up request)"),
        ("/logout", "Sign out of Kinde"),
    ]
    items = "".join(f"<li><a href='{href}'>{escape(text)}</a></li>" for href, text in links)
    return page("Spike S1b: Kinde login", f"<p>{state}</p><ul>{items}</ul>")


@app.get("/login")
def login(label: str = "plain", acr_values: str | None = None, max_age: str | None = None, prompt: str | None = None):
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    params = {
        "response_type": "code",
        "client_id": ENV["KINDE_CLIENT_ID"],
        "redirect_uri": REDIRECT,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    sent = {k: v for k, v in (("acr_values", acr_values), ("max_age", max_age), ("prompt", prompt)) if v}
    params.update(sent)
    PENDING[state] = {"verifier": verifier, "nonce": nonce, "label": label, "sent": sent, "started": time.time()}
    return RedirectResponse(f"{META['authorization_endpoint']}?{urlencode(params)}")


@app.get("/api/v1/auth/callback")
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    pending = PENDING.pop(state or "", None)
    if error:
        return page("Sign-in returned an error", f"<pre>{escape(error)}\n{escape(error_description or '')}</pre><p><a href='/'>back</a></p>")
    if pending is None or not code:
        return page("Rejected", "<p>Unknown or reused state, or no code. The attempt is ignored.</p>")

    report: dict = {"label": pending["label"], "sent": pending["sent"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    started = time.perf_counter()
    exchange = httpx.post(
        META["token_endpoint"],
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": ENV["KINDE_CLIENT_ID"],
            "client_secret": ENV["KINDE_CLIENT_SECRET"],
            "code_verifier": pending["verifier"],
        },
        timeout=20,
    )
    report["token_exchange"] = {"status": exchange.status_code, "ms": round((time.perf_counter() - started) * 1000)}
    if exchange.status_code != 200:
        report["token_exchange"]["body_fields"] = sorted(exchange.json()) if exchange.headers.get("content-type", "").startswith("application/json") else "not json"
        _save(report)
        return page("Token exchange failed", f"<pre>{escape(json.dumps(report, indent=2))}</pre>")

    tokens = exchange.json()
    report["token_response_fields"] = sorted(tokens)
    id_token = tokens["id_token"]
    claims = kinde.b64url_json(id_token.split(".")[1])
    header = kinde.b64url_json(id_token.split(".")[0])
    keys = KEYS
    if not any(k.get("kid") == header.get("kid") for k in keys):  # a rotated key: fetch the set again, once
        keys = kinde.jwks(META)
    ok, why = kinde.verify_rs256(id_token, keys)
    report["id_token"] = {
        "header": {k: header.get(k) for k in ("alg", "typ", "kid")},
        "signature": why,
        "claim_problems": kinde.check_claims(claims, issuer=META["issuer"], audience=ENV["KINDE_CLIENT_ID"], nonce=pending["nonce"]),
        "claim_names": sorted(claims),
        "email_verified": claims.get("email_verified", "ABSENT"),
        "acr": claims.get("acr", "ABSENT"),
        "amr": claims.get("amr", "ABSENT"),
        "auth_time": claims.get("auth_time", "ABSENT"),
        "auth_age_s": round(time.time() - claims["auth_time"]) if isinstance(claims.get("auth_time"), (int, float)) else "n/a",
        "lifetime_s": claims.get("exp", 0) - claims.get("iat", 0),
    }
    report["negative_controls"] = {
        "tampered_claim_rejected": not kinde.verify_rs256(kinde.tamper(id_token), keys)[0],
        "alg_none_rejected": not kinde.verify_rs256(kinde.unsigned(id_token), keys)[0],
        "wrong_audience_flagged": bool(kinde.check_claims(claims, issuer=META["issuer"], audience="someone-else", nonce=pending["nonce"])),
        "wrong_nonce_flagged": bool(kinde.check_claims(claims, issuer=META["issuer"], audience=ENV["KINDE_CLIENT_ID"], nonce="not-the-nonce")),
        "expired_flagged": bool(kinde.check_claims({**claims, "exp": time.time() - 3600}, issuer=META["issuer"], audience=ENV["KINDE_CLIENT_ID"], nonce=None)),
    }
    access = tokens.get("access_token", "")
    if access.count(".") == 2:
        access_claims = kinde.b64url_json(access.split(".")[1])
        report["access_token"] = {"claim_names": sorted(access_claims), "aud": access_claims.get("aud")}
    info = httpx.get(META["userinfo_endpoint"], headers={"Authorization": f"Bearer {access}"}, timeout=20)
    report["userinfo"] = {"status": info.status_code, "fields": sorted(info.json()) if info.status_code == 200 else "-"}

    # Our own session, as the design has it: opaque id in a cookie, the IdP's MFA time kept beside it.
    existing_id = request.cookies.get("s1b_session", "")
    existing = SESSIONS.get(existing_id)
    same_user = bool(existing and existing["sub"] == claims.get("sub"))
    session_id = existing_id if same_user else secrets.token_urlsafe(32)
    SESSIONS[session_id] = {
        "sub": claims.get("sub"),
        "mfa_at": claims.get("auth_time") if ("mfa" in (claims.get("amr") or []) or claims.get("acr") == "mfa") else (existing or {}).get("mfa_at"),
        "created": (existing or {}).get("created", time.time()) if same_user else time.time(),
    }
    report["session"] = {"kept_existing_session_id": same_user, "mfa_at_recorded": SESSIONS[session_id]["mfa_at"] is not None}
    report["sub_looks_like"] = str(claims.get("sub", ""))[:3] + "..."
    _save(report)

    response = page(
        f"Run recorded: {pending['label']}",
        f"<pre>{escape(json.dumps(report, indent=2))}</pre><p><a href='/'>back to the list</a></p>",
    )
    response.set_cookie("s1b_session", session_id, httponly=True, samesite="lax", path="/")
    return response


@app.get("/logout")
def logout():
    params = {"redirect": HOME, "post_logout_redirect_uri": HOME, "client_id": ENV["KINDE_CLIENT_ID"]}
    return RedirectResponse(f"{META['end_session_endpoint']}?{urlencode(params)}")


def _save(report: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(report) + "\n")
