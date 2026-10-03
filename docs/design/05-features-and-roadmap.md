# 05. Features and roadmap

*Revised 2026-10-02 for ADR-010 to ADR-012: the UI is rebuilt as a React SPA over a FastAPI API, and an operator back end is added. The milestones are renumbered (section 3 maps old to new) and the estimates are re-done with the UI rebuild included.*

## 1. Feature set by tier

Requirement IDs refer to doc 01. Sizes: **S** under 10 h, **M** 10 to 30 h, **L** 30 to 80 h, **XL** more.

### Tier 0: platform (nothing ships without these)

| Feature | Req | Size | Notes |
|---|---|---|---|
| Postgres port + test harness | F-T1 | L | Doc 03 section 9. Tests first |
| Tenancy: RLS, composite FKs, `tenant_session`, `user_settings` | F-T1, T2, T4 | L | |
| **FastAPI JSON API over the domain modules**: about 60 routes, OpenAPI, problem+json, `Money` type, generated two-tenant and CSRF tests | F-T1, T3 | L | Doc 07 sections 2 and 5 |
| **SPA foundation**: Vite, React, TypeScript scaffold, design tokens, API client generated from OpenAPI, front-end CI | | L | Doc 07 section 3 |
| **SPA parity rebuild**: shell and URL filters, dashboard, trade log, trade detail, imports, accounts and settings | F-T3 | XL | Doc 07 section 5; `ui/` is the behavioral reference and is deleted when this exits |
| Sign up, log in, reset, verify (hosted IdP) with the SPA session and CSRF flow | F-A1, A2 | M | Doc 03 section 4 |
| Sessions, logout everywhere, audit log | F-A2 | M | |
| Optional TOTP MFA | F-A3 | S | Mostly IdP configuration |
| Profile page: sessions, email and password links, plan | F-A4 | S | |
| Data export and account deletion (with jobs) | F-A5, A6 | M | |
| Invite codes for the beta (hashed, multi-use, optional bundled grant) | F-A7 | S | |
| Terms and Privacy acceptance and versioning | F-A8 | S | |
| Screenshots to R2, quotas, Pillow pipeline | F-T1 | M | |
| Hardening: sanitization, limits, strict CSP, headers, rate limits | doc 04 | M | |
| Onboarding | F-P1 | M | **Timezone confirmation ships with the beta (inside M4)**, because a wrong timezone silently corrupts imports; the full guided flow comes in M8 |
| Marketing site, pricing, docs/FAQ, support email, legal pages | F-B4 | M | Static, on Cloudflare Pages; waitlist page goes up early (M1) |
| CI/CD, staging, backups, restore drill, monitoring, runbooks | doc 02 | L | |

### Tier 1: product completeness (expected at public launch)

| Feature | Req | Size | Notes |
|---|---|---|---|
| Calendar heatmap + day view with day notes | F-P3 | L | `day_notes` table exists; README Phase 3; new SPA page and API routes |
| Reports: symbol, time of day, weekday, hold time, tag, account | F-P4 | L | Reuse `finance.py` aggregators behind new stats routes |
| Demo/sample data mode | F-P2 | M | A synthetic dataset, never real user data |
| Prop rule tracking | F-P5 | L to XL | See note A |
| Plans, entitlements, checkout, portal, webhooks | F-B1..B3 | L | Needs the payment account approved first |
| **Admin back end (operator)** | F-O1..O8 | L | See below; replaces the old "admin aggregates page" |
| Second import source | F-P7 | L | See note B; pick from beta demand |

### Admin back end (operator)

You asked for these; the design is ADR-012 (decisions), doc 03 (tables, plan resolution) and doc 07 section 6 (endpoints). Milestone M10, about 55 to 85 h.

