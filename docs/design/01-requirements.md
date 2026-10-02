# 01. Requirements

*Revised 2026-10-02 for ADR-010 to ADR-012: the UI becomes a React SPA over a stateless API (changes F-T3, the concurrency model and the constraints), and the operator back end becomes explicit requirements (F-O1 to F-O8).*

## 1. Product framing

A hosted trading journal and analytics app for **prop-firm futures traders**. Users export a Tradovate Performance CSV, import it, and get trades, statistics, journaling (notes, tags, screenshots) and a view of their prop-firm business (evaluations, funded accounts, costs, payouts, pass rate).

The differentiator versus generic journals is the **prop-account lifecycle**, which the current code already models (account type, archive with outcome, ledger of costs and payouts, firm list). Protect and extend that; do not chase feature parity with every journal on the market.

**Persona.** A trader with 2 to 10 prop accounts at several firms, trading 5 to 30 trades a day, who exports a CSV at the end of the session and journals for 10 minutes. Desktop-first; wants to glance at the dashboard on a phone.

## 2. Functional requirements

Priority: **P0** blocks launch, **P1** expected at launch, **P2** after launch.

### Identity and accounts

| ID | Requirement | Pri |
|---|---|---|
| F-A1 | Sign up with email + password or Google; email must be verified before data entry | P0 |
| F-A2 | Log in, log out, "log out of all devices", password reset | P0 |
| F-A3 | Optional TOTP two-factor with recovery codes | P1 |
| F-A4 | Change email / password; view active sessions | P1 |
| F-A5 | Export all my data (CSV + JSON + screenshots) | P0 |
| F-A6 | Delete my account and all data (with a short grace period) | P0 |
| F-A7 | Invite codes to gate the private beta (multi-use, optional bundled plan grant) | P0 (beta) |
| F-A8 | Accept Terms of Service and Privacy Policy, versioned | P0 |

### Tenancy

| ID | Requirement | Pri |
|---|---|---|
| F-T1 | Every record belongs to exactly one user; no read or write across users | P0 |
| F-T2 | Per-user settings (timezone, trading-day start, theme) replace global settings | P0 |
| F-T3 | Per-browser UI state (filters in the URL) replaces the global singleton; the API keeps no per-session or per-tab state | P0 |
| F-T4 | New-user provisioning creates default tag categories and settings | P0 |

### Core journal (exists today; must keep working)

Import Tradovate Performance CSV with preview, duplicate handling and undo; trade grouping; dashboard statistics and charts; trade log with filters; trade detail with tags, rich-text notes, screenshots; accounts with fee schedules, archive, costs and payouts; timezone and trading-day handling.

### Product completeness

| ID | Requirement | Pri |
|---|---|---|
| F-P1 | First-run onboarding: create account, how to export from Tradovate, import, see a populated dashboard | P0 |
| F-P2 | Demo/sample data mode so a visitor can explore before importing | P1 |
| F-P3 | Calendar view and day view with day notes (README Phase 3; `day_notes` table already exists) | P1 |
| F-P4 | Reports: by symbol, time of day, day of week, hold time, tag, account (Phase 3) | P1 |
| F-P5 | Prop rule tracking: trailing drawdown, daily loss limit, profit target progress (schema fields exist; Phase 4) | P1 |
| F-P6 | Insights from tags (cost of each mistake tag, expectancy by setup) (Phase 4) | P2 |
| F-P7 | More import sources (see doc 05, "Import adapters") | P1 to P2 |
| F-P8 | Daily plan / review journaling, weekly review | P2 |
| F-P9 | Read-only shareable trade/day links | P2 (needs its own security review) |

### Business

| ID | Requirement | Pri |
|---|---|---|
| F-B1 | Plans and entitlements enforced **server-side** (accounts, storage, history) | P0 for paid launch |
| F-B2 | Subscription checkout, cancellation, invoices via a payment provider's hosted pages | P0 for paid launch |
| F-B3 | Lapsed subscription means read-only plus export, never data deletion | P0 for paid launch |
| F-B4 | Marketing site, pricing, docs/FAQ, support email, legal pages | P0 |
| F-B5 | Operator back end: see F-O1 to F-O8 below (replaces the earlier aggregates-only admin view) | P1 |

### Operator back end (admin)

Design: ADR-012, doc 03 (tables, plan resolution), doc 07 section 6 (endpoints). Priority P1 for public launch (milestone M10); the beta runs with the CLI and invite codes only.

| ID | Requirement | Pri |
|---|---|---|
| F-O1 | Admin means `is_admin` **and** MFA satisfied recently; `is_admin` is set only through the CLI, never through the UI or API | P1 |
| F-O2 | User management: search; view account metadata, effective plan and its source, usage counts; suspend and unsuspend; revoke sessions; trigger an export; schedule and cancel deletion | P1 |
| F-O3 | Manual plan override: plan, optional expiry, required reason; takes precedence over the billing-provider mirror and survives webhooks and the nightly reconcile job | P1 |
| F-O4 | Discount codes created through the payment provider's API | P1 |
| F-O5 | Local grant codes (N days of a plan, one redemption per user) and beta invite codes (multi-use, optional bundled grant) | P1 |
| F-O6 | Every admin action is written to the audit log, including views of a user's detail page | P1 |
| F-O7 | Admins never see trade, note or screenshot content; no impersonation; enforced by database grants, not only by code | P1 |
| F-O8 | Overview metrics: signups, active users, plan counts, import failure rate | P2 |

