# 02. Architecture

*Revised 2026-10-02 for [ADR-010](06-decisions.md) (FastAPI + React) and ADR-011 (same-origin cookie). Front-end tooling and the API contract are in [doc 07](07-frontend-and-api.md).*

## 1. Principles

| # | Principle | Why |
|---|---|---|
| P1 | **Never hold broker credentials.** CSV upload only | Biggest liability avoided; matches reality (prop accounts have no API) |
| P2 | **Isolation is enforced by the database**, not by discipline | One forgotten `WHERE` in ~100 statements is a breach |
| P3 | **Managed services over self-hosted** | One part-time developer cannot also be the DBA and the sysadmin |
| P4 | **Portable:** Docker image, plain Postgres, S3 API, env-var config | Hosting becomes a one-day, reversible decision |
| P5 | **Data is never held hostage.** Export always works, even when a subscription lapses | Trust is the product |
| P6 | **Boring technology; keep what works** | Domain logic and tests are the asset |
| P7 | **One origin, stateless API.** The SPA and the API share an origin and the API keeps no per-user memory | Simple cookies and a normal CSP; any instance can serve any request; deploys drop nothing |

## 2. Today versus target

```
TODAY (one user, one machine)
  Browser ──127.0.0.1──► NiceGUI process ──► SQLite file + ./data/screenshots/

TARGET (many users, hosted)

                     Internet
                        |
              +---------v----------+          marketing site (static)
              |    Cloudflare      |--------> example.com   (Cloudflare Pages)
              | DNS, TLS, WAF, CDN |
              +---------+----------+
                        | app.example.com   (HTTPS, one origin)
              +---------v------------------------------------+
              |  API container (1..N stateless instances)    |
              |  FastAPI + Python 3.12                       |
              |   /                 SPA: index.html, /assets |
              |   /api/v1/*         JSON API (tenant)        |
              |   /api/admin/v1/*   operator API             |
              |   /auth/*           login, callback, logout  |
              |   /files/{id}       screenshot redirect      |
              |   /webhooks/*       billing events           |
              |   /healthz /readyz                           |
              |  session dependency -> tenant_session        |
              +----+-------------+--------------+------------+
                   |             |              |
          +--------v--+   +------v-----+  +-----v--------+
          | Postgres  |   | R2 bucket  |  | Email API    |
          | (managed) |   | (private)  |  | (txn mail)   |
          | RLS on    |   | u/<user>/..|  +--------------+
          +-----------+   +------------+

   Hosted IdP (OIDC) <-- login redirect --> Browser
   Payment provider ---- signed webhook --> /webhooks/billing
   Sentry (errors, scrubbed)    Uptime monitor (/healthz)

   Build time: frontend/ (Vite + React + TypeScript) --pnpm build--> dist/ --copied into--> API image
```

## 3. Components

| Component | Choice | Responsibility | Notes |
|---|---|---|---|
| Edge | Cloudflare (free) | DNS, TLS, basic WAF, bot fight, coarse rate limits, caching of hashed `/assets/*` | |
| Marketing site | Static (Astro or plain HTML) in `site/`, on Cloudflare Pages | Landing, pricing, docs, legal | Separate host from the app; app cookies are host-only (`__Host-`), so they never reach it |
| **SPA** | Vite + React 19 + TypeScript, built to static files | The whole user interface | Served by the API container, same origin (ADR-011). Tooling in doc 07 |
| **API** | FastAPI, Python 3.12, Pydantic v2, psycopg pool, **stateless** | JSON API over the domain modules, auth callbacks, webhooks, health, SPA files | Scales by adding instances; no sticky sessions |
| Database | Managed PostgreSQL 16+ | All tenant data, sessions, subscriptions, audit log | Roles: `owner` (migrations), `app_rw` (RLS-bound), and narrow system profiles incl. `app_admin` (doc 03) |
| Files | Cloudflare R2, private | Screenshots, thumbnails, data-export ZIPs | Key `u/<user_uuid>/<uuid>.<ext>`; presigned GET via `/files/{id}`; the redirect target must be allowed by the CSP (doc 04) |
| Identity | Hosted IdP over OIDC | Passwords, MFA, email verification, breach checks | We keep our own `users` and `sessions` rows (doc 03) |
| Email | Transactional provider (Resend, Postmark or SES) | Receipts, deletion confirmations, export-ready notices | SPF, DKIM, DMARC on a sending subdomain |
| Billing | Provider's hosted checkout + portal | Payment, tax, invoices | We never see card data |
| Errors | Sentry (backend and browser) | Exceptions with PII scrubbing | `send_default_pii=False`; strip request bodies and notes; source maps uploaded to Sentry, **not served publicly** |
| Uptime | Any external pinger | `/healthz` every minute, alert by email/SMS | |
| Jobs | In-process for launch (see below) | Imports (thread pool), deletion sweep, export builder, reconcile | |