| Feature | What it does | Req |
|---|---|---|
| Admin identity | `is_admin` **and** recent MFA; `is_admin` set only by the CLI, never the UI or API | F-O1 |
| User management | Search; view account metadata, effective plan and its source, usage counts; suspend and unsuspend; revoke sessions; trigger an export; schedule or cancel deletion | F-O2 |
| Plan override | Plan, optional expiry, required reason; wins over the billing mirror; survives webhooks and the nightly reconcile because the effective plan is computed, not stored | F-O3 |
| Discount codes | Created through the payment provider's API; listed and deactivated from the admin | F-O4 |
| Grant codes and beta invites | Local codes giving N days of a plan, one redemption per user; invites extended for multi-use beta codes with an optional bundled grant | F-O5 |
| Audit | Every admin mutation and every user-detail view is written to the append-only `audit_log`; viewer with filters | F-O6 |
| Content-blind | The admin DB role has no SELECT on tenant tables; no impersonation; a CI test proves it | F-O7 |
| Overview | Signups, active users, plan counts, import failure rate | F-O8 |

### Tier 2: after launch (each has a gate)

| Feature | Gate before building |
|---|---|
| Insights from tags (mistake cost, expectancy by setup, streaks) | Enough beta data to know which insights users act on |
| Daily plan / pre-market and weekly review journaling | Beta feedback |
| Shareable read-only trade/day links | Own threat model: sanitized rendering, unguessable tokens, revocation, no PII, per-link expiry |
| More import sources | Real sample files from real users |
| Broker API sync (live accounts only) | Security re-review: encrypted token storage (envelope encryption, KMS), scope minimization |
| AI-assisted review of notes | Consent design, data-processing agreement with the model provider, opt-in, no training on user data, descriptive not prescriptive output |
| Mobile app, public API | The JSON API and OpenAPI document exist from day one; needs API tokens, a versioning policy and per-token rate limits |
| Team/coach accounts | Role model and per-share access grants (RLS policies by membership) |

**Deliberately not built before launch:** social feeds, leaderboards, custom dashboard builders, options/equities, multi-currency, native apps. They add scope and attack surface without proving the core.

### Note A. Prop rule tracking (differentiator, but accuracy-sensitive)

- Schema already carries `max_drawdown`, `trailing_type` (intraday/eod), `trail_lock_*`, `daily_loss_limit`, `profit_target` on `accounts`.
- Model firm rules as **versioned data** (a file in the repo keyed by firm and plan, with an effective date), not hardcoded branches, because firms change rules.
- **Hard limit:** an intraday trailing drawdown depends on the *unrealized* peak during a trade, which the Performance CSV does not contain. From closed trades you can compute an end-of-day trailing drawdown exactly and an intraday one only approximately. Say so in the UI and keep the "informational, may differ from the firm's official figure" disclaimer.
- Consistency and minimum-trading-day rules: start with the ones you personally track across Apex, Tradeify, Lucid and similar, and add on demand.

### Note B. Import adapters (the biggest functional design item)

Today the parser consumes Tradovate's **Performance** export, where each row is already a matched buy/sell **fill pair**; other platforms export raw **executions** or orders that must be paired.

- Introduce an adapter interface: `detect(head: bytes) -> confidence`, `parse(data, tz) -> ParseResult`. The existing parser becomes `TradovatePerformanceAdapter`.
- Add a canonical `executions → fill pairs` pairing step (FIFO per account and symbol) for sources that give executions; the existing `grouping.py` then works unchanged on the resulting pairs.
- **Schema decision to make now, because it is cheap now and painful later:** replace `buy_fill_id/sell_fill_id` integers with `source text`, `buy_ref text`, `sell_ref text`, uniqueness `(user_id, source, buy_ref, sell_ref)`. Tradovate IDs are about 36 billion, so they already exceed 32 bits; other platforms may use strings.
- **Do not build adapters speculatively.** Ask beta users which platform they use and collect real sample files first. Today non-Performance files are rejected with an explanation, which is the right default.

## 2. Spikes (de-risk before committing)

Each is a day or two, ends in a short written conclusion appended to doc 06, and may change a decision.