## 3. Non-functional requirements

| Area | Target | Notes |
|---|---|---|
| **Tenant isolation** | Zero cross-user access, enforced in the database and covered by automated two-user tests | The headline requirement |
| **Security baseline** | OWASP ASVS Level 2 as the checklist (see doc 04) | Not a certification |
| **Durability** | RPO 5 min (point-in-time recovery), RTO 4 h; tested restore | A journal is irreplaceable history; users will not forgive loss |
| **Availability** | 99.5% monthly (about 3.6 h down) | Not intraday-critical; users journal after the session |
| **Performance** | With 10,000 trades: p95 dashboard API response under 800 ms and p95 first load of the SPA under 2 s on broadband; JavaScript budget of 150 KB gzipped for the entry chunk and 300 KB for the landing route (targets; spike S1 measured 133 KB and about 298 KB on a toy with a chart and an editor, once those were code-split); import of a 5,000-row CSV under 10 s | Measure in M2 and M3 on synthetic data |
| **Scale target** | 1,000 registered users in year 1, design headroom to 10,000 | See section 4 |
| **Cost** | Infra under $60/month through beta | See doc 02 |
| **Privacy** | Export and delete on request; no broker credentials; no selling or sharing data; minimal analytics | GDPR/CCPA-ready |
| **Maintainability** | One developer can run it: managed services, CI, runbooks. Two languages now (Python and TypeScript), kept in step by generated API types | |
| **Operator access** | The operator cannot read user content; every admin action is audited | ADR-012 |
| **Portability** | Docker image + plain Postgres + S3 API; no lock-in to a PaaS | Hosting is then a reversible decision |
| **Accessibility** | Keyboard navigable, contrast AA in both themes | The NiceGUI UI uses aria labels today; the rebuild must keep that (Radix primitives and `jsx-a11y` linting help) |
| **Browsers** | Current Chrome, Edge, Firefox, Safari; responsive down to phone width for viewing | |

## 4. Load estimate

Inputs measured from your real data: screenshots average about 110 KB (14 files, 1.5 MB total); a notes body averages about 90 bytes.

Assumptions for a **heavy** user: 30 trades/day for 250 days is 7,500 trades a year, about 1.3 fill pairs per trade is roughly 10,000 fill pairs a year, and 2,000 screenshots a year.

| Quantity | Per heavy user per year | 1,000 users (mixed, 25% heavy) |
|---|---|---|
| Database rows (trades, fill pairs, import links, trade tags, screenshots) | about 35,000 | about 14 million |
| Database size incl. indexes | about 20 MB | about 8 GB |
| Screenshot storage | about 220 MB | about 80 to 100 GB worst case, about 30 GB typical |
| R2 cost for screenshots | | about $0.3 to $1.5 per month (after the 10 GB free) |

Concurrency: 1,000 registered users is about 100 to 300 weekly actives and **30 to 50 concurrent users at peak**. At 10,000 users, 300 to 500. The API is stateless (no websockets, no per-tab server state), so that load is a modest request rate and capacity grows by adding instances behind the load balancer. The single-process NiceGUI ceiling that originally shaped this section is gone (ADR-010). Request rate and memory per instance are **unmeasured**; spike S4 and M11 measure them.

Read path to watch: `queries.load_trades` loads every matching trade into Python and aggregates with `Decimal`. At 10,000 trades per user that is likely tens to hundreds of milliseconds, but it is **unmeasured**. The dashboard is several stats requests, so they should share one load or one cached summary per request. Plan: build a 10,000-trade synthetic dataset in M2, measure the dashboard, and only then decide whether to push aggregation into SQL (Postgres `NUMERIC` makes exact SQL sums possible).

## 5. Constraints

- Solo developer, part-time. Prefer managed services and boring tools.
- Existing stack: Python 3.12, NiceGUI 3.17 (being replaced, ADR-010), SQLite (being replaced, ADR-002), pandas (used only in `parser.py` to read the CSV). New: FastAPI, React and TypeScript.
- Developer experience: some React, new to TypeScript. That drives the strict, generated-types tooling in doc 07.
- Dev machine: Windows 10 Home with Git, the GitHub CLI, `uv`, Python 3.12 and Node 24 installed; **no Docker** (hardware virtualization is off) and no pnpm yet.
- The app currently holds real data for one user (you): 23 trades, 8 accounts.

## 6. Out of scope for launch

Broker API sync, native mobile app, team or coach accounts, public profiles and leaderboards, AI-generated trade reviews (needs a data-processing and consent design first), multi-currency, options and equities. Each is listed with its gate in doc 05.

## 7. Success measures for the beta

- **Activation:** a new user imports a first CSV within 24 hours of signing up (target 60%).
- **Retention:** returns in week 4 (target 30%).
- **Trust:** zero cross-tenant incidents; restore drill passes; no data-loss reports.
- **Quality:** import failures on valid Tradovate CSVs under 1%.
