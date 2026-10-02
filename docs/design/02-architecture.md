# 02. Architecture

## 1. Principles

| # | Principle | Why |
|---|---|---|
| P1 | **Never hold broker credentials.** CSV upload only | Biggest liability avoided; matches reality (prop accounts have no API) |
| P2 | **Isolation is enforced by the database**, not by discipline | One forgotten `WHERE` in ~100 statements is a breach |
| P3 | **Managed services over self-hosted** | One part-time developer cannot also be the DBA and the sysadmin |
| P4 | **Portable:** Docker image, plain Postgres, S3 API, env-var config | Hosting becomes a one-day, reversible decision |
| P5 | **Data is never held hostage.** Export always works, even when a subscription lapses | Trust is the product |
| P6 | **Boring technology; keep what works** | Domain logic and tests are the asset |

## 2. Today versus target

```
TODAY (one user, one machine)
  Browser ──127.0.0.1──► NiceGUI process ──► SQLite file + ./data/screenshots/

TARGET (many users, hosted)

                     Internet
                        |
              +---------v----------+          marketing site (static)
              |    Cloudflare      |--------> yourdomain.com   (Cloudflare Pages)
              | DNS, TLS, WAF, rate|
              +---------+----------+
                        | app.yourdomain.com  (HTTPS + websocket)
              +---------v-----------------------------+
              |  App container (1 instance, 1 process)|
              |  NiceGUI + FastAPI                    |
              |   - /auth/*       login callbacks     |
              |   - UI pages      websocket           |
              |   - /webhooks/*   billing events      |
              |   - /healthz /readyz                  |
              |   session middleware -> tenant_session|
              +----+-------------+-------------+------+
                   |             |             |
          +--------v--+   +------v-----+  +----v---------+
          | Postgres  |   | R2 bucket  |  | Email API    |
          | (managed) |   | (private)  |  | (txn mail)   |
          | RLS on    |   | u/<user>/..|  +--------------+
          +-----------+   +------------+
                   ^
   Hosted IdP (OIDC) ---- login redirect ---- Browser
   Payment provider ---- signed webhook ----> /webhooks/billing
   Sentry (errors, scrubbed)    Uptime monitor (/healthz)
```

## 3. Components