| Spike | Question | Success looks like |
|---|---|---|
| **S1** SPA and cookie auth against FastAPI (**done 2026-10-02**: conclusions under ADR-010 and ADR-011 in doc 06; the TanStack Table v8 or v9 choice moves to M4) | Can FastAPI serve the built SPA with a history fallback and correct cache headers behind Cloudflare? Does the `__Host-` cookie flow (OIDC redirect, `next`) and the CSRF header flow, including multipart, work? Does a strict CSP survive Vite output, ECharts, Tiptap and Tailwind? Does the generated-types pipeline work with the `Money` type? Does the toolchain set (Vite 8, TypeScript 7, ESLint 10, Vitest 5, pnpm 12) install and run together? Pick TanStack Router or React Router, and TanStack Table v8 or v9 | A two-user toy: login redirect, isolated data, a typed client generated from OpenAPI, a CSRF-protected mutation, security headers, a passing Vitest test and a passing Playwright test |
| **S1b** IdP choice | Which provider meets the criteria in doc 03 section 4 with the least friction from Python, **including an MFA indicator in the ID token and step-up re-authentication for admin**? | A working login callback against the front-runner; notes on user export and deletion APIs; MFA claim confirmed |
| **S2** Postgres + RLS | Does the RLS/`tenant_session` design work with psycopg's pool, and how painful is the port? | `accounts.py` and `journal.py` ported, their tests green on Postgres, a two-tenant test passing |
| **S3** Files | Does a presigned R2 URL work as an `<img src>` through the `/files/{id}` redirect under the real CSP, and what must `img-src` allow? Does the Pillow pipeline keep screenshot quality at acceptable size? | Upload, thumbnail, display under the strict CSP, delete, orphan report; a decision between redirecting and streaming through the API |
| **S4** Deploy | One image (API + SPA) on Render behind Cloudflare: caching of `/assets/*`, the SPA fallback, deploy behavior, memory and latency under an HTTP load test; marketing site on Cloudflare Pages. (If ADR-011 is reversed to Pages plus an edge proxy, this becomes a two-piece deploy and gains the proxy and quota questions.) | Container reachable at the real domain; resident memory and p95 latency measured at 50 simulated users |
| **S5** Payments (non-code; the policies were read, see doc 08) | Will a provider approve a trading-journal product, and does its API create discount codes? | Application submitted and answered |

## 3. Roadmap

Effort is **hours of focused, AI-assisted work including tests and review**. These are my estimates and uncalibrated: **recalibrate at the end of M2 and again when the first rebuilt page ships in M4**, then rescale the rest. Build M3 and M4 as **vertical slices** (a page and its routes together, starting with the trade log) rather than all of the API first.

| M | Milestone | Hours | Exit criteria |
|---|---|---|---|
| **M0** | **Foundations (GitHub first)** | **Done 2026-10-02** | Private repo, CI green on `main`, image builds in CI (summary in section 6) |
| **M1** | **Spikes and front-end foundations**: S1, S1b, S2, S3, S4, S5; Vite/React/TypeScript scaffold, pnpm, front-end CI job, OpenAPI codegen; waitlist page live | 40 to 60 | Decisions confirmed or amended in doc 06; scaffold CI green; payment application submitted; waitlist collecting emails (validates demand early) |
| **M2** | **Multi-tenant core and API skeleton**: Postgres port, tenancy, FastAPI app factory (`current_user`, `tenant_session`, errors, config), `user_settings`, `user_usage`, `source/ref` fill-pair columns, local-data migration script | 70 to 100 | All 16 existing test files green on Postgres; schema lint + two-tenant suite green; your data migrated and golden-checked; 10,000-trade benchmark recorded |
| **M3** | **API layer**: about 60 routes over the domain modules, OpenAPI, `Money` type, route tests with generated two-tenant and CSRF tests | 55 to 85 | Every current feature reachable through the API; drift check in CI; route-level two-tenant suite green |
| **M4** | **SPA parity rebuild**: shell, theme, URL filters, dashboard, trade log, trade detail (notes, tags, screenshots UI), imports, accounts and settings, timezone confirmation | 150 to 230 | Every `ui/` screen reproduced; component tests on the key ones; **`ui/` deleted** |
| **M5** | **Auth and accounts**: SPA login flow, sessions, CSRF, IdP and MFA, profile, export, deletion, audit log, invites | 55 to 80 | Doc 04 authentication gates ticked; end-to-end signup to dashboard on staging; Playwright smoke with a fake IdP |
| **M6** | **Files and hardening**: R2 pipeline, quotas, sanitization, limits, strict CSP and headers, rate limits, logging, Sentry | 30 to 45 | Doc 04 input, output and file gates ticked; ZAP baseline clean |
| **M7** | **Deploy and ops**: staging and production from one image, CI/CD, backups, restore drill, monitoring, runbooks, email domain | 30 to 50 | Restore drill passed; alerts fire in a test; runbooks written |
| | **Private beta (invite-only, free), 10 to 30 users** | | Collect feedback and sample files; watch activation and import-failure rate |
| **M8** | **Product completeness**: guided onboarding, demo mode, calendar and day view, reports (in parallel with beta) | 95 to 150 | Beta users reach the dashboard unaided; docs/FAQ written |
| **M9** | **Billing and legal**: plans, entitlements, checkout, webhooks, dunning, billing UI, legal review | 40 to 60 | A test subscription goes through the full lifecycle; lapse leads to read-only, never deletion |
| **M10** | **Admin back end**: operator API and pages, CLI, `app_admin` role, plan overrides, grant and discount codes, audit viewer | 55 to 85 | Doc 04 admin gates ticked |
| **M11** | **Launch readiness**: Playwright end-to-end suite, HTTP load test, security review including admin, penetration test (if budget), launch checklist, support process, status page | 30 to 50 | Checklist (`engineering:deploy-checklist`) green; load test meets doc 01 targets |
| | **Public launch** | | |

