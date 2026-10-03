# Trading Journal: launch design

**Status:** Draft v2, 2026-10-02. v2 pivots the UI from NiceGUI to a **FastAPI JSON API with a React and TypeScript SPA** ([ADR-010](06-decisions.md)) and adds an operator back end ([ADR-012](06-decisions.md)). This revision changes documents only.
**Goal:** turn the local, single-user trading journal into a hosted, multi-user product people can sign up for.

## Documents

| # | Doc | Answers |
|---|---|---|
| 01 | [Requirements](01-requirements.md) | What must it do, how big, what constraints |
| 02 | [Architecture](02-architecture.md) | Components, hosting, GitHub + CI/CD, ops, cost |
| 03 | [Data and accounts](03-data-and-accounts.md) | Multi-tenancy, schema changes, auth and sessions, plans and codes, files, migration |
| 04 | [Security](04-security.md) | Threat model, findings in today's code, launch gate |
| 05 | [Features and roadmap](05-features-and-roadmap.md) | Feature list by priority, admin back end, spikes, milestones, risks, M0 outcome |
| 06 | [Decisions](06-decisions.md) | ADRs: options, trade-offs, what would change my mind |
| 07 | [Front end and API](07-frontend-and-api.md) | API contract, React tooling, page-by-page rebuild list, admin API, stale-assumption audit |
| 08 | [Spike prep research](08-spike-prep-research.md) | Identity providers, payment-provider policies and Render facts, checked on the vendors' pages |

## TL;DR

