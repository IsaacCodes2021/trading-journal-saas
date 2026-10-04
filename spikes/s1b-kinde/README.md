# Spike S1b: Kinde as the identity provider (throwaway, never merged)

Run on 2026-10-04 against a live Kinde tenant on the **free plan** (`northfieldlabs.kinde.com`). The conclusions are in `docs/design/06-decisions.md` (ADR-003 amendment) and `docs/design/08-spike-prep-research.md`; this file holds the evidence behind them. No token, code, secret, email address or phone number is recorded anywhere in this folder.

## Files

| File | What it does |
|---|---|
| `kinde.py` | Reads `backend/.env.kinde` (gitignored); fetches the discovery document and signing keys; checks an RS256 signature with plain RSA arithmetic (no new dependency); checks `iss`, `aud`, `exp`, `iat`, `nonce`; builds the tampered and `alg: none` negative controls |
| `probe_basics.py` | No sign-in needed: discovery document, signing keys, machine-to-machine token, and the management API (list, create, get, delete a user) |
| `app.py` | A real OIDC (OpenID Connect) login with PKCE (the code-theft protection) against the tenant, with the login variants `acr_values`, `max_age` and `prompt` as query parameters; appends one report per run to `s1b_runs.jsonl` (claim names, a few claim values, every check) |

Run: `cd backend; $env:S1B_REPORT_DIR = "<folder outside the repo>"; .venv\Scripts\python.exe -m uvicorn app:app --app-dir ..\spikes\s1b-kinde --host 127.0.0.1 --port 8000`, then open `http://localhost:8000/`. `http://localhost:8000/api/v1/auth/callback` must be an allowed callback URL on the Kinde application.

## Results

### Discovery document (no sign-in)

- Present: issuer (equals the configured domain), authorization, token, userinfo, end-session, JWKS (the published signing keys), revocation and introspection endpoints.
- Authorization code flow only; **PKCE `S256` only**; **RS256 only**; the token endpoint accepts `client_secret_post` only (no `client_secret_basic`).
- `claims_supported` lists `acr`, `amr` and `auth_time`; `acr_values_supported` lists `mfa` and `phr`; `prompt_values_supported` and `grant_types_supported` are not listed.
- One signing key.

### Plain login (run 1, a real sign-in)

- Token exchange 200 in about 500 ms. ID token lifetime 3,600 s.
- Signature valid against the published key; no problems with `iss`, `aud`, `exp`, `iat` or `nonce`. All five negative controls behaved (tampered claim, `alg: none`, wrong audience, wrong nonce, expired).
- `email_verified: true`.
- **`acr: "mfa"`, `amr: ["email", "otp:sms"]` and `auth_time` are present on an ordinary login** (this user had a text-message second factor). Note that `amr` lists the *methods used*, not the word `mfa`: **the rule "MFA happened" must be `acr == "mfa"`**, not `"mfa" in amr`.
- Other ID token claims: `at_hash`, `azp`, `email`, `family_name`, `given_name`, `iat`, `jti`, `name`, `nonce`, `org_codes`, `picture`, `rat`, `sub` (looks like `kp_...`), `updated_at`.
- The access token is also a JWT (claims include `permissions` and `org_code`; empty `aud`); we do not need it, because sessions are our own.
- The userinfo endpoint answers 200 with `email`, `email_verified`, `family_name`, `given_name`, `id`, `name`, `picture`, `preferred_username`, `sub`, `updated_at`.

### Step-up (run 5: `acr_values=mfa` and `max_age=0`, with an existing Kinde session)

- Kinde **showed the MFA code prompt again** (a fresh text-message code) although the user was already signed in.
- After the code: signature valid, no claim problems, `acr: "mfa"`, `amr: ["email","otp:sms"]`, **`auth_time` moved forward by 1,044 s to the moment of the code** (token age 1 s), same `sub`, and our own session id was kept (`kept_existing_session_id: true`) with the MFA time updated. **This is the step-up the admin design (ADR-012) needs, and it works as the open Kinde docs pull request (PR 811) describes.**

### Management API (machine-to-machine client credentials)

- Token request 200 in about 500 ms; the token lives 86,399 s (about 24 hours); scopes `create:users delete:users read:users update:users`.
- `GET /api/v1/users` lists users (a `next_token` is returned even when the next page is empty); `POST /api/v1/user` creates; `GET /api/v1/user?id=` fetches; `DELETE /api/v1/user?id=` returns 200 and a later fetch returns **400** (not 404). No rate-limit headers were seen.

### User export (the owner's dashboard, Settings > Business > Details > Export data)

- A zip of NDJSON files: `organizations.ndjson` and `users.ndjson` (and `access_requests.ndjson` per the docs). A user record has `id`, `email`, `first_name`, `last_name`, `created_on`, `email_verified`, `identities` (type, identity, `is_verified`, `preprovisioned_identity`), `properties` and `organizations`. This user had no password hash, because the first factor was an emailed code. **Manual, owner-only; not an API.** It proves an exit path exists; it cannot serve a user's own "export my data" request.

### Not proven (to re-test when the admin and sign-in screens are built)

1. Runs 2 to 4 (`acr_values=mfa` alone, `max_age=0` alone, `prompt=login` alone) were not completed, so **which parameter forces the challenge is not isolated**. The combined step-up request is what the design uses and it works. All five variants were accepted by Kinde (each reached the sign-in page without an error).
2. Only a **text-message** factor was exercised. The `amr` value for an authenticator app (TOTP, time-based one-time password) is unknown, so "require an authenticator app for the admin and refuse text messages" is not yet provable. Text messages are also the weaker factor (SIM-swap) and are metered (the free plan includes 10).
3. Google sign-in, password sign-in, end-session (logout), and what Kinde does when a user with **no** MFA enrolled hits a step-up request.
4. Free-plan limits beyond the pricing page (10,500 MAU, custom domain included): sending Kinde's emails from our own domain, rate limits, and the behaviour above 10,500 MAU.
5. The hand-written signature check is for the spike only. Production should use a vetted library (PyJWT or Authlib), including `at_hash` handling and key rotation (the spike refetches the key set once when it sees an unknown `kid`).

## Cleanup

The test user was created through Kinde's hosted sign-up (the owner's own email) and must be deleted from the tenant (dashboard: Users, or the management API) when the spike is over. The Kinde export in the working folder `test kinde export/` contains that user's data and is excluded from git through `.git/info/exclude`.
