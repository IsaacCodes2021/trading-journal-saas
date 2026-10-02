# 05. Features and roadmap

## 1. Feature set by tier

Requirement IDs refer to doc 01. Sizes: **S** under 10 h, **M** 10 to 30 h, **L** 30 to 80 h, **XL** more.

### Tier 0: platform (nothing ships without these)

| Feature | Req | Size | Notes |
|---|---|---|---|
| Postgres port + test harness | F-T1 | L | Doc 03 section 9. Tests first |
| Tenancy: RLS, composite FKs, `tenant_session`, per-tab state, `user_settings` | F-T1..T4 | L | |
| Sign up / log in / reset / verify (hosted IdP) | F-A1, A2 | M | |
| Sessions, logout everywhere, audit log | F-A2 | M | |
| Optional TOTP MFA | F-A3 | S | Mostly IdP configuration |
| Profile: email/password change, active sessions | F-A4 | S | |
| Data export and account deletion (with jobs) | F-A5, A6 | M | |
| Invite codes for the beta | F-A7 | S | |
| Terms/Privacy acceptance and versioning | F-A8 | S | |
| Screenshots to R2, quotas, Pillow pipeline | F-T1 | M | |
| Hardening: sanitization, limits, headers, rate limits | doc 04 | M | |
| Onboarding: timezone confirm, first import, "how to export from Tradovate" | F-P1 | M | Activation depends on this |
| Marketing site, pricing, docs/FAQ, support email, legal pages | F-B4 | M | Waitlist page goes up early (M1) |
| CI/CD, staging, backups, restore drill, monitoring, runbooks | doc 02 | L | |

### Tier 1: product completeness (expected at public launch)

| Feature | Req | Size | Notes |
|---|---|---|---|
| Calendar heatmap + day view with day notes | F-P3 | L | `day_notes` table exists; README Phase 3 |
| Reports: symbol, time of day, weekday, hold time, tag, account | F-P4 | L | Reuse `finance.py` aggregators |
| Demo/sample data mode | F-P2 | M | A synthetic dataset, never real user data |
| Prop rule tracking | F-P5 | L to XL | See note A |
| Plans, entitlements, checkout, portal, webhooks | F-B1..B3 | L | Needs the payment account approved first |
| Admin aggregates page | F-B5 | S | |
| Second import source | F-P7 | L | See note B; pick from beta demand |

### Tier 2: after launch (each has a gate)

| Feature | Gate before building |
|---|---|
| Insights from tags (mistake cost, expectancy by setup, streaks) | Enough beta data to know which insights users act on |
| Daily plan / pre-market and weekly review journaling | Beta feedback |
| Shareable read-only trade/day links | Own threat model: sanitized rendering, unguessable tokens, revocation, no PII, per-link expiry |
| More import sources | Real sample files from real users |
| Broker API sync (live accounts only) | Security re-review: encrypted token storage (envelope encryption, KMS), scope minimization |
| AI-assisted review of notes | Consent design, data-processing agreement with the model provider, opt-in, no training on user data, descriptive not prescriptive output |
| Mobile app, public API | Versioned JSON API with tokens (FastAPI routers per doc 02 section 5) |
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
| **S1** NiceGUI multi-user | Can page handlers get the Starlette `request` for middleware auth? Does per-tab state in `app.storage.tab` replace the filter singleton cleanly? Does the Socket.IO endpoint check `Origin`? What CSP survives? Does the built-in `user` test fixture work for UI tests? | A two-user toy with a login redirect, isolated filters, headers, and a passing UI test |
| **S1b** IdP choice | Which provider meets the criteria in doc 03 section 4 with the least friction from Python? | A working login callback against the front-runner; notes on user export and deletion APIs |
| **S2** Postgres + RLS | Does the RLS/`tenant_session` design work with psycopg's pool, and how painful is the port? | `accounts.py` and `journal.py` ported, their tests green on Postgres, a two-tenant test passing |
| **S3** Files | Does a presigned R2 URL work in `ui.image`? Does the Pillow pipeline keep screenshot quality at acceptable size? | Upload, thumbnail, display, delete, orphan report |
| **S4** Deploy | Do websockets work through Cloudflare + Render? Memory per session? Cold start? Deploy behavior? | Hello-world container reachable at the real domain; RSS measured at 50 sessions |
| **S5** Payments (non-code) | Will a provider approve a trading-journal product? | Application submitted and answered |