1. **The domain code is worth keeping; the data layer and the UI are what change.** Parsing, trade grouping and finance math (`parser.py`, `grouping.py`, `trades.py`, `finance.py`, `timeutil.py`, `symbols.py`, `models.py`) stay in Python, unchanged, with their tests; money stays exact `Decimal`. The data layer is ported to Postgres, and the NiceGUI UI is rebuilt as a React SPA.
2. **Today the app has no concept of a user.** No `user_id` anywhere, a process-wide filter singleton, global UNIQUE constraints, unauthenticated screenshot URLs. Deploying it as-is would expose everyone's data to everyone.
3. **Recommended shape:** a stateless **FastAPI JSON API** over the domain modules and a **Vite + React + TypeScript SPA**, built into one image and served on **one origin**; managed Postgres with **row-level security (RLS)**; private S3-compatible storage (Cloudflare R2) for screenshots; a **hosted identity provider** (sign-in via OIDC) with our own session cookie; one container on a PaaS (Render recommended) from GitHub; an **operator back end** that cannot read user content.
4. **Tenant isolation is enforced by the database, not by remembering a `WHERE user_id = ?` in ~100 SQL statements.** That is the single most important design choice. The admin database role gets the same treatment: it is *granted* nothing on trade, note or screenshot tables.
5. **Never take broker credentials.** CSV upload only. This removes the largest class of liability. It also matches reality: Tradovate API access requires a live funded account and is not available for prop-firm evaluation accounts ([source](https://vendor-support.ninjatrader.com/s/article/Tradovate-API-Access)).
6. **Order of work:** GitHub and CI foundations (done), then spikes and front-end foundations, then the multi-tenant core, the API layer and the SPA rebuild, then auth, hardening and deploy. Run an **invite-only free beta** while finishing product features, billing and the admin back end. Rough size, M0 excluded: **about 430 to 650 hours to private beta and 650 to 1,000 to public launch**. That is roughly double the NiceGUI plan (200 to 300 and 330 to 525): the pivot buys horizontal scaling, a normal CSP and full UI control, and costs the rebuild.

## What I found in the current code

Verified by reading the source; line references are to the files in `backend/tradejournal/`.

| Area | Today | Consequence |
|---|---|---|
| Users | None. `db.py` schema has no user column; 12 tables hold tenant data | Add `user_id` + RLS to all 12 |
| UI | 2,752 lines of NiceGUI in `ui/` (17 files), reaching the database at about 45 `open_db()` sites | Rebuilt as a SPA over an API (ADR-010); `ui/` is the behavioral reference until parity, then deleted |
| Global state | `filters = FilterState()` in `ui/state.py:70`, shared by every page and visitor | User A's filters would show to user B. Becomes URL state in the SPA; the API keeps no per-user state |
| Settings | Global key/value table (`settings.py`) | Becomes per-user `user_settings` |
| Uniqueness | `accounts.name`, `tag_groups.name`, `fill_pairs(buy_fill_id, sell_fill_id)` are globally UNIQUE | Collide across users and leak that another user's data exists. Scope per user |
| Screenshots | Served from a public static route `/screenshots` (`ui/app.py:39`), no auth | Replace with private bucket + short-lived signed URLs |
| Orphan cleanup | `remove_orphan_files` deletes any file not in the DB, run at every startup (`ui/app.py:38`) | Destructive on shared storage or after a DB restore. Must become a per-user, delayed, dry-run-first job |
| Notes | Raw HTML from the Quasar editor stored as-is (`journal.py` `clean_notes` only checks emptiness) | Stored XSS the moment notes are rendered to anyone but the author. Sanitize with an allow-list on the server and again when rendering |
| Uploads | No `max_file_size` on either `ui.upload` (`trade_detail.py:269`, `pages/imports.py:236`); the 25 MB check runs after the whole file is in memory (`journal.py:286`); CSV parsed whole by pandas | Memory-exhaustion DoS. Set limits at every layer of the new upload routes |
| DB connections | `db.connect()` runs migrations on every connection (`db.py:247`); one connection per operation | Migrations must be a release step; need a connection pool |
| "Today" | `date.today()` uses the server's clock (`ui/state.py:53`) | Wrong day for users in other timezones. The API resolves date presets in the user's timezone |
| Dependencies | `requirements.txt` had floor versions and `pytest` in the runtime list | **Done in M0:** `uv.lock` with hashes, a generated `requirements.txt`, dev tools in a dev group. npm joins in M1 |
| Version control | There was none | **Resolved in M0:** private repo, CI green, tag `v0.1.0-local` |
| Data volume | Your journal: 23 trades, 28 fill pairs, 14 screenshots (~110 KB each), 8 accounts | Migrating your own data is trivial; scale math lives in doc 01 |

What is already good and should be kept: Decimal-everywhere money math, UTC timestamps, content-sniffed image uploads (no SVG), transactional imports with dry-run previews and undo, random screenshot names, a numbered migration list, 16 test files (210 tests) plus real fixtures. The tests are the safety net for the Postgres port.

## Decisions at a glance

Full reasoning and "what would change my mind" in [06-decisions.md](06-decisions.md).

| # | Decision | Recommendation |
|---|---|---|
| D1 | UI stack | **FastAPI JSON API + Vite/React/TypeScript SPA** (ADR-010). *Supersedes "keep NiceGUI"* |
| D2 | Database and tenancy | **Postgres, shared schema, `user_id` + FORCE RLS + composite FKs** |
| D3 | Authentication | **Hosted IdP via OIDC**, own opaque server-side session; shortlist and spike before choosing; needs an MFA claim for admin |
| D4 | File storage | **Cloudflare R2**, private bucket, per-user key prefix, presigned GET |
| D5 | Hosting | **Render**: one container holding the API and the built SPA, Docker so it is portable. The marketing site stays on Cloudflare Pages |
| D6 | Source control and CI/CD | **Private GitHub repo**, GitHub Actions CI (plus a front-end job), host auto-deploys gated on CI |
| D7 | Billing | Decide model first; lean to a merchant-of-record provider; **apply for the account early** |
| D8 | Local-first mode | **Freeze it as a tag**; SaaS only going forward |
| D9 | Same-origin session cookie and CSRF | **SPA served by the API container on one origin**; `__Host-` cookie; four CSRF layers; no CORS (ADR-011) |
| D10 | Operator back end | **CLI-granted admin, fresh MFA, database role with no grants on tenant tables, computed plan with overrides, grant and discount codes, append-only audit** (ADR-012) |

## Assumptions I made (correct me)

- The product is for **prop-firm futures traders** importing **Tradovate Performance CSVs**, with the prop-account lifecycle (evaluation, funded, payouts, costs) as the differentiator.
- **Solo developer**, Windows machine, small infrastructure budget (about $25 to $60 per month at beta). You have **some React experience and are new to TypeScript**.
- Most users are in the **US**; no formal compliance regime (HIPAA, SOC 2) is required at launch.
- Launch is invite-only free beta first, then paid.
- The rebuilt UI reproduces today's screens and look (dark and light) at beta, using Tailwind and shadcn/ui rather than a redesign; billing, calendar, reports and the admin back end come after beta.
- You chose a Vite SPA over Next.js after the checks recorded in ADR-010, and one origin over Pages plus a proxy (ADR-011).

## Questions only you can answer

Each has a default; I will proceed on the defaults until you say otherwise.

1. **Business model.** Free forever, paid, or free beta then paid? *Default: free invite-only beta, then free tier + paid Pro.* **Decided 2026-10-03: the default.**
2. **Legal entity.** Personal or an LLC? It affects the payment-provider account, Terms of Service and tax. *Default: personal for beta; talk to an accountant/lawyer before taking money.* **Decided 2026-10-03: the default (personal for the beta).**
3. **Name and domain.** "Trading Journal" is generic. The domain drives cookies, email sending and branding. *Default: pick one before M5.* **Decided 2026-10-03: the product is called Roundturn.** A futures term for one complete trade, which is what the app builds from fill pairs. On 2026-10-03 the registries showed roundturn.app, .io, .co, .dev, .net and getroundturn.com unregistered (roundturn.com is taken), and a web search found no existing product with the name. The owner registered **roundturn.app** on 2026-10-03. Not yet done: a trademark search. The word is a common industry term, so it is weak as a trademark and shares search results with glossary pages. The repository keeps the name trading-journal-saas.
4. **Where are your users?** US-only, or EU too (GDPR, EU data region)? *Default: US region, GDPR-ready deletion and export.* **Decided 2026-10-03: the default.**
5. **Hours per week.** Turns the effort estimates into a calendar. *Default: part-time, about 15 h/week, so roughly 7 to 10 months to private beta and 10 to 15 months to public launch.* **Not answered: the default stands.**
6. **Budget ceiling** per month for infrastructure. *Default: $60.* **Not answered: the default stands.**
7. **Schedule appetite.** If 7 to 10 months to beta is too long, the cheapest levers are in doc 05 section 3 (a reduced settings UI at beta saves about 30 to 50 hours).

## Sources and verification status

Checked on the web during this session:

- Tradovate API needs a live funded account with a $1,000 minimum and a paid add-on; prop-firm and evaluation accounts are excluded: [NinjaTrader vendor support](https://vendor-support.ninjatrader.com/s/article/Tradovate-API-Access), [Tradovate forum](https://community.tradovate.com/t/prop-account-api-access/10430).
- Render Postgres point-in-time recovery: 3-day window on Hobby, 7-day on Pro; free-plan databases get none: [Render docs](https://render.com/docs/postgresql-backups), [changelog](https://render.com/changelog/added-point-in-time-recovery-to-all-paid-database-instances).
- Hosting price ranges (Railway about $10 to 15, Render about $21 to 34 for web + Postgres + worker; Hetzner VPS about 4 EUR): [Render comparison](https://render.com/articles/railway-vs-fly-io), [techsy](https://techsy.io/en/blog/railway-vs-render-vs-fly-io). These are third-party summaries; confirm on the vendors' pages before buying.
- GitHub Free: branch protection and rulesets are not available on **private** repos: [rulesets docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets), [branch protection docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/managing-a-branch-protection-rule). 2,000 Actions minutes per month on the free plan.
- Cloudflare R2: 10 GB free, then $0.015 per GB-month, zero egress: [R2](https://www.cloudflare.com/products/r2/).
- Hosted auth plans, read on the vendors' pricing pages on 2026-10-02 (details in [doc 08](08-spike-prep-research.md)): **Auth0's free plan has 25,000 MAU but no MFA**; **Kinde's free plan has 10,500 MAU with authenticator-app MFA, Google login and a custom domain**; WorkOS AuthKit's first million MAU are free but a custom domain costs $99 a month; Clerk's MFA is paid. [Auth0](https://auth0.com/pricing), [Kinde](https://kinde.com/pricing/), [WorkOS](https://workos.com/pricing), [Clerk](https://clerk.com/pricing), [Supabase](https://supabase.com/pricing). Re-check at signup.
- **Added for the pivot.** Next.js static export drops rewrites, middleware, cookies, redirects and headers, and needs build-time params for dynamic routes: [static exports guide](https://nextjs.org/docs/app/guides/static-exports). A strict Next.js CSP needs nonces (forcing dynamic rendering) or an experimental hash-based option: [CSP guide](https://nextjs.org/docs/app/guides/content-security-policy).
- Pydantic documents `Decimal` as a string in serialization schemas but `number | string` in validation schemas: [Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/), [FastAPI discussion](https://github.com/fastapi/fastapi/discussions/14264).
- pnpm blocks dependency lifecycle scripts by default since v10 and offers `minimumReleaseAge` since v10.16; a past advisory bypassed the script block: [pnpm settings](https://pnpm.io/10.x/settings), [Socket](https://socket.dev/blog/pnpm-10-0-0-blocks-lifecycle-scripts-by-default), [advisory](https://github.com/pnpm/pnpm/security/advisories/GHSA-379q-355j-w6rj). Corepack ships with Node 24 LTS but is dropped from Node 25 and later: [Socket](https://socket.dev/blog/node-js-tsc-votes-to-stop-distributing-corepack).
- Package versions in doc 07 were read from the npm registry on 2026-10-02 (for example Vite 8.3, React 19.3, TypeScript 7.0, TanStack Router 1.x and Query 5, pnpm 12). **Spike S1 then confirmed they work together, except TypeScript 7, which breaks `openapi-typescript` and `typescript-eslint`: pin 6.0.x.**

From my own knowledge, **not verified here** (flagged where used): whether payment providers' APIs create discount codes (their acceptable-use rules were read, see doc 08); GitHub Pro price; whether Kinde, the front-runner, really emits `amr` and `acr` and honors step-up in a live tenant (its documentation for that is an unmerged docs pull request); whether `img-src` must allow the R2 host for the screenshot redirect; Cloudflare Pages Functions and Workers quotas (only relevant to the rejected proxy option); Render's memory tiers and prices (third-party figures; its zero-downtime deploys and Postgres versions were checked); Dependabot's handling of `pnpm-lock.yaml`; CSP violation-report delivery in real browsers (it failed from the embedded browser in S1); `openapi-fetch` stability (it is pre-1.0).

The earlier NiceGUI findings (user storage, one process per instance, `request` injection in `@ui.page`, `unsafe-eval`, "a few hundred concurrent users on one instance") belong to the superseded ADR-001 and no longer drive any decision.

I am not a lawyer. Terms of Service, Privacy Policy and the financial disclaimer need a professional's review before you take payments.