| Component | Choice | Responsibility | Notes |
|---|---|---|---|
| Edge | Cloudflare (free) | DNS, TLS, basic WAF, bot fight, coarse rate limits | Proxies websockets; confirm in spike S4 |
| Marketing site | Static (Astro or plain HTML) in `site/` | Landing, pricing, docs, legal | Separate origin from the app; no app cookies on the apex domain |
| App | NiceGUI 3.x + FastAPI routes, Python 3.12, **one process** | UI, auth callbacks, webhooks, health | NiceGUI does not support multiple workers ([discussion](https://github.com/zauberzeug/nicegui/discussions/4483)) |
| Database | Managed PostgreSQL 16+ | All tenant data, sessions, subscriptions, audit log | Two roles: `owner` (migrations) and `app` (RLS-bound, not superuser) |
| Files | Cloudflare R2, private | Screenshots, thumbnails, data-export ZIPs | Key `u/<user_uuid>/<uuid>.<ext>`; presigned GET, 5-minute expiry |
| Identity | Hosted IdP over OIDC | Passwords, MFA, email verification, breach checks | We keep our own `users` and `sessions` rows (doc 03) |
| Email | Transactional provider (Resend, Postmark or SES) | Receipts, deletion confirmations, export-ready notices | SPF, DKIM, DMARC on a sending subdomain |
| Billing | Provider's hosted checkout + portal | Payment, tax, invoices | We never see card data |
| Errors | Sentry | Exceptions with PII scrubbing | `send_default_pii=False`; strip request bodies and notes |
| Uptime | Any external pinger | `/healthz` every minute, alert by email/SMS | |
| Jobs | In-process for launch (see below) | Imports (`run.io_bound`), deletion sweep, export builder | |

**Background work at launch.** Imports already run in a thread (`run.io_bound`, `pages/imports.py`). The rest (account-deletion sweep, export builder, orphan-file report, retention) are small and can run as scheduled tasks: a Render Cron Job that calls an internal command (`python -m tradejournal jobs run <name>`). Introduce a real queue (Postgres `SKIP LOCKED` table) only when a job needs retries or progress UI.

## 4. Request flow

```
1. Browser GET /trades  (cookie: __Host-session=<opaque token>)
2. Session middleware: sha256(token) -> sessions row -> user, check not expired/revoked
   no valid session -> 302 /login
3. Page function builds the UI. It captures `user` in its closure.
4. Later events arrive over the websocket. Each handler does:
       with tenant_session(user.id) as conn:     # BEGIN; set_config('app.user_id', <uuid>, true)
           ... existing domain functions, unchanged signatures ...
       # COMMIT; the setting is transaction-local, so pooled connections never leak identity
5. Postgres RLS filters every statement to this user. If the setting is missing, the policy
   evaluates to NULL and the query returns zero rows (fails closed).
```

A websocket that stays open outlives a session revocation. Mitigation: `tenant_session()` re-checks the session (cached 60 s) so "log out everywhere" takes effect within a minute.

## 5. Code changes (minimal re-layering)

Do **not** reorganize for its own sake. Add only what multi-tenancy, auth and hosting need; the existing modules are cohesive.

```
backend/tradejournal/
  finance.py parser.py grouping.py trades.py timeutil.py symbols.py models.py   KEEP (pure)
  accounts.py ledger.py journal.py importer.py store.py queries.py              PORT (SQL dialect only)
  settings.py                                                                   REWRITE (per-user)
  db.py                                                                         REWRITE (pool, tenant_session, runner)
  config.py                                                                     REWRITE (env-based settings)
  NEW  auth/        idp client (OIDC), sessions, middleware, current_user
  NEW  infra/       storage.py (R2), email.py, entitlements.py, logging.py, ratelimit.py
  NEW  api/         FastAPI routers: auth callbacks, webhooks, health, file redirects
  NEW  jobs/        deletion sweep, export builder, orphan report
  ui/                                                                           CHANGE: per-tab filters, auth gate, no global state
  cli.py                                                                        KEEP as admin tool (adds --user)
```

Port effort is bounded: about 100 `conn.execute` call sites across 8 modules (counted with grep), plus the migration scripts. The SQLite-specific constructs to translate: `?` placeholders, `lastrowid`, `INSERT OR IGNORE`, `COLLATE NOCASE`, the `GLOB` check constraint, `PRAGMA`, `executescript`, TEXT-typed money and timestamps (doc 03 has the mapping).

**Seam for a future UI rewrite.** If NiceGUI is ever outgrown, the domain and data modules stay; FastAPI routers expose the same operations as JSON. Because NiceGUI runs on FastAPI, JSON endpoints can be added beside the pages without a second server.

## 6. Hosting

Hosting is low-regret because the unit of deployment is a Docker image and the database is plain Postgres.

| Option | Ops burden | DB backups / PITR | Approx. cost (web + Postgres) | Notes |
|---|---|---|---|---|
| **Render** | Low | Managed; PITR on every paid DB, **3-day window on the Hobby workspace, 7-day on Pro** ([docs](https://render.com/docs/postgresql-backups)) | about $21 to 34 / month | Flat, predictable pricing; GitHub auto-deploy; websocket support to confirm in S4 |
| Railway | Low | Postgres runs as a service you configure; check backup story before relying on it | about $10 to 15 / month | Usage-billed; cheaper; weaker backup guarantees (unverified) |
| Fly.io | Medium | Fly Postgres is unmanaged; managed Postgres starts around $38 | cheap compute, DB pricier | Strong for multi-region later, more DIY now |
| Hetzner VPS + Docker/Coolify | **High** | You build it: pg_dump, WAL archiving, patching, firewall | about 4 EUR + your time | Cheapest; you become the sysadmin. Revisit at scale |

Prices are from third-party summaries ([Render](https://render.com/articles/railway-vs-fly-io), [techsy](https://techsy.io/en/blog/railway-vs-render-vs-fly-io)); confirm on the vendors' pricing pages.

**Recommendation: Render.** The deciding factor is durable, managed point-in-time recovery for data users cannot recreate. The extra $10 to 20 a month over Railway buys that. Pick a **US region** near most users unless question 4 in the README says otherwise.

**Sizing.** NiceGUI plus pandas plus numpy will likely not be comfortable in 512 MB. Plan on the 2 GB web tier for production and measure resident memory under 50 simulated websocket sessions in spike S4. Replacing pandas with the stdlib `csv` module (it is used in exactly one place, `parser.py:92`) would shrink the image and memory; a post-launch optimization.

**Deploys.** One instance means a deploy drops open websockets for a few seconds; NiceGUI clients reconnect or reload. Acceptable. Avoid deploying during US market hours (06:30 to 13:00 Pacific) once users exist.

## 7. GitHub, CI/CD and environments

### Repository

- **Private** repo, personal account to start (transfer to an organization later when there is a legal entity; transfers preserve history).
- **GitHub Free limits on private repos:** no branch protection or rulesets ([docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)), no code-scanning add-ons. Two ways to handle it:
  1. GitHub Pro (a few dollars a month; price unverified) to protect `main`: required CI, no force-push, no direct push. **Recommended once you launch.**
  2. Free: work by pull request anyway and let the host deploy only after CI passes, so a red `main` never ships.
- **Trunk-based:** short-lived branches `feat/...`, `fix/...`, squash-merge to `main`.
- Use GitHub **Issues + Projects** as the backlog; retire the README status table.
- Before the first commit: delete `backend/=ro`; confirm `.gitignore` covers `data/`, `.venv/`, `.env*`, `*.db`, `*.db-wal`, `*.db-shm`, `__pycache__/`, `.pytest_cache/`, `.nicegui/` (NiceGUI's storage folder), exports (`*.csv` outside `tests/fixtures/`). The fixtures contain only fill IDs, prices and timestamps (no names or account numbers) so they are safe in a private repo; anonymize them if the repo ever goes public. There is no history yet, so nothing sensitive can already be in it.

### Branches, environments, deploy flow

```
 feature branch --PR--> main --(auto, after CI green)--> STAGING (own DB, own R2 bucket, own IdP tenant)
                          |
                          +--(you fast-forward)--> `production` branch --(auto)--> PRODUCTION
                              git push origin main:production        (manual, deliberate, after staging check)
```

- Render services track branches (`main` for staging, `production` for prod) with **auto-deploy after CI checks pass** (confirm the setting exists on your plan).
- Migrations run as a **release step** (pre-deploy command) using the `owner` role with a Postgres advisory lock, never on connection open as `db.py:247` does today.
- Secrets live in the host's environment settings and GitHub Actions secrets. Never in the repo. Ship `.env.example` with names only.
- Local dev: a native Postgres install (Docker is deferred, see the tools table below); the app runs from your venv. The Python version is pinned to 3.12 (`backend/.python-version`).

### CI (`.github/workflows/ci.yml`), on every PR and push to `main`

1. Install Python 3.12, `uv sync --frozen` from a **lock file with hashes** (adopt `uv`; replaces floor-pinned `requirements.txt`; dev dependencies such as pytest move to a dev group).
2. `ruff check` and `ruff format --check`. Enable ruff's `S` rules (bandit-style security checks); this is the free substitute for CodeQL on a private Free repo.
3. `pytest` against a **Postgres service container** (same major version as production).
4. `pip-audit` (known-vulnerable dependencies).
5. `gitleaks` (secrets in the diff).
6. `docker build` (no push) to prove the image builds.

Also: **Dependabot** for pip, GitHub Actions and Docker (free); a PR template with the security checklist from doc 04; later, a scheduled workflow that restores last night's backup into staging as the restore drill.

Free-plan budget is 2,000 Actions minutes per month; this pipeline uses a few minutes per run.

### Tools to install on your machine (step 1; nothing is installed yet)

| Tool | Why | Note |
|---|---|---|
| Git for Windows | Version control | Not currently on PATH |
| GitHub CLI (`gh`) | Create the repo, PRs, secrets from the terminal | `gh auth login` with a browser; do not paste tokens into chats or files |
| Docker Desktop (WSL2): **deferred** | Local Postgres, build the production image | Not practical on this machine today: hardware virtualization is off in firmware, WSL is still v1, about 11 GB free on C:. CI builds the image; use a native Postgres install for local development |
| `uv` | Lock file and fast installs | |

Account hygiene while you are at it: GitHub, the host, Cloudflare and the domain registrar all get **2FA (a hardware key or authenticator, not SMS)** and a password manager. Windows 10 reached end of support in October 2025; this machine will hold production deploy access, so plan to move to Windows 11 or enroll in extended security updates.

## 8. Observability

| Signal | Tool | Alert when |
|---|---|---|
| Availability | External pinger on `/healthz` (process up) and `/readyz` (DB reachable) | 2 failures in a row |
| Errors | Sentry, PII scrubbed; no trade data, notes or screenshot names | New issue; spike in rate |
| Logs | JSON to stdout with request id and a hashed user id; platform log search | |
| Database | Provider metrics: connections, disk, CPU | Disk above 80%, connections above 80% of limit |
| Backups | Job reports success/age | Last good backup older than 26 h |
| Business | Counts from the DB (signups, imports, active users) on the admin page | |

Do not add third-party session-replay or ad analytics inside the app (it renders financial data). Use a privacy-friendly analytics tool on the marketing site only.

## 9. Backup and disaster recovery

- **Postgres:** provider PITR (RPO about 5 minutes within the retention window) plus a **nightly logical dump** to a *separate* R2 bucket in a different account/credentials, 30-day lifecycle, so one stolen credential or one mistaken `DROP` cannot take out both the data and its backups.
- **R2:** enable object versioning on the screenshots bucket and a 30-day lifecycle on noncurrent versions; orphan cleanup never hard-deletes (see doc 04).
- **Restore drill:** restore the latest dump into staging and run the app against it, quarterly and before launch. A backup that has never been restored is a hope, not a backup.
- **Runbook:** a short `docs/runbooks/restore.md` written during M5.
- **Targets:** RPO 5 minutes, RTO 4 hours (doc 01).

## 10. Cost at beta (monthly, approximate)

| Item | Estimate | Confidence |
|---|---|---|
| Production web (2 GB) + Postgres | $25 to 45 | Third-party prices; verify |
| Staging web + small Postgres | $10 to 15 | Same; or skip a permanent staging and use a temporary one |
| R2 | about $0 under 10 GB, then $0.015 per GB | Vendor page |
| Cloudflare, Sentry free tier, hosted IdP free tier | $0 | IdP free tiers advertise 25,000 MAU; verify |
| Transactional email | $0 on a free tier | Unverified |
| Domain | about $1 | |
| GitHub Pro (optional but recommended) | a few dollars | Unverified |
| **Total** | **about $35 to 65** | Right at the $60 default ceiling |

## 11. Scale path and what I would revisit

| Trigger | Revisit |
|---|---|
| Sustained 300+ concurrent sessions, or memory pressure | Second NiceGUI instance with sticky sessions and Redis ([NiceGUI](https://nicegui.io/documentation/storage)); or move heavy pages to FastAPI + a SPA |
| Dashboard slower than 1.5 s p95 | Push aggregation into SQL; cache per-user summary rows |
| Need for native mobile app or public API | Promote FastAPI routers to a versioned JSON API with API tokens |
| Screenshot volume above ~1 TB | Lifecycle to Infrequent Access; thumbnails only for old data |
| Revenue above the point where PaaS cost matters | Move to a VPS with Coolify or Kubernetes-lite; the Docker image and Postgres port unchanged |
| EU customer base | EU region for DB and bucket; DPA with each subprocessor |