## 3. Roadmap

Effort is **hours of focused, AI-assisted work including tests and review**. These are my estimates and uncalibrated: compare actuals at the end of M2 and rescale the rest.

| M | Milestone | Hours | Exit criteria |
|---|---|---|---|
| **M0** | **Foundations (GitHub first)**: Git, private repo, CI, lock file, Dockerfile, issues | 8 to 15 | Green CI on `main` running today's tests; container image builds in CI (local Docker is not practical on the dev machine, see doc 02 section 7); backlog in GitHub Issues |
| **M1** | **Spikes** S1, S1b, S2, S3, S4, S5; waitlist landing page live | 20 to 30 | Decisions confirmed or amended in doc 06; payment application submitted; waitlist collecting emails (validates demand early) |
| **M2** | **Multi-tenant core**: Postgres port, tenancy, per-tab state, `user_settings`, `source/ref` fill-pair columns, local-data migration script | 60 to 90 | All 16 existing test files green on Postgres; schema lint + two-tenant suite green; your data migrated and golden-checked; 10,000-trade benchmark recorded |
| **M3** | **Auth and accounts**: IdP, sessions, profile, MFA, export, deletion, audit log, invites | 45 to 70 | Doc 04 authentication and access-control gates ticked; end-to-end signup to dashboard on staging |
| **M4** | **Files and hardening**: R2 pipeline, quotas, sanitization, limits, headers, rate limits, logging, Sentry | 30 to 45 | Doc 04 input/output/file gates ticked; ZAP baseline clean |
| **M5** | **Deploy and ops**: staging + production, CI/CD to host, backups, restore drill, monitoring, runbooks, email domain | 30 to 45 | Restore drill passed; alerts fire in a test; runbooks written |
| | **Private beta (invite-only, free), 10 to 30 users** | | Collect feedback and sample files; watch activation and import-failure rate |
| **M6** | **Product completeness**: onboarding polish, demo mode, calendar and day view, reports (in parallel with beta) | 80 to 140 | Beta users reach the dashboard unaided; docs/FAQ written |
| **M7** | **Billing and legal**: plans, entitlements, checkout, webhooks, dunning, legal review | 30 to 50 | A test subscription goes through the full lifecycle; lapse leads to read-only, never deletion |
| **M8** | **Launch readiness**: load test, security review, penetration test (if budget), launch checklist, support process, status page | 25 to 40 | Checklist (`engineering:deploy-checklist`) green; load test meets doc 01 targets |
| | **Public launch** | | |

**Totals:** about **200 to 300 hours to private beta**, **330 to 525 hours to public launch**.
At **15 h/week**: beta in roughly 3 to 4.5 months, public launch in 5 to 8 months. At 30 h/week, half that.

```
M0 -> M1 -> M2 -> M3 -> M4 -> M5 --> BETA --> M7 -> M8 --> LAUNCH
                                       \--> M6 (parallel, fed by beta feedback) --/
S5 (payment approval) runs from M1 and gates M7
```

