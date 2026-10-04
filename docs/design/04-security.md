# 04. Security

Target: **OWASP ASVS Level 2** as the checklist (a checklist, not a certification). The design principle is *defense in depth where the cost of a miss is a cross-user breach*: tenant isolation has three independent layers (doc 03).

*Revised 2026-10-02 for ADR-010 to ADR-012. The attack surface is now a React SPA plus a JSON API plus an operator back end. The websocket-hijack threat drops out; CSRF, CORS, npm supply chain, credential handling in the SPA, a strict CSP and admin abuse come in.*

## 1. What we protect

| Asset | Sensitivity | Why |
|---|---|---|
| Trades, P&L, account sizes, firms, payouts | **High** | Personal financial data |
| Screenshots | **High** | Platform screenshots often show account numbers, balances, firm names |
| Notes | Medium to High | Psychological and personal content |
| Email, name | Medium | PII |
| Sessions | **Critical** | A session is the user |
| Subscription state | Medium | Business integrity |
| Operator credentials (GitHub, host, Cloudflare, DB owner role, R2 keys) | **Critical** | Total compromise |
| Admin capability (`is_admin`, plan overrides, grant and discount codes) | **Critical** | Free plans, account suspension, and the audit trail itself |
| Front-end bundle and its npm dependencies | **High** | Code that runs inside every user's authenticated session |

We hold **no** broker credentials, API keys or card data by design (P1, and the payment provider hosts checkout).

## 2. Threat model

