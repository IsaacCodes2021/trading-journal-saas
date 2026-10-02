# Trading Journal: launch design

**Status:** Draft v1, 2026-10-02. Design only; no code has been changed.
**Goal:** turn the local, single-user trading journal into a hosted, multi-user product people can sign up for.

## Documents

| # | Doc | Answers |
|---|---|---|
| 01 | [Requirements](01-requirements.md) | What must it do, how big, what constraints |
| 02 | [Architecture](02-architecture.md) | Components, hosting, GitHub + CI/CD, ops, cost |
| 03 | [Data and accounts](03-data-and-accounts.md) | Multi-tenancy, schema changes, auth, billing, files, migration |
| 04 | [Security](04-security.md) | Threat model, findings in today's code, launch gate |
| 05 | [Features and roadmap](05-features-and-roadmap.md) | Feature list by priority, milestones, risks, step 1 (GitHub) |
| 06 | [Decisions](06-decisions.md) | ADRs: options, trade-offs, what would change my mind |

## TL;DR

1. **The domain code is worth keeping; the data layer and UI plumbing are not multi-user.** Parsing, trade grouping and finance math (`parser.py`, `grouping.py`, `trades.py`, `finance.py`, `timeutil.py`) are pure, tested and untouched by this plan. Everything that touches storage or global state needs work.
2. **Today the app has no concept of a user.** No `user_id` anywhere, a process-wide filter singleton, global UNIQUE constraints, unauthenticated screenshot URLs. Deploying it as-is would expose everyone's data to everyone.
3. **Recommended shape:** keep NiceGUI, move SQLite to managed Postgres with **row-level security (RLS)**, move screenshots to private S3-compatible storage (Cloudflare R2), use a **hosted identity provider** (sign-in via OIDC) with our own session cookie, deploy one container to a PaaS (Render recommended) from GitHub.
4. **Tenant isolation is enforced by the database, not by remembering a `WHERE user_id = ?` in ~100 SQL statements.** That is the single most important design choice.
5. **Never take broker credentials.** CSV upload only. This removes the largest class of liability. It also matches reality: Tradovate API access requires a live funded account and is not available for prop-firm evaluation accounts ([source](https://vendor-support.ninjatrader.com/s/article/Tradovate-API-Access)).
6. **Order of work:** GitHub + CI foundations, then spikes, then multi-tenant core, then auth, then hardening and deploy. Run an **invite-only free beta** while finishing product features and billing. Rough size: 8 to 11 focused weeks to private beta, 14 to 20 to public launch.

## What I found in the current code

Verified by reading the source; line references are to the files in `backend/tradejournal/`.

| Area | Today | Consequence |
|---|---|---|
| Users | None. `db.py` schema has no user column; 12 tables hold tenant data | Add `user_id` + RLS to all 12 |
| Global state | `filters = FilterState()` in `ui/state.py:70`, shared by every page and visitor | User A's filters would show to user B. Must become per-tab state |
| Settings | Global key/value table (`settings.py`) | Becomes per-user `user_settings` |
| Uniqueness | `accounts.name`, `tag_groups.name`, `fill_pairs(buy_fill_id, sell_fill_id)` are globally UNIQUE | Collide across users and leak that another user's data exists. Scope per user |
| Screenshots | Served from a public static route `/screenshots` (`ui/app.py:39`), no auth | Replace with private bucket + short-lived signed URLs |
| Orphan cleanup | `remove_orphan_files` deletes any file not in the DB, run at every startup (`ui/app.py:38`) | Destructive on shared storage or after a DB restore. Must become a per-user, delayed, dry-run-first job |
| Notes | Raw HTML from the Quasar editor stored as-is (`journal.py` `clean_notes` only checks emptiness) | Stored XSS the moment notes are rendered to anyone but the author. Sanitize with an allow-list |
| Uploads | No `max_file_size` on either `ui.upload` (`trade_detail.py:269`, `pages/imports.py:236`); the 25 MB check runs after the whole file is in memory (`journal.py:286`); CSV parsed whole by pandas | Memory-exhaustion DoS. Set limits at every layer |
| DB connections | `db.connect()` runs migrations on every connection (`db.py:247`); one connection per operation | Migrations must be a release step; need a connection pool |
| "Today" | `date.today()` uses the server's clock (`ui/state.py:53`) | Wrong day for users in other timezones |
| Dependencies | `requirements.txt` has floor versions (`pandas>=2.2`, `nicegui>=3.17,<4`) and `pytest` in the runtime list | Non-reproducible builds. Add a lock file with hashes; split dev deps |
| Version control | **None.** No `.git`, and Git is not installed on this machine (also no `gh`, no Docker) | This is step 1 |
| Housekeeping | Stray zero-byte file `backend/=ro` (accidental shell redirect) | Delete before the first commit |
| Data volume | Your journal: 23 trades, 28 fill pairs, 14 screenshots (~110 KB each), 8 accounts | Migrating your own data is trivial; scale math lives in doc 01 |

What is already good and should be kept: Decimal-everywhere money math, UTC timestamps, content-sniffed image uploads (no SVG), transactional imports with dry-run previews and undo, random screenshot names, a numbered migration list, 16 test files plus real fixtures. The tests are the safety net for the Postgres port.

## Decisions at a glance

Full reasoning and "what would change my mind" in [06-decisions.md](06-decisions.md).

| # | Decision | Recommendation |
|---|---|---|
| D1 | UI stack | **Keep NiceGUI** for launch; put a seam in so a later rewrite is possible |
| D2 | Database and tenancy | **Postgres, shared schema, `user_id` + FORCE RLS + composite FKs** |
| D3 | Authentication | **Hosted IdP via OIDC**, own opaque server-side session; shortlist and spike before choosing |
| D4 | File storage | **Cloudflare R2**, private bucket, per-user key prefix, presigned GET |
| D5 | Hosting | **Render** (web service + managed Postgres), Docker so it is portable |
| D6 | Source control and CI/CD | **Private GitHub repo**, GitHub Actions CI, host auto-deploys gated on CI |
| D7 | Billing | Decide model first; lean to a merchant-of-record provider; **apply for the account early** |
| D8 | Local-first mode | **Freeze it as a tag**; SaaS only going forward |

## Assumptions I made (correct me)

- The product is for **prop-firm futures traders** importing **Tradovate Performance CSVs**, with the prop-account lifecycle (evaluation, funded, payouts, costs) as the differentiator.
- **Solo developer**, Windows machine, small infrastructure budget (about $25 to $60 per month at beta).
- Most users are in the **US**; no formal compliance regime (HIPAA, SOC 2) is required at launch.
- Launch is invite-only free beta first, then paid.
- "Start with connecting GitHub" means: put the code in a GitHub repo and wire CI and deployment to it. If you meant "Sign in with GitHub" for end users, tell me; that is a different (and smaller) feature.

## Questions only you can answer

Each has a default; I will proceed on the defaults until you say otherwise.

1. **Business model.** Free forever, paid, or free beta then paid? *Default: free invite-only beta, then free tier + paid Pro.*
2. **Legal entity.** Personal or an LLC? It affects the payment-provider account, Terms of Service and tax. *Default: personal for beta; talk to an accountant/lawyer before taking money.*
3. **Name and domain.** "Trading Journal" is generic. The domain drives cookies, email sending and branding. *Default: pick one before M3.*
4. **Where are your users?** US-only, or EU too (GDPR, EU data region)? *Default: US region, GDPR-ready deletion and export.*
5. **Hours per week.** Turns the effort estimates into a calendar. *Default: part-time, about 15 h/week, so roughly 5 to 7 months to public launch.*
6. **Budget ceiling** per month for infrastructure. *Default: $60.*

## Sources and verification status

Checked on the web during this session:

- NiceGUI user storage, one process per instance, sticky sessions + Redis for multiple instances: [storage docs](https://nicegui.io/documentation/storage), [multi-worker discussion](https://github.com/zauberzeug/nicegui/discussions/4483), [load balancer discussion](https://github.com/zauberzeug/nicegui/discussions/1539).
- Tradovate API needs a live funded account with a $1,000 minimum and a paid add-on; prop-firm and evaluation accounts are excluded: [NinjaTrader vendor support](https://vendor-support.ninjatrader.com/s/article/Tradovate-API-Access), [Tradovate forum](https://community.tradovate.com/t/prop-account-api-access/10430).
- Render Postgres point-in-time recovery: 3-day window on Hobby, 7-day on Pro; free-plan databases get none: [Render docs](https://render.com/docs/postgresql-backups), [changelog](https://render.com/changelog/added-point-in-time-recovery-to-all-paid-database-instances).
- Hosting price ranges (Railway about $10 to 15, Render about $21 to 34 for web + Postgres + worker; Hetzner VPS about 4 EUR): [Render comparison](https://render.com/articles/railway-vs-fly-io), [techsy](https://techsy.io/en/blog/railway-vs-render-vs-fly-io). These are third-party summaries; confirm on the vendors' pages before buying.
- GitHub Free: branch protection and rulesets are not available on **private** repos: [rulesets docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets), [branch protection docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule). 2,000 Actions minutes per month on the free plan.
- Cloudflare R2: 10 GB free, then $0.015 per GB-month, zero egress: [R2](https://www.cloudflare.com/products/r2/).
- Hosted auth free tiers advertised at 25,000 monthly active users (Auth0, Kinde): [Auth0 pricing](https://auth0.com/pricing), [Kinde comparison](https://www.kinde.com/comparisons/top-10-authentication-providers-for-b2c-software-2026/). Re-check at signup.

From my own knowledge, **not verified here** (flagged where used): payment providers' acceptable-use rules for trading-adjacent products, GitHub Pro price, NiceGUI's `request` injection in `@ui.page`, whether NiceGUI needs `unsafe-eval` in a CSP, "a few hundred concurrent users on one NiceGUI instance" (a forum estimate; load-test it in M8).

I am not a lawyer. Terms of Service, Privacy Policy and the financial disclaimer need a professional's review before you take payments.