**Background work at launch.** An import runs in a thread pool inside the request, behind a concurrency semaphore; if one ever exceeds about 10 seconds it becomes a job with polling (`GET /api/v1/jobs/{id}`). The rest (account-deletion sweep, export builder, orphan-file report, billing reconcile, retention) are small and run as scheduled tasks: a Render Cron Job that calls an internal command (`python -m tradejournal jobs run <name>`). Introduce a real queue (Postgres `SKIP LOCKED` table) only when a job needs retries or progress UI.

## 4. Request flow

```
Page load
1. Browser GET /trades   (cookie: __Host-tj_session=<opaque token>)
2. FastAPI returns index.html (static, no auth needed); /assets/* are hashed and immutable
3. The SPA boots and calls GET /api/v1/session
     200  {user, settings, plan, entitlements, csrf_token, flags, app_version}  -> render
     401  -> window.location = /auth/login?next=/trades   (full-page OIDC flow, doc 03 section 4)

API call
4. fetch('/api/v1/trades?...', {credentials: 'same-origin'}); unsafe methods add X-CSRF-Token
5. FastAPI dependency: sha256(cookie) -> sessions row (cached 60 s) -> user.
   suspended or pending_deletion -> 403. Unsafe methods: Origin / Sec-Fetch-Site and CSRF token checked.
6. Handler:
       with tenant_session(user.id) as conn:     # BEGIN; set_config('app.user_id', <uuid>, true)
           ... existing domain functions, unchanged signatures ...
       # COMMIT; the setting is transaction-local, so pooled connections never leak identity
7. Postgres RLS filters every statement to this user. If the setting is missing, the policy
   evaluates to NULL and the query returns zero rows (fails closed).
8. JSON response (money as strings); errors as application/problem+json.
```

Every request re-validates the session (cached 60 s per instance), so "log out everywhere" takes effect within a minute. There is no long-lived connection that could outlive a revocation.

**Stale tabs.** A deploy replaces the SPA, but an already open tab keeps running the old one. Keep the API backward compatible for one release; every response carries `X-App-Version` and the SPA shows a "reload" banner when it differs from its own build.

## 5. Code layout

Do **not** reorganize for its own sake. Add what multi-tenancy, auth, the API and the SPA need; the existing modules are cohesive.

```
backend/tradejournal/
  finance.py parser.py grouping.py trades.py timeutil.py symbols.py models.py   KEEP (pure, unchanged)
  accounts.py ledger.py journal.py importer.py store.py queries.py              PORT (SQL dialect only)
  settings.py                                                                   REWRITE (per-user)
  db.py                                                                         REWRITE (pool, tenant_session, system_session, runner)
  config.py                                                                     REWRITE (env-based settings)
  NEW  api/        app factory, deps (current_user, tenant_session, require_admin), errors, spa.py, openapi.py
                   routers/  session, accounts, imports, trades, tags, screenshots, stats, settings,
                             profile, exports, onboarding, webhooks, health
                   admin/    users, plan_overrides, codes, audit, metrics
                   schemas/  Pydantic models (Money type, pagination, problem+json)
  NEW  auth/       OIDC client, sessions, CSRF, MFA freshness
  NEW  infra/      storage.py (R2), email.py, billing.py (provider interface), entitlements.py, logging.py, ratelimit.py
  NEW  jobs/       deletion sweep, export builder, orphan report, billing reconcile
  ui/                                                                           DELETE once the SPA reaches parity (reference until M4 exits)
  cli.py                                                                        KEEP as admin tool (adds `admin grant|revoke|list`, `--user`)
frontend/                                                                       NEW (layout in doc 07 section 4)
```

