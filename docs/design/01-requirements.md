# 01. Requirements

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
| F-A7 | Invite codes to gate the private beta | P0 (beta) |
| F-A8 | Accept Terms of Service and Privacy Policy, versioned | P0 |

### Tenancy

| ID | Requirement | Pri |
|---|---|---|
| F-T1 | Every record belongs to exactly one user; no read or write across users | P0 |
| F-T2 | Per-user settings (timezone, trading-day start, theme) replace global settings | P0 |
| F-T3 | Per-session UI state (filters) replaces the global singleton | P0 |
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
| F-B5 | Admin view: user counts, plan counts, errors; no browsing of user trade data | P1 |

## 3. Non-functional requirements

| Area | Target | Notes |
|---|---|---|
| **Tenant isolation** | Zero cross-user access, enforced in the database and covered by automated two-user tests | The headline requirement |
| **Security baseline** | OWASP ASVS Level 2 as the checklist (see doc 04) | Not a certification |
| **Durability** | RPO 5 min (point-in-time recovery), RTO 4 h; tested restore | A journal is irreplaceable history; users will not forgive loss |
| **Availability** | 99.5% monthly (about 3.6 h down) | Not intraday-critical; users journal after the session |
| **Performance** | p95 page load under 1.5 s with 10,000 trades; import of a 5,000-row CSV under 10 s | Measure in M2 on synthetic data |
| **Scale target** | 1,000 registered users in year 1, design headroom to 10,000 | See section 4 |
| **Cost** | Infra under $60/month through beta | See doc 02 |
| **Privacy** | Export and delete on request; no broker credentials; no selling or sharing data; minimal analytics | GDPR/CCPA-ready |
| **Maintainability** | One developer can run it: managed services, CI, runbooks | |
| **Portability** | Docker image + plain Postgres + S3 API; no lock-in to a PaaS | Hosting is then a reversible decision |
| **Accessibility** | Keyboard navigable, contrast AA in both themes | The UI already uses aria labels |
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

Concurrency: 1,000 registered users is about 100 to 300 weekly actives and **30 to 50 concurrent websocket sessions at peak**. At 10,000 users, 300 to 500. A single NiceGUI process is the real ceiling (it does not support multiple workers; scaling out needs sticky sessions plus Redis, see [NiceGUI discussion](https://github.com/zauberzeug/nicegui/discussions/4483)). That is fine for year 1.

Read path to watch: `queries.load_trades` loads every matching trade into Python and aggregates with `Decimal`. At 10,000 trades per user that is likely tens to hundreds of milliseconds, but it is **unmeasured**. Plan: build a 10,000-trade synthetic dataset in M2, measure the dashboard, and only then decide whether to push aggregation into SQL (Postgres `NUMERIC` makes exact SQL sums possible).

## 5. Constraints

- Solo developer, part-time. Prefer managed services and boring tools.
- Existing stack: Python 3.12, NiceGUI 3.17, SQLite, pandas (used only in `parser.py` to read the CSV).
- Dev machine: Windows 10 Home, Node installed, **no Git, GitHub CLI or Docker yet**.
- The app currently holds real data for one user (you): 23 trades, 8 accounts.

## 6. Out of scope for launch

Broker API sync, native mobile app, team or coach accounts, public profiles and leaderboards, AI-generated trade reviews (needs a data-processing and consent design first), multi-currency, options and equities. Each is listed with its gate in doc 05.

## 7. Success measures for the beta

- **Activation:** a new user imports a first CSV within 24 hours of signing up (target 60%).
- **Retention:** returns in week 4 (target 30%).
- **Trust:** zero cross-tenant incidents; restore drill passes; no data-loss reports.
- **Quality:** import failures on valid Tradovate CSVs under 1%.