## 4. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Payment provider declines a trading-adjacent product | Medium | High | Apply in M1; keep two providers on the shortlist; position as analytics, not advice |
| R2 | NiceGUI limits found late (CSP, memory per socket, auth integration) | Medium | Medium | Spikes S1 and S4; documented revisit triggers; domain layer is UI-independent |
| R3 | Postgres port changes behavior subtly | Medium | High | Port the test harness first; golden-stats comparison on real data; run fixtures on both engines during the port |
| R4 | Tradovate changes the CSV format | Medium | Medium | Strict parser errors with a clear message; versioned fixtures; track import failure rate; keep sample files from beta users |
| R5 | Prop firms change rules; computed limits differ from official | High | Medium | Rules as versioned data; informational disclaimer; user can override per account |
| R6 | Cross-tenant leak | Low | **Critical** | Three layers (doc 03); lint and isolation tests in CI; penetration test |
| R7 | Data loss | Low | **Critical** | PITR + separate nightly dump + versioned bucket; restore drills |
| R8 | Solo-developer bandwidth; scope creep | High | High | Hard tier boundaries; beta gating; time-box each milestone; GitHub Projects board |
| R9 | Legal exposure (financial disclaimer, privacy, tax) | Medium | High | Professional review of Terms and Privacy; merchant of record; disclaimers in-product |
| R10 | Weak demand | Medium | High | Waitlist in M1; invite-only beta measures activation and retention before billing work |
| R11 | Dev workstation compromise (Windows 10 is past end of support) | Low | High | Upgrade or enroll in extended updates; hardware-key 2FA; prod secrets not stored locally |

## 5. Test strategy (summary)

| Layer | What | Tooling |
|---|---|---|
| Unit | Parser, grouping, finance, timeutil | pytest (exists) |
| DB integration | All data modules on real Postgres | pytest + Postgres service (CI) / compose (local); per-test transaction rollback |
| **Tenant isolation** | Schema lint; two-tenant suite across every function and route | pytest, run on every PR |
| Auth flows | Callback validation, session expiry/rotation/revocation, using a fake IdP | pytest |
| Webhooks | Signature verification, idempotency, out-of-order events | pytest |
| UI smoke | Login, import, dashboard, trade detail | NiceGUI's `user` test fixture (confirm in S1); Playwright against staging after deploy |
| Security | XSS corpus, header test, oversize uploads, gitleaks, `pip-audit`, ZAP baseline | CI + M4/M8 |
| Load | 200 simulated websocket sessions, 5,000-row import, 10,000-trade dashboard | k6 or Locust, in M8 |

The `engineering:testing-strategy` skill can turn this into a full plan when we reach M2.

## 6. Step 1: connect GitHub (the actual first task)

Current state: no Git, no `gh`, no repo, no history. Nothing here has been done yet; each step is for us to do together, and I will ask before installing or pushing anything.

1. **Install tools:** Git for Windows and the GitHub CLI (with `winget`), plus `uv` and `ruff` in a Python 3.12 virtual environment. Docker is deferred (see step 10).
2. **Identity:** set `git config --global user.name` and `user.email`. Use GitHub's private no-reply address (`<id>+<username>@users.noreply.github.com`) so your real email is not embedded in commit history.
3. **Authenticate:** `gh auth login` (browser flow). Turn on 2FA for the GitHub account first. Do not paste tokens anywhere.
4. **Clean the tree:** delete `backend/=ro`; extend `.gitignore` (doc 02 section 7); add `.gitattributes` (`* text=auto`, `*.bat text eol=crlf`, `*.sh text eol=lf`) because the project mixes Windows and shell scripts.
5. **First commit:** `git init -b main`; `git add -A`; **read `git status` and `git diff --cached --stat` before committing** to confirm `data/` and `.venv/` are absent; commit; tag it `v0.1.0-local` so the last local-first version stays recoverable (ADR-008).
6. **Create the remote:** `gh repo create trading-journal --private --source . --remote origin --push`.
7. **CI v0:** a workflow that runs `ruff` and the existing SQLite tests so `main` is green before the port starts. Add `dependabot.yml` and a PR template. Enable Dependabot alerts and security updates in repo settings (free).
8. **Protect `main`:** needs GitHub Pro on a private repo (doc 02 section 7). Until then, always work on branches and merge through PRs.
9. **Backlog:** turn the milestones above into GitHub Issues on a Project board; replace the README status table with a link to it.
10. **Dev environment:** `uv` lock file and Dockerfile are done in M0 (CI builds the image). For local Postgres in M2 use a native Windows install: Docker Desktop is not practical on this machine today (hardware virtualization is off in firmware, WSL is still v1, about 11 GB free on C:). `.env.example` arrives with the M2 config rewrite.

Then M1 (spikes). If you meant "Sign in with GitHub" for end users rather than the repo, say so: it would be added as a social login option at the IdP and does not change this plan.
