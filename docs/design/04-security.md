# 04. Security

Target: **OWASP ASVS Level 2** as the checklist (a checklist, not a certification). The design principle is *defense in depth where the cost of a miss is a cross-user breach*: tenant isolation has three independent layers (doc 03).

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

We hold **no** broker credentials, API keys or card data by design (P1, and the payment provider hosts checkout).

## 2. Threat model

| # | Threat | Primary mitigations | Verified by |
|---|---|---|---|
| T1 | **Cross-tenant access** (missing filter, IDOR, SQL injection, file URL guessing) | RLS (forced) + composite FKs + default `user_id`; parameterized SQL only; `/files/{id}` authorizes under RLS; no public URLs | Schema lint test; two-tenant suite; manual review of every raw SQL string |
| T2 | **Account takeover** (credential stuffing, phishing, session theft) | Hosted IdP: breached-password check, rate limiting, MFA; opaque revocable sessions; `__Host-` HttpOnly Secure cookie; re-auth for email change and deletion; "new login" email | IdP configuration checklist; session tests |
| T3 | **Malicious upload** (image bomb, polyglot, huge file, CSV bomb) | Size limits at proxy, upload widget and server; Pillow decode with pixel cap and re-encode; CSV size and row caps; parse in the worker thread with a timeout | Fuzz fixtures; oversize tests |
| T4 | **XSS** (stored via notes; via names rendered into HTML strings) | Allow-list sanitize notes on write **and** read; keep using escaped rendering (`esc()` in `trades.py:58`); no `ui.html` of user data; security headers | Unit tests with XSS payload corpus |
| T5 | **Denial of service** (heavy imports, websocket floods, expensive dashboards) | Per-user quotas and rate limits; import concurrency limit; Cloudflare; request timeouts; pagination | Load test in M8 |
| T6 | **Secrets leak** (repo, logs, CI, Docker layers) | `.gitignore`, gitleaks in CI, env-var secrets, Sentry and log scrubbing, no secrets in image, separate keys per environment, rotation runbook | CI; periodic review |
| T7 | **Supply chain** (malicious or vulnerable dependency, compromised GitHub Action or account) | Lock file with hashes; Dependabot; `pip-audit`; GitHub Actions pinned by commit SHA; hardware-key 2FA on GitHub and the host; minimal dependencies | CI |
| T8 | **Operator error** (destructive command in prod, restore of the wrong data) | Separate prod credentials not stored on the dev machine by default; PITR + separate backup bucket; expand-contract migrations; rehearse on staging; admin UI has no data access | Restore drill |
| T9 | **Webhook forgery** | Signature verification; idempotent event table; reconcile job | Unit tests with bad signatures |
| T10 | **Data loss** (bad migration, deletion bug) | PITR, nightly dump to a separate bucket, soft-delete grace, R2 versioning, orphan job that reports before it deletes | Restore drill |
| T11 | **Third-party data exposure** (error tracker, logs, analytics) | `send_default_pii=False`; scrub request bodies; no session replay in the app; subprocessor list; DPAs | Review of Sentry payloads |
| T12 | **OAuth flow attacks** (CSRF, open redirect, code injection) | `state`, `nonce`, PKCE; exact redirect-URI allow-list; ID token validation | Auth flow tests |
| T13 | **Clickjacking, MIME sniffing, downgrade** | `frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, HSTS with preload once stable, `Referrer-Policy: strict-origin-when-cross-origin` | Header test in CI |
| T14 | **Cross-site websocket hijack** | `Origin` check on the Socket.IO endpoint; per-page client id | Spike S1 (unverified assumption) |

## 3. Findings in today's code

Severity is for the *hosted* case. The app is safe today only because it binds to `127.0.0.1` (`ui/app.py:45`). **Do not expose the current build to any network.**

| # | Sev | Finding | Where | Fix |
|---|---|---|---|---|
| S1 | **Blocker** | No authentication or per-user data at all | whole app; `db.py` schema | Docs 03 (tenancy + auth) |
| S2 | **Blocker** | Screenshots served from an unauthenticated static route; protection is only the unguessability of a UUID filename | `ui/app.py:39` | Private bucket, `/files/{id}`, presigned redirect |
| S3 | **Blocker** | Process-wide filter state shared by all visitors | `ui/state.py:70` | Per-tab state in `app.storage.tab` |
| S4 | **High** | Global UNIQUE constraints leak existence of other users' data and cause collisions | `db.py` (`accounts.name`, `tag_groups.name`, `fill_pairs`) | Per-user uniqueness |
| S5 | **High** | Startup job deletes every file not referenced in the DB; destructive on shared storage or after a restore | `ui/app.py:38`; `journal.py:340` | Reporting-first, delayed, per-user job (doc 03 section 6) |
| S6 | **High** | Notes saved as raw editor HTML; `clean_notes` only checks emptiness | `journal.py:241`; `trade_detail.py:232` | Sanitize with an allow-list library (for example `nh3`): allow `p, br, b, strong, i, em, u, s, ul, ol, li, blockquote, h1-h3, a[href http/https/mailto]` with `rel="noopener noreferrer"`; drop everything else, including `style`, event handlers, `javascript:` and `data:` URLs |
| S7 | **High** | No upload size limits at the widget or proxy; the 25 MB check runs after the whole file is in memory; whole CSV parsed by pandas with no row cap | `trade_detail.py:269`, `pages/imports.py:236`, `journal.py:286`, `parser.py:92` | Limits at every layer; stream where possible; row cap (suggest 50,000) |
| S8 | **Medium** | Migrations run on every connection open | `db.py:247` | Release-step runner with advisory lock |
| S9 | **Medium** | Non-reproducible dependencies (floor versions); test tools in runtime requirements | `requirements.txt` | `uv` lock file with hashes; dev group |
| S10 | **Medium** | Server clock used for "today" | `ui/state.py:55`, `settings_page.py:340,393`, `widgets.py:131` | User timezone |
| S11 | **Medium** | CSV timestamps interpreted in a hardcoded default timezone | `settings.py:12` | Onboarding confirmation (doc 03 section 7) |
| S12 | **Low** | No structured logging, error tracking or request ids | `ui/app.py` prints | Logging + Sentry |
| S13 | **Low** | Stray file `backend/=ro` | repo | Delete |
| S14 | **Info** | NiceGUI renders with Vue's runtime template compiler and inline scripts, so a strict CSP will probably need `unsafe-inline` and `unsafe-eval` (not verified; test in spike) | `ui/app.py:14` inline paste script | Set the strongest CSP that works; rely on S6 as the primary XSS control |

**Credit where due.** Already good: image uploads are content-sniffed and **SVG is excluded** (it can carry script); files get random names; money is exact `Decimal`; SQL is parameterized (the dynamic `IN (...)` lists in `queries.py` build placeholders, not values); the one HTML cell renderer escapes tag names and styles (`trades.py:58`). The remaining work is a small number of well-understood gaps, not a rewrite.

## 4. Controls checklist (launch gate)

Items marked **GATE** must be done before any real user data is stored. Others must be done before public launch.

### Authentication and sessions
- [ ] **GATE** Hosted IdP configured: email verification required, breached-password check on, MFA available, brute-force protection on
- [ ] **GATE** Callback validates `state`, `nonce`, PKCE, ID-token `iss/aud/exp`; requires `email_verified`; exact redirect-URI allow-list
- [ ] **GATE** Cookie flags and `__Host-` prefix; session rotation; server-side revocation; idle and absolute timeouts
- [ ] Re-authentication for email change, MFA change and account deletion
- [ ] Admin accounts require MFA; admin page shows aggregates only

### Access control and tenancy
- [ ] **GATE** `app_rw` is not superuser and lacks `BYPASSRLS` (checked in CI)
- [ ] **GATE** Schema lint passes: RLS enabled and forced, policy present, index leads with `user_id`, on every tenant table
- [ ] **GATE** Two-tenant suite passes for every domain function and `/files/{id}`
- [ ] **GATE** No UI code can reach `system_session`
- [ ] Entitlement checks live in the domain/service layer

### Input, output, files
- [ ] **GATE** Notes sanitized on write and read; XSS payload tests
- [ ] **GATE** Upload limits at proxy, widget and server; pixel cap; CSV row cap; import concurrency limit
- [ ] Every JS-string renderer that interpolates user data uses `esc()`; grep audit recorded
- [ ] Private bucket; no public object URLs; presigned URLs 60 s

### Transport and headers
- [ ] **GATE** HTTPS only, HSTS; Cloudflare "Full (strict)" TLS
- [ ] CSP (as strict as NiceGUI allows), `frame-ancestors 'none'`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`
- [ ] Websocket `Origin` check confirmed