**Totals (M0 is already done):** about **430 to 650 hours to private beta** (M1 to M7) and about **650 to 1,000 hours to public launch** (M1 to M11).
At **15 h/week**: beta in roughly 7 to 10 months, public launch in 10 to 15 months. At 30 h/week, half that.

**Against roadmap v1** (NiceGUI; its headline was 200 to 300 hours to beta and 330 to 525 to launch, which are about 185 to 280 and 320 to 510 once the finished M0 is excluded), the pivot adds roughly **245 to 370 hours to beta and 330 to 485 to launch**. The increase is the API layer (M3), the SPA foundation and rebuild (parts of M1 and M4), and the SPA-specific parts of auth and hardening. The admin back end (M10) is new scope after beta rather than a consequence of the pivot.

**Levers if the schedule hurts**, in order of how little they cost the product: ship the beta with a reduced settings UI (the archive flow, ledger and tag manager simplified or later; about 30 to 50 h out of M4); keep reports and the calendar after beta (already so); build admin in two steps (overrides and codes first, metrics later).

```
M0 done -> M1 -> M2 -> M3 <-> M4 -> M5 -> M6 -> M7 --> BETA --> M9 -> M10 -> M11 --> LAUNCH
                                                         \--> M8 (parallel, fed by beta feedback) --/
S5 (payment approval) runs from M1 and gates M9
```