| # | Threat | Primary mitigations | Verified by |
|---|---|---|---|
| T1 | **Cross-tenant access** (missing filter, IDOR, SQL injection, file URL guessing) | RLS (forced) + composite FKs + default `user_id`; parameterized SQL only; `/files/{id}` authorizes under RLS; no public URLs | Schema lint test; two-tenant suite; manual review of every raw SQL string |
| T2 | **Account takeover** (credential stuffing, phishing, session theft) | Hosted IdP: breached-password check, rate limiting, MFA; opaque revocable sessions; `__Host-` HttpOnly Secure same-origin cookie, nothing readable by JavaScript; re-auth for email change and deletion; "new login" email | IdP configuration checklist; session tests |
| T3 | **Malicious upload** (image bomb, polyglot, huge file, CSV bomb) | Size limits at the edge, the route and the streaming reader; Pillow decode with pixel cap and re-encode; CSV size and row caps; parse in the worker thread with a timeout | Fuzz fixtures; oversize tests |
| T4 | **XSS** (stored via notes; via names rendered as HTML) | React escapes text by default; **no `dangerouslySetInnerHTML` outside one `SafeHtml` component** (DOMPurify with the same allow-list as the server; ESLint `react/no-danger` plus a CI grep); allow-list sanitize notes on write (`nh3`) **and** on render; strict CSP; `X-Content-Type-Options`; a chart `formatter` that returns HTML escapes user-derived text (`echarts.format.encodeHTML`) | XSS payload corpus against the API and the SPA; CSP header test |
| T5 | **Denial of service** (heavy imports, request floods, expensive dashboards) | Per-user quotas and rate limits; import concurrency limit; Cloudflare; request timeouts; pagination | Load test in M11 |
| T6 | **Secrets leak** (repo, logs, CI, Docker layers) | `.gitignore`, gitleaks in CI, env-var secrets, Sentry and log scrubbing, no secrets in image, separate keys per environment, rotation runbook | CI; periodic review |
| T7 | **Supply chain** (malicious or vulnerable dependency in Python **or npm**, compromised GitHub Action or account). **npm is the largest new exposure**: a front-end package runs inside every user's authenticated session | Lock files with hashes (`uv.lock`, `pnpm-lock.yaml`); `pnpm install --frozen-lockfile`; **dependency lifecycle scripts blocked** (pnpm's default since v10; any allow-listed script reviewed); `minimumReleaseAge` so a freshly published version is not installed at once ([pnpm settings](https://pnpm.io/10.x/settings)); Dependabot for uv, npm, Actions and Docker; `pip-audit` and `pnpm audit`; Actions pinned by commit SHA; hardware-key 2FA on GitHub and the host; few dependencies, each reviewed when first added; a strict CSP limits where a compromised package could send data | CI; review of every new package |
| T8 | **Operator error** (destructive command in prod, restore of the wrong data) | Separate prod credentials not stored on the dev machine by default; PITR + separate backup bucket; expand-contract migrations; rehearse on staging; admin UI has no data access | Restore drill |
| T9 | **Webhook forgery** | Signature verification; idempotent event table; reconcile job | Unit tests with bad signatures |
| T10 | **Data loss** (bad migration, deletion bug) | PITR, nightly dump to a separate bucket, soft-delete grace, R2 versioning, orphan job that reports before it deletes | Restore drill |
| T11 | **Third-party data exposure** (error tracker, logs, analytics) | `send_default_pii=False`; scrub request bodies; no session replay in the app; subprocessor list; DPAs | Review of Sentry payloads |
| T12 | **OAuth flow attacks** (CSRF, open redirect, code injection) | `state`, `nonce`, PKCE; exact redirect-URI allow-list; ID token validation | Auth flow tests |
| T13 | **Clickjacking, MIME sniffing, downgrade** | `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, HSTS with preload once stable, `Referrer-Policy: strict-origin-when-cross-origin` | Header test in CI |
| T14 | **CSRF** (a malicious site makes the victim's browser call the API with the session cookie). *This row replaces the websocket-hijack threat, which disappears with NiceGUI* | Four layers (ADR-011): `SameSite=Lax`; `Origin` / `Sec-Fetch-Site` validated; per-session `X-CSRF-Token` header; JSON or multipart bodies only. Logout is `POST` | CSRF test for every unsafe route, generated from the OpenAPI document |
| T15 | **CORS misconfiguration** | Same origin, so **no CORS middleware at all**; if one is ever added, never a wildcard origin together with credentials | Test that cross-origin requests get no `Access-Control-Allow-*` headers |
| T16 | **Credential or secret exposure in the SPA** (tokens in storage, secrets in the bundle, public source maps) | The session lives only in an `HttpOnly` cookie; nothing sensitive in `localStorage`; `VITE_*` variables are public by design and hold no secrets (CI grep); source maps go to Sentry and are not served; strict CSP | CI grep; bundle inspection before beta |
| T17 | **Admin abuse or a compromised admin** | `is_admin` set only by the owner-level CLI (column-level grants); MFA within 15 minutes and a 15-minute idle timeout; admin DB role with **no SELECT on tenant tables**; every mutation and user-detail view audited in an append-only log; no impersonation; optionally an access gateway in front of `/admin` (availability and limits unverified) | Admin-role grant test in CI; audit review |
| T18 | **Code and plan abuse** (guessing or replaying grant and invite codes, misuse of overrides) | Codes hashed at rest and rate-limited with one identical error for every failure; one redemption per user; overrides are admin-only, need a reason and are audited; the effective plan is computed, not stored | Unit and rate-limit tests |

## 3. Findings in today's code

Severity is for the *hosted* case. The app is safe today only because it binds to `127.0.0.1` (`ui/app.py:45`). **Do not expose the current build to any network.**

| # | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| S1 | **Blocker** | No authentication or per-user data at all | whole app; `db.py` schema | Docs 03 (tenancy + auth) |
| S2 | **Blocker** | Screenshots served from an unauthenticated static route; protection is only the unguessability of a UUID filename | `ui/app.py:39` | Private bucket, `/files/{id}`, presigned redirect |
| S3 | **Blocker** | Process-wide filter state shared by all visitors | `ui/state.py:70` | Filters move to URL search params and `localStorage`; the API keeps no per-user or per-tab state (doc 03 section 4). Disappears with `ui/` |
| S4 | **High** | Global UNIQUE constraints leak existence of other users' data and cause collisions | `db.py` (`accounts.name`, `tag_groups.name`, `fill_pairs`) | Per-user uniqueness |
| S5 | **High** | Startup job deletes every file not referenced in the DB; destructive on shared storage or after a restore | `ui/app.py:38`; `journal.py:340` | Reporting-first, delayed, per-user job (doc 03 section 6) |
| S6 | **High** | Notes saved as raw editor HTML; `clean_notes` only checks emptiness | `journal.py:241`; `trade_detail.py:232` | Sanitize with an allow-list library (for example `nh3`): allow `p, br, b, strong, i, em, u, s, ul, ol, li, blockquote, h1-h3, a[href http/https/mailto]` with `rel="noopener noreferrer"`; drop everything else, including `style`, event handlers, `javascript:` and `data:` URLs. In the SPA, read-only HTML renders only through `SafeHtml` (DOMPurify, same list) |
| S7 | **High** | No upload size limits at the widget or proxy; the 25 MB check runs after the whole file is in memory; whole CSV parsed by pandas with no row cap | `trade_detail.py:269`, `pages/imports.py:236`, `journal.py:286`, `parser.py:92` | Limits at every layer (edge, route, streaming read); row cap (suggest 50,000). The `ui.upload` call sites disappear with `ui/`; the missing limits do not |
| S8 | **Medium** | Migrations run on every connection open | `db.py:247` | Release-step runner with advisory lock |
| S9 | **Medium** | Non-reproducible dependencies (floor versions); test tools in runtime requirements | `requirements.txt` | `uv` lock file with hashes; dev group |
| S10 | **Medium** | Server clock used for "today" | `ui/state.py:55`, `settings_page.py:340,393`, `widgets.py:131` | The server resolves date presets in the user's timezone and the SPA formats in it. The code disappears with `ui/`; the rule carries over |
| S11 | **Medium** | CSV timestamps interpreted in a hardcoded default timezone | `settings.py:12` | Onboarding confirmation (doc 03 section 7) |
| S12 | **Low** | No structured logging, error tracking or request ids | `ui/app.py` prints | Logging + Sentry |
| S13 | **Low** | Stray file `backend/=ro` | repo | Delete |
| S14 | **Info** | *Superseded by ADR-010.* NiceGUI's runtime template compiler and inline scripts stood in the way of a strict CSP. A Vite SPA does not | `ui/app.py:14` | A strict CSP (section 4); S6 remains the primary control for stored XSS |

S3, S10 and S14 are properties of the NiceGUI UI and disappear when `ui/` is deleted; the rule behind S3 and S10 carries over (see the Fix column). Every other row belongs to the data layer or the process and survives the pivot.

**Credit where due.** Already good: image uploads are content-sniffed and **SVG is excluded** (it can carry script); files get random names; money is exact `Decimal`; SQL is parameterized (the dynamic `IN (...)` lists in `queries.py` build placeholders, not values); the one HTML cell renderer escapes tag names and styles (`trades.py:58`; React gives the same protection by default). The remaining work is a small number of well-understood gaps, not a rewrite of the domain layer.

## 4. Controls checklist (launch gate)

Items marked **GATE** must be done before any real user data is stored. Others must be done before public launch.

### Authentication and sessions
- [ ] **GATE** Hosted IdP configured: email verification required, breached-password check on, MFA available, brute-force protection on
- [ ] **GATE** Callback validates `state`, `nonce`, PKCE, ID-token `iss/aud/exp`; requires `email_verified`; exact redirect-URI allow-list
- [ ] **GATE** Cookie flags and `__Host-` prefix; session rotation; server-side revocation; idle and absolute timeouts
- [ ] **GATE** CSRF: `Origin` / `Sec-Fetch-Site` check and `X-CSRF-Token` enforced on every unsafe route; no CORS middleware (tests generated from the OpenAPI document)
- [ ] Re-authentication for email change, MFA change and account deletion
- [ ] **GATE (admin)** `is_admin` writable only by the owner-level CLI (column-level grants verified in CI); admin routes require MFA within 15 minutes; admin idle timeout 15 minutes
- [ ] **GATE (admin)** The `app_admin` role cannot SELECT any tenant table (a CI test connects as it); admin responses contain no trade, note or screenshot fields (OpenAPI scan); every admin mutation and user-detail view lands in `audit_log`

### Access control and tenancy
- [ ] **GATE** `app_rw` is not superuser and lacks `BYPASSRLS` (checked in CI)
- [ ] **GATE** Schema lint passes: RLS enabled and forced, policy present, index leads with `user_id`, on every tenant table
- [ ] **GATE** Two-tenant suite passes for every domain function and `/files/{id}`
- [ ] **GATE** No tenant route can reach `system_session`, and no admin route can reach `tenant_session` (import-boundary test)
- [ ] Entitlement checks live in the domain/service layer; the effective plan is computed and never stored, so webhooks cannot overwrite an override

### Input, output, files
- [x] **GATE** Notes sanitized on write and read; XSS payload tests (2026-10-04: `nh3` on write, DOMPurify on render, the same list pinned by a test; 58 tests in `tests/test_api_sanitize.py`)
- [ ] **GATE** Upload limits at the edge, the route and the streaming reader; pixel cap; CSV row cap; import concurrency limit
- [ ] **GATE** No raw HTML injection outside `SafeHtml` (ESLint `react/no-danger` plus a CI grep for `dangerouslySetInnerHTML` and `innerHTML`)
- [ ] Private bucket; no public object URLs; presigned URLs 60 s

### Transport and headers
- [ ] **GATE** HTTPS only, HSTS; Cloudflare "Full (strict)" TLS
- [ ] **GATE** A strict CSP sent as a header by the API (not a `<meta>` tag), starting from `default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`, plus `nosniff`, `Referrer-Policy` and `Permissions-Policy`. **Spike S1 result:** this policy works with Vite output, Tailwind 4, React, ECharts and Tiptap with two fixes (a plain-text ECharts tooltip formatter; Tiptap `injectCSS: false`) and **no `style-src-attr` exception**. Still open: `img-src` must allow the R2 host if `/files/{id}` redirects there (spike S3), and CSP report delivery, which did not work from the embedded browser (S1 used the console and a Playwright test that fails on any violation, with a control proving it catches one)

### Secrets and supply chain
- [ ] **GATE** No secrets in the repo; gitleaks green; `.env.example` only
- [ ] **GATE** Separate credentials per environment; DB `owner` role used only by the migration step
- [ ] Lock files with hashes (`uv.lock`, `pnpm-lock.yaml`); Dependabot on for uv, npm, Actions and Docker; `pip-audit` and `pnpm audit` green; GitHub Actions pinned by SHA
- [ ] **GATE** pnpm configured with dependency lifecycle scripts blocked (any allow-listed build script reviewed) and a `minimumReleaseAge`; `pnpm install --frozen-lockfile` in CI and the Dockerfile
- [ ] **GATE** No secrets in `VITE_*` variables or the bundle (CI grep); source maps not served publicly
- [ ] 2FA with a hardware key or authenticator app on GitHub, host, Cloudflare, registrar, IdP, payment provider

### Resilience, logging, privacy
- [ ] **GATE** Backups configured; **one successful restore drill** documented
- [ ] Audit log events written (login, failure, change, import, undo, export, delete, admin)
- [ ] Sentry scrubbing verified against a real error payload
- [ ] Export and deletion work end to end, including R2 prefix and IdP user
- [ ] Privacy Policy, Terms, financial disclaimer, subprocessor list reviewed by a professional

### 4.1 Gates already enforced in code (2026-10-03)

Boxes stay unticked above because each is a launch gate for the whole system. These parts are built, and each is held in place by a test or a CI step that fails if it regresses. "Mutation-checked" means I removed the protection on purpose and watched the test fail.

| Gate (section 4) | Enforced by | Still open |
|---|---|---|
| CSRF on every unsafe route; no CORS | `tests/test_api_gates.py`, generated from the OpenAPI document: every operation answers an anonymous caller 401, and every write answers 403 to a missing token, a wrong token, a cross-site request and a foreign origin, with a positive control. A rogue unguarded route is caught (mutation-checked). `PUBLIC` is an empty allow-list. A test also checks that no CORS middleware exists and a preflight gets nothing | Real sessions, rotation and revocation now exist (M5 slice 1 and 2); the same generated tests cover `POST /auth/logout` |
| Strict CSP, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, HSTS | `api/security.py`; tests assert no `unsafe-inline` or `unsafe-eval` (mutation-checked) and HSTS only in production | Cloudflare "Full (strict)"; `img-src` for R2 (S3) |
| Upload and body limits | `api/limits.py`: `Content-Length` checked before the app runs, chunked bodies counted as read, separate JSON and multipart ceilings (mutation-checked) | Per-route exact limits (10 MB per screenshot, 10 files), the edge cap, pixel cap, CSV row cap, import concurrency limit |
| No raw HTML outside `SafeHtml` | `frontend/scripts/check-security.mjs` in CI (also forbids `eval` and `new Function`); ESLint `react/no-danger` still applies | |
| No secrets in `VITE_*` or the bundle; no public source maps | The same script: no `import.meta.env.VITE_*` or `process.env` in the app, no `.env` files, no `.map` files, no secret-shaped strings in `dist/` | |
| Locked dependencies, frozen installs | `uv sync --frozen`, `pnpm install --frozen-lockfile`, `pip-audit`, `pnpm audit` in CI. The Dockerfile builds the front end with a frozen install, and base images are pinned by digest | |
| The `app_admin` role (and every other system role) cannot read a tenant table | `tests/pg/test_pg_system_roles.py`: a catalog check of every system role against every tenant table and every privilege (table-level and column-level), then a connection as each role that tries to read each table and is refused. The only exception is `app_admin` reading `user_usage`, through a read-only policy the schema lint allows and nothing else. Mutation-checked (a grant on `accounts`, a non-read-only policy, no policy) | Admin responses scanned for trade, note and screenshot fields (no admin routes yet); `audit_log` writes (M5, M10) |
| `is_admin` writable only by the owner | The same file: no role has INSERT or UPDATE on `users.is_admin` in the catalog, and the login role is refused when it tries to set it, or `status`, on insert or update (mutation-checked) | The CLI that creates operators; fresh MFA on admin routes (M5, M10) |
| No tenant route reaches `system_session`; no admin route reaches `tenant_session` | `tests/test_import_boundary.py` reads the source of every module under `api/routers` and resolves its imports, relative ones included. The checker is tested against seven spellings of each forbidden import. Mutation-checked with a probe route on each side | Covers route modules only: a helper that a route calls and that imports the wrong session is not seen. Revisit when admin routes exist |
| Callback validates `state`, `nonce`, PKCE, ID-token `iss`/`aud`/`exp`; requires `email_verified` | `tests/test_auth_kinde.py` (the adapter, with real RSA keys and a fake provider over a mock transport) and `tests/pg/test_pg_auth_flow.py` (the whole round trip against Postgres). RS256 only; `exp`, `iat`, `iss`, `aud`, `sub` required; `email_verified` must be exactly true. Seventeen mutations across both files are each caught | The same checks against the real Kinde tenant (needs its callback URL set and the three settings); the exact redirect-URI allow-list lives in the Kinde application |
| Cookie flags and `__Host-`; rotation; server-side revocation; idle and absolute timeouts | The flow test reads the real `Set-Cookie` headers (Secure, HttpOnly, SameSite=Lax, Path=/, no Domain, 90-day maximum, `no-store`); `tests/pg/test_pg_sessions.py` covers the token hash, expiry, idle, sliding, revocation and suspension; a new sign-in ends the old session; logout ends it at once | A "log out everywhere" route; the session list in settings |
| Invites: single use, one answer for every failure, no race | `tests/pg/test_pg_sessions.py` and the flow test: five kinds of failure (none, an invalid code, junk, another person's invite, a revoked one) give one code to the page and one reason (`invite`) in the log; the last use under a race is undone whole (mutation-checked) | Per-address rate limit on the sign-in routes (M6) |
| `audit_log` is append-only and holds no secrets | A trigger refuses every update except detaching a deleted person; the sign-in role can only INSERT; the flow test searches the log for the code, the `state`, the token and the secret | Export and the viewer (M5 later); the admin events (M10) |

## 5. Logging, monitoring and incident response

- **Log:** auth events, import and undo, exports, deletions, admin actions, permission failures. **Never log:** passwords, session or CSRF tokens, notes, trade contents, screenshot names, full IPs (store a salted hash).
- **Alerts:** spike in 401/403, spike in failed logins, 5xx rate, webhook failures, backup older than 26 h, a new CSP violation pattern, any admin action outside expected hours.
- **Incident response:** write `docs/runbooks/incident.md` in M7 (severity levels, who to notify, how to revoke all sessions, how to rotate every secret, how to restore, how to notify users within the legal window). The `engineering:incident-response` skill can draft it.
- **Breach notification:** if user data is exposed, many jurisdictions require notice within days; the runbook records the contact list and template ahead of time.

## 6. Privacy and legal (not legal advice)

- **Data minimization:** no broker credentials; no precise IPs; no third-party trackers in the app.
- **Rights:** export (F-A5) and deletion (F-A6) are built in. Disclose backup retention.
- **Financial disclaimer:** the product analyzes data the user supplies; it is **not investment advice**; past performance does not predict future results; prop-rule tracking (F-P5) is **informational and may differ from the firm's official calculation**. Insights stay descriptive ("you lose more on trades tagged X"), never prescriptive ("buy"/"sell").
- **Cookies and storage:** only essential: the session cookie, a short-lived OIDC state cookie during sign-in, and `localStorage` for per-browser UI preferences. No analytics or tracking in the app. Confirm with counsel whether a banner is required for your user regions.
- **Operator access:** admins never see trade, note or screenshot content (enforced by database grants, doc 03 and ADR-012), cannot impersonate users, and every admin action is audited. Say so in the Privacy Policy.
- **Subprocessors:** host, database, R2/Cloudflare, IdP, email provider, payment provider, Sentry. List them publicly and keep DPAs.
- **Terms of Service:** versioned; record acceptance (`users.tos_version`).

## 7. Verification plan

1. **Automated (every PR):** unit and integration tests, schema lint, two-tenant suite, route-level two-tenant and CSRF tests generated from the OpenAPI document, the admin-role grant test, header test, XSS corpus, gitleaks, `pip-audit`, `pnpm audit`, ruff `S` rules, ESLint security rules, and the API-type drift check.
2. **Before beta (M7):** self-run checklist above; OWASP ZAP baseline scan against staging (SPA and API); manual attempts at IDOR on every route and on `/files/{id}` with a second account.
3. **Before public launch (M11):** `engineering:code-review` and the built-in `security-review` on the full auth, billing, file and **admin** code (the `is_admin` path, MFA freshness, audit completeness); a paid or peer **penetration test** focused on tenant isolation, auth and the admin surface if budget allows; load test; restore drill repeated.
4. **After launch:** quarterly dependency and access review; annual restore drill and checklist refresh; a simple vulnerability-disclosure page (`security.txt` and an email address).