### Secrets and supply chain
- [ ] **GATE** No secrets in the repo; gitleaks green; `.env.example` only
- [ ] **GATE** Separate credentials per environment; DB `owner` role used only by the migration step
- [ ] Lock file with hashes; Dependabot on; `pip-audit` green; GitHub Actions pinned by SHA
- [ ] 2FA with a hardware key or authenticator app on GitHub, host, Cloudflare, registrar, IdP, payment provider

### Resilience, logging, privacy
- [ ] **GATE** Backups configured; **one successful restore drill** documented
- [ ] Audit log events written (login, failure, change, import, undo, export, delete, admin)
- [ ] Sentry scrubbing verified against a real error payload
- [ ] Export and deletion work end to end, including R2 prefix and IdP user
- [ ] Privacy Policy, Terms, financial disclaimer, subprocessor list reviewed by a professional

## 5. Logging, monitoring and incident response

- **Log:** auth events, import and undo, exports, deletions, admin actions, permission failures. **Never log:** passwords, tokens, notes, trade contents, screenshot names, full IPs (store a salted hash).
- **Alerts:** spike in 401/403, spike in failed logins, 5xx rate, webhook failures, backup older than 26 h.
- **Incident response:** write `docs/runbooks/incident.md` in M5 (severity levels, who to notify, how to revoke all sessions, how to rotate every secret, how to restore, how to notify users within the legal window). The `engineering:incident-response` skill can draft it.
- **Breach notification:** if user data is exposed, many jurisdictions require notice within days; the runbook records the contact list and template ahead of time.