Port effort for the data layer is unchanged and bounded: about 100 `conn.execute` call sites across 8 modules (counted with grep), plus the migration scripts. The SQLite-specific constructs to translate: `?` placeholders, `lastrowid`, `INSERT OR IGNORE`, `COLLATE NOCASE`, the `GLOB` check constraint, `PRAGMA`, `executescript`, TEXT-typed money and timestamps (doc 03 has the mapping). The API layer is about 60 routes over those modules (doc 07 section 5).

**The seam is now the plan.** The earlier design kept a "seam for a future UI rewrite". The rewrite is happening now, so the seam is the FastAPI layer: domain and data modules know nothing about HTTP, and the SPA knows nothing about the database.

## 6. Hosting

Hosting is low-regret because the unit of deployment is a Docker image and the database is plain Postgres.

| Option | Ops burden | DB backups / PITR | Approx. cost (web + Postgres) | Notes |
|---|---|---|---|---|
| **Render** | Low | Managed; PITR on every paid DB, **3-day window on the Hobby workspace, 7-day on Pro** ([docs](https://render.com/docs/postgresql-backups)) | about $21 to 34 / month | Flat, predictable pricing; GitHub auto-deploy; zero-downtime deploy behavior to confirm in S4 |
| Railway | Low | Postgres runs as a service you configure; check backup story before relying on it | about $10 to 15 / month | Usage-billed; cheaper; weaker backup guarantees (unverified) |
| Fly.io | Medium | Fly Postgres is unmanaged; managed Postgres starts around $38 | cheap compute, DB pricier | Strong for multi-region later, more DIY now |
| Hetzner VPS + Docker/Coolify | **High** | You build it: pg_dump, WAL archiving, patching, firewall | about 4 EUR + your time | Cheapest; you become the sysadmin. Revisit at scale |

Prices are from third-party summaries ([Render](https://render.com/articles/railway-vs-fly-io), [techsy](https://techsy.io/en/blog/railway-vs-render-vs-fly-io)); confirm on the vendors' pricing pages.

**Recommendation: Render.** The deciding factor is durable, managed point-in-time recovery for data users cannot recreate. The extra $10 to 20 a month over Railway buys that. Pick a **US region** near most users unless question 4 in the README says otherwise.

**How the SPA is served (ADR-011).** One Render web service runs one image that contains the API and the built SPA, so the browser sees a single origin: the `__Host-` cookie works, there is no CORS, and the CSP can say `connect-src 'self'`. Cloudflare caches the hashed `/assets/*`. Two alternatives were weighed and set aside: SPA on Cloudflare Pages with `/api` proxied to Render (an extra hop, Cloudflare-specific configuration, free-tier request quotas that are unverified), and separate `app.` and `api.` subdomains (CORS with credentials, no `__Host-` prefix). If the single-origin choice is ever reversed, spike S4 becomes a two-piece deploy.

**Sizing.** The API holds no per-user state, so memory should track concurrent requests, not open tabs, and be far below NiceGUI's. That is **unmeasured**: start staging on the smallest tier, measure resident memory under a load test in S4, and treat the 2 GB production tier as a cautious starting point rather than a requirement. Pandas is imported in exactly one place (`parser.py:92`); replacing it with the stdlib `csv` module would shrink the image and memory, a post-launch optimization.

**Instances and deploys.** Because the API is stateless, run two instances once users depend on it so a deploy or crash never means downtime; one is fine for staging and early beta. Open tabs survive a deploy (no websockets); in-flight requests finish on graceful shutdown. Avoid deploying during US market hours (06:30 to 13:00 Pacific) once users exist. In-process state is limited to two caches (the 60 s session cache and the rate limiter); the rate limiter moves to Postgres or Redis when there is more than one instance.

## 7. GitHub, CI/CD and environments

### Repository

- **Private** repo `IsaacCodes2021/trading-journal-saas` (created in M0), personal account to start (transfer to an organization later when there is a legal entity; transfers preserve history). The tag `v0.1.0-local` marks the last local-first version.
- **GitHub Free limits on private repos:** no branch protection or rulesets ([docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)), no code-scanning add-ons. Two ways to handle it:
  1. GitHub Pro (a few dollars a month; price unverified) to protect `main`: required CI, no force-push, no direct push. **Recommended once you launch.**
  2. Free: work by pull request anyway and let the host deploy only after CI passes, so a red `main` never ships.
- **Trunk-based:** short-lived branches `feat/...`, `fix/...`, `docs/...`, squash-merge to `main`.
- Use GitHub **Issues + Projects** as the backlog; retire the README status table.
- `.gitignore` covers `data/`, `.venv/`, `.env*`, `*.db*`, caches, exports (`*.csv` outside `tests/fixtures/`), `node_modules/` and `dist/`; `.gitattributes` keeps fixture bytes exact. The fixtures contain only fill IDs, prices and timestamps (no names or account numbers), so they are safe in a private repo; anonymize them if the repo ever goes public. Drop the `.nicegui/` entry when `ui/` is deleted.

### Branches, environments, deploy flow

```
 feature branch --PR--> main --(auto, after CI green)--> STAGING (own DB, own R2 bucket, own IdP tenant)
                          |
                          +--(you fast-forward)--> `production` branch --(auto)--> PRODUCTION
                              git push origin main:production        (manual, deliberate, after staging check)
```

- Render services track branches (`main` for staging, `production` for prod) with **auto-deploy after CI checks pass** (confirm the setting exists on your plan).
- Migrations run as a **release step** (pre-deploy command) using the `owner` role with a Postgres advisory lock, never on connection open as `db.py:247` does today.
- Secrets live in the host's environment settings and GitHub Actions secrets. Never in the repo. Ship `.env.example` with names only. **Front-end build variables (`VITE_*`) are public by design: they hold no secrets**, and CI greps for that.
- Local dev: a native Postgres install (Docker is deferred, see the tools table below); the API runs from your venv and the SPA from `pnpm dev` with a dev proxy to the API. Python is pinned to 3.12 (`backend/.python-version`), Node to 24 LTS.

### CI (`.github/workflows/ci.yml`), on every PR and push to `main`

**Back end** (implemented in M0, except the Postgres service which arrives in M2):

1. `uv sync --frozen` from a **lock file with hashes**; `requirements.txt` is generated from it and CI checks they agree.
2. `ruff check` and `ruff format --check`, with ruff's `S` rules (bandit-style security checks; the free substitute for CodeQL on a private Free repo).
3. `pytest` (SQLite today; against a **Postgres service container**, same major version as production, from M2).
4. `pip-audit` on the pinned versions.
5. `gitleaks` (pinned binary, full history).
6. `docker build` (no push) plus an import smoke test.

**Front end** (added with the SPA scaffold in M1):

7. `pnpm install --frozen-lockfile`, with dependency lifecycle scripts blocked and a minimum release age configured (doc 04).
8. ESLint, `tsc --noEmit` (type check), Vitest, `pnpm build`, a bundle-size budget check.
9. `pnpm audit` for known-vulnerable packages.
10. **API type drift:** regenerate `openapi.json` and `schema.d.ts` and fail on any diff (doc 07 section 2).
11. From M5: Playwright end-to-end tests against the built image and a fake IdP.

The image build becomes **multi-stage**: a Node 24 stage builds the SPA, a Python stage adds it to the API.

Also: **Dependabot** for uv, GitHub Actions, Docker and npm (Dependabot should read `pnpm-lock.yaml`; confirm in M1); a PR template with the security checklist from doc 04; later, a scheduled workflow that restores last night's backup into staging as the restore drill.

Free-plan budget is 2,000 Actions minutes per month; the full pipeline should use well under 10 minutes per run (to be measured).

### Tools on your machine

| Tool | Status | Note |
|---|---|---|
| Git for Windows | **Installed** (2.55.0) | |
| GitHub CLI (`gh`) | **Installed** (2.102.0), authenticated | Do not paste tokens into chats or files |
| `uv` and `ruff` | **Installed** in the project's Python 3.12 venv | |
| Node.js | **Installed** (24.21 LTS) | Pinned in `.nvmrc` and CI |
| pnpm | Not installed; added in M1 | Corepack ships with Node 24 but is dropped from Node 25+; pin `packageManager` and use pnpm's own installer (doc 07) |
| Docker Desktop (WSL2): **deferred** | | Not practical on this machine today: hardware virtualization is off in firmware, WSL is still v1, about 11 GB free on C:. CI builds the image; use a native Postgres install for local development |

Account hygiene while you are at it: GitHub, the host, Cloudflare and the domain registrar all get **2FA (a hardware key or authenticator, not SMS)** and a password manager. Windows 10 reached end of support in October 2025; this machine will hold production deploy access, so plan to move to Windows 11 or enroll in extended security updates.

## 8. Observability

| Signal | Tool | Alert when |
|---|---|---|
| Availability | External pinger on `/healthz` (process up) and `/readyz` (DB reachable) | 2 failures in a row |
| Errors | Sentry (API and browser), PII scrubbed; no trade data, notes or screenshot names | New issue; spike in rate |
| Logs | JSON to stdout with request id and a hashed user id; platform log search | |
| CSP violations | Reports to a logging endpoint (`report-to`) | A new violation pattern |
| Database | Provider metrics: connections, disk, CPU | Disk above 80%, connections above 80% of limit |
| Backups | Job reports success/age | Last good backup older than 26 h |
| Business | Counts from `user_usage` and the DB (signups, imports, active users) on the admin overview | |

Do not add third-party session-replay or ad analytics inside the app (it renders financial data). Use a privacy-friendly analytics tool on the marketing site only.

## 9. Backup and disaster recovery

- **Postgres:** provider PITR (RPO about 5 minutes within the retention window) plus a **nightly logical dump** to a *separate* R2 bucket in a different account/credentials, 30-day lifecycle, so one stolen credential or one mistaken `DROP` cannot take out both the data and its backups.
- **R2:** enable object versioning on the screenshots bucket and a 30-day lifecycle on noncurrent versions; orphan cleanup never hard-deletes (see doc 04).
- **Restore drill:** restore the latest dump into staging and run the app against it, quarterly and before launch. A backup that has never been restored is a hope, not a backup.
- **Runbook:** a short `docs/runbooks/restore.md` written during M7.
- **Targets:** RPO 5 minutes, RTO 4 hours (doc 01).

## 10. Cost at beta (monthly, approximate)

| Item | Estimate | Confidence |
|---|---|---|
| Production web + Postgres | $25 to 45 | Third-party prices; verify. Web memory is unmeasured, so this assumes the cautious tier |
| Staging web + small Postgres | $10 to 15 | Same; or skip a permanent staging and use a temporary one |
| R2 | about $0 under 10 GB, then $0.015 per GB | Vendor page |
| Cloudflare (DNS, CDN, Pages for the marketing site), Sentry free tier, hosted IdP free tier | $0 | Kinde's free plan covers 10,500 MAU including MFA (doc 08); verify at signup |
| Transactional email | $0 on a free tier | Unverified |
| Domain | about $1 | |
| GitHub Pro (optional but recommended) | a few dollars | Unverified |
| **Total** | **about $35 to 65** | Right at the $60 default ceiling |

## 11. Scale path and what I would revisit

| Trigger | Revisit |
|---|---|
| Rising request load or latency | Add instances (stateless, no sticky sessions); move the rate limiter to Postgres or Redis |
| Dashboard slower than 800 ms p95 | Push aggregation into SQL; cache per-user summary rows |
| Native mobile app or public API | The JSON API and OpenAPI document already exist; add API tokens, versioning policy and per-token rate limits |
| Front-end deploy frequency hurts the coupled deploy | Move the SPA to Cloudflare Pages with an edge proxy (ADR-011 option B); the API contract is unchanged |
| Screenshot volume above ~1 TB | Lifecycle to Infrequent Access; thumbnails only for old data |
| Revenue above the point where PaaS cost matters | Move to a VPS with Coolify or Kubernetes-lite; the Docker image and Postgres port unchanged |
| EU customer base | EU region for DB and bucket; DPA with each subprocessor |