**Mapping from roadmap v1:** M0 to M0; M1 to M1 (plus front-end foundations); M2 to M2 (plus the API skeleton); M3 (auth) to **M5**; M4 (files and hardening) to **M6**; M5 (deploy and ops) to **M7**; M6 (product completeness) to **M8**; M7 (billing) to **M9**; M8 (launch readiness) to **M11**. New: M3 (API layer), M4 (SPA parity), M10 (admin).

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Payment provider declines a trading-adjacent product | Medium | High | Apply in M1; keep two providers on the shortlist; position as analytics, not advice |
| R2 | **SPA rebuild overruns** (the pivot roughly doubles the effort). *Replaces the old "NiceGUI limits found late" risk, which ADR-010 resolves* | High | High | Vertical slices page by page; `ui/` as the behavioral reference; the reduced-settings lever; recalibrate after M2 and at the first rebuilt page |
| R3 | Postgres port changes behavior subtly | Medium | High | Port the test harness first; golden-stats comparison on real data; run fixtures on both engines during the port |
| R4 | Tradovate changes the CSV format | Medium | Medium | Strict parser errors with a clear message; versioned fixtures; track import failure rate; keep sample files from beta users |
| R5 | Prop firms change rules; computed limits differ from official | High | Medium | Rules as versioned data; informational disclaimer; user can override per account |
| R6 | Cross-tenant leak | Low | **Critical** | Three layers (doc 03); lint and isolation tests in CI, including route-level; penetration test |
| R7 | Data loss | Low | **Critical** | PITR + separate nightly dump + versioned bucket; restore drills |
| R8 | Solo-developer bandwidth; scope creep | High | High | Hard tier boundaries; beta gating; time-box each milestone; GitHub Projects board; the schedule levers above |
| R9 | Legal exposure (financial disclaimer, privacy, tax) | Medium | High | Professional review of Terms and Privacy; merchant of record; disclaimers in-product |
| R10 | Weak demand | Medium | High | Waitlist in M1; invite-only beta measures activation and retention before billing work |
| R11 | Dev workstation compromise (Windows 10 is past end of support) | Low | High | Upgrade or enroll in extended updates; hardware-key 2FA; prod secrets not stored locally |
| R12 | TypeScript learning curve and a JavaScript toolchain with several new major versions | Medium | Medium | Strict types with generated API types; five code patterns drafted once (doc 07); confirm the tool set in M1 and fall back one major where a tool lags |
| R13 | npm supply-chain compromise | Medium | High | Doc 04 T7: lifecycle scripts blocked, release-age delay, lock files, few dependencies, strict CSP |
| R14 | API and SPA drift | Low | Medium | Types generated from OpenAPI and committed; CI fails on any diff; backward-compatible API for one release |
| R15 | Admin compromise or misuse | Low | **Critical** | ADR-012: CLI-only grant, fresh MFA, content-blind DB role, append-only audit |
| R16 | Money precision lost between Postgres, JSON and JavaScript | Low | High | Money as strings end to end; the `Money` schema type; CI test that no money field is `number`; one chart-conversion helper |

## 5. Test strategy (summary)