## 6. Privacy and legal (not legal advice)

- **Data minimization:** no broker credentials; no precise IPs; no third-party trackers in the app.
- **Rights:** export (F-A5) and deletion (F-A6) are built in. Disclose backup retention.
- **Financial disclaimer:** the product analyzes data the user supplies; it is **not investment advice**; past performance does not predict future results; prop-rule tracking (F-P5) is **informational and may differ from the firm's official calculation**. Insights stay descriptive ("you lose more on trades tagged X"), never prescriptive ("buy"/"sell").
- **Cookies:** only essential (session, CSRF/state, NiceGUI tab storage). Confirm with counsel whether a banner is required for your user regions.
- **Subprocessors:** host, database, R2/Cloudflare, IdP, email provider, payment provider, Sentry. List them publicly and keep DPAs.
- **Terms of Service:** versioned; record acceptance (`users.tos_version`).

## 7. Verification plan

1. **Automated (every PR):** unit and integration tests, schema lint, two-tenant suite, header test, XSS corpus, gitleaks, `pip-audit`, ruff `S` rules.
2. **Before beta (M5):** self-run checklist above; OWASP ZAP baseline scan against staging; manual attempts at IDOR on every route and on `/files/{id}` with a second account.
3. **Before public launch (M8):** `engineering:code-review` and the built-in `security-review` on the full auth, billing and file code; a paid or peer **penetration test** focused on tenant isolation and auth if budget allows; load test; restore drill repeated.
4. **After launch:** quarterly dependency and access review; annual restore drill and checklist refresh; a simple vulnerability-disclosure page (`security.txt` and an email address).