| Layer | What | Tooling |
|---|---|---|
| Unit (Python) | Parser, grouping, finance, timeutil | pytest (exists, 210 tests) |
| DB integration | All data modules on real Postgres | pytest + Postgres service (CI) / native install (local); per-test transaction rollback |
| **Tenant isolation** | Schema lint; two-tenant suite across domain functions; **a route-level suite generated from the OpenAPI document** (user B calls every route with user A's ids) | pytest, run on every PR |
| Auth flows | Callback validation, session expiry/rotation/revocation, MFA freshness, using a fake IdP | pytest |
| CSRF and headers | Every unsafe route with a missing or foreign `Origin` and with a missing token; CSP and security-header test; no CORS headers | pytest, generated from OpenAPI |
| API contract | OpenAPI snapshot; no money field typed `number`; admin schema has no content fields; generated types up to date | pytest and a CI diff |
| Webhooks | Signature verification, idempotency, out-of-order events; overrides survive them | pytest |
| Admin role | A test connects as `app_admin` and fails to read any tenant table; import-boundary test (tenant routes cannot use `system_session`, admin routes cannot use `tenant_session`) | pytest |
| Front-end unit and component | Components, hooks, the money and filter helpers | Vitest + React Testing Library; MSW for the network |
| End to end | Sign in, import, dashboard, trade detail, upload a screenshot, admin override | Playwright against the built image, Postgres and a fake IdP in CI; a smaller smoke run against staging |
| Security | XSS corpus against the API and the SPA, oversize uploads, gitleaks, `pip-audit`, `pnpm audit`, ZAP baseline | CI + M6 and M11 |
| Load | HTTP load (not websockets): a 5,000-row import, a 10,000-trade dashboard, about 100 concurrent users | k6 or Locust, in M11 |

The `engineering:testing-strategy` skill can turn this into a full plan when we reach M2.

## 6. M0 outcome (done 2026-10-02)

**Done.** Git 2.55.0 and the GitHub CLI 2.102.0 installed; private repo `IsaacCodes2021/trading-journal-saas`; commits carry the GitHub no-reply address; tag `v0.1.0-local` marks the last local-first version. Dependencies locked with `uv.lock` and a generated, hash-pinned `requirements.txt`; Python pinned to 3.12 (system Python on this machine is 3.14, which is untested). Ruff with its security rules, and a separate mechanical-reformat commit. CI on every push and pull request: lint and format, 210 tests, `requirements.txt` agrees with the lock, `pip-audit`, gitleaks (a pinned binary scanning full history), and a container build with an import smoke test. Dependabot alerts and updates are on. CI caught one real bug on its first run (a screenshot file name kept its Windows folders on Linux), which is fixed with tests.

**Deviations.** Docker is deferred (hardware virtualization is off, WSL is v1, about 11 GB free on C:), so CI builds the image. The gitleaks Action was replaced by the binary because the Action fails on a repository's first push.

**Not done.** GitHub Issues and the project board (they need your go-ahead), and branch protection (GitHub Pro).

**Amendment (2026-10-02):** the generated `requirements.txt` above was removed. Dependabot's `uv` updater edited it instead of `uv.lock` (pull requests #1 and #2 changed only that file), which broke pip resolution and the "agrees with the lock" check. The Dockerfile now installs with `uv sync --frozen` from a digest-pinned `uv` image, the launchers install `uv` into their environment, and CI exports the lock only to run `pip-audit`.

**Next:** M1.

## 7. Baseline benchmark (measured 2026-10-02, before any Postgres work)

`backend/benchmarks/baseline.py` generates seeded synthetic Tradovate exports and times the existing SQLite code. It is the "before" number for the M2 benchmark in section 3: re-run the same scenarios on the Postgres port and compare. Machine: Ryzen 5 3600, 16 GB, Windows 10, Python 3.12.1, SQLite 3.43.1, local SSD, one process, warm cache, no HTTP. Data: one account, 4 instruments, 20 trades a day, 10% of trades scaled in with a second fill pair (so 10,000 trades are 11,031 fill pairs).

| Scenario | Result | Target (doc 01) |
|---|---|---|
| Import one file of 5,506 rows into an empty journal | 1.22 s (parse alone 0.31 s) | under 10 s for 5,000 rows |
| Dry-run preview of that same file (all duplicates) | 0.37 s | none |
| Import one day (20 trades) into a 10,000-trade journal | 12 ms median | none |
| `load_trades`, all 10,000 trades | 390 ms median, 410 ms p95 | none |
| Summary, daily results, score and equity curve over those trades | 45 ms median | none |
| **Dashboard data (accounts, load, compute) at 10,000 trades** | **448 ms median, 458 ms p95** | p95 under 800 ms |
| `load_trades`, one month | 14 ms | none |
| `rebuild_account` (repair tool), 11,031 pairs | 16.1 s (about 1.5 ms a pair, linear) | none |
| `undo_import` of 2,743 rows | 80 ms | none |

**What this tells us** (measured numbers first, then inferences):

1. **Measured: the current code meets both targets with room to spare on a local database**, 8 times under on import and 1.8 times under on the dashboard. The margin is thin on the dashboard once the real costs are added.
2. **Measured: loading rows is 87% of the dashboard time (390 of 448 ms).** The compute step is cheap. Not profiled, but the likely cause is building 10,000 records from TEXT columns (about 11 `Decimal` and date parses each).
3. **Inference, not measured: the importer will miss its target on a network database unless it is rewritten to batch.** It runs about 3 statements per row (look up the pair, insert it, link it to the import), about 16,500 for this file. At 0.5 to 1 ms per round trip to a managed Postgres that is 8 to 16 s, over the 10 s target. The M2 port should look up existing pairs in one query per file and insert with `executemany` or `COPY`. Verify in spike S2 with a measured round trip.
4. **Design consequence for M3: the dashboard endpoint returns aggregates, not trades.** Shipping 10,000 trades as JSON to the browser was not benchmarked and would add serialization and transfer on top of the 390 ms. The trade log is paginated on the server. If Python aggregation is still too slow on Render's smaller CPU, the fallback in doc 02 applies: SQL aggregation, or cached per-user summary rows.
5. **Design consequence for M2: `rebuild_account` and `recalculate_trades` must be background jobs when hosted**, not work done inside a web request (16 s at one heavy user's size).

**Not covered:** concurrent users, HTTP and JSON overhead, Postgres (that is the point of re-running it), multiple accounts, filters other than one month, and the other pages. The synthetic grouping may differ from real exports (real days have more scale-ins and reversals).
