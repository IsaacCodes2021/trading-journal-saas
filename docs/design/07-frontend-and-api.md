# 07. Front end and API (FastAPI + React)

**Status:** Proposed, 2026-10-02. Introduced by [ADR-010](06-decisions.md); read with docs 02 to 05, which are updated to match. Package versions below are the latest on the npm registry on 2026-10-02; the ecosystem has moved a major version on several tools, so **confirm the combination in the M1 scaffold** before treating any pin as settled. Spike S1 (2026-10-02) confirmed the set except TypeScript, which must be 6.0.x; the evidence is in `spikes/s1-spa-cookie-auth/README.md` on the branch `spike/s1-spa-cookie-auth`.

## 1. What stays, what moves, what goes

| Bucket | Files | Notes |
|---|---|---|
| **Keep in Python, unchanged** | `parser.py`, `grouping.py`, `trades.py`, `finance.py`, `timeutil.py`, `symbols.py`, `models.py`, and their tests | Money stays exact `Decimal`. 210 tests today |
| **Port (SQL dialect only)** | `accounts.py`, `ledger.py`, `journal.py`, `importer.py`, `store.py`, `queries.py`, `settings.py`, `db.py` | Doc 03 section 9 |
| **New, Python** | `api/`, `auth/`, `infra/`, `jobs/` | Doc 02 section 5 |
| **New, TypeScript** | `frontend/` | This doc |
| **Deleted after parity** | `ui/` (2,752 lines in 17 files) | Kept as the behavioral reference until M4 exits |

**Why the rewrite is cheap now.** `ui/` is a single-user proof of concept of about 2,750 lines; the domain layer (about 2,450 lines) is already separate and the UI reaches the database through about 45 `open_db()` call sites that the API layer replaces; there are no users or deployed data to migrate. It never gets cheaper than this.

## 2. API contract

| Topic | Decision |
|---|---|
| Paths | `/api/v1/*` tenant API; `/api/admin/v1/*` operator API; `/auth/*` browser redirects (login, callback, logout); `/files/{id}` screenshots; `/webhooks/*`; `/healthz`, `/readyz` |
| Format | JSON, **snake_case keys end to end** (no case-conversion layer, so nothing to drift) |
| **Money** | JSON **strings** (`"-475.00"`). A shared `Money` type in `api/schemas` accepts strings only (rejects JSON numbers) and declares `{"type": "string", "pattern": "^-?\\d+(\\.\\d+)?$"}` in **both** input and output schemas. Reason: Pydantic documents `Decimal` as a string in serialization schemas but as `number \| string` in validation (input) schemas, which would let the generated TypeScript types accept JavaScript floats ([Pydantic JSON Schema](https://docs.pydantic.dev/latest/concepts/json_schema/), [FastAPI discussion](https://github.com/fastapi/fastapi/discussions/14264)). A CI test fails if any money field in the OpenAPI document has type `number` |
| Client arithmetic | None with `number`. Totals come from the API (`finance.py`). Where the client must compute, use `decimal.js`. Chart series convert strings to `number` in exactly one helper (`lib/money.ts`) at the ECharts boundary, the same rule as today ("floats only for drawing charts"). S1 confirmed that `Intl.NumberFormat` formats a decimal *string* exactly (a 21-digit value, in a unit test on Node 24) |
| Time | Timestamps ISO 8601 UTC with `Z`; trading days `YYYY-MM-DD`; the client formats in the user's timezone from `/api/v1/session`; trading-day computation stays server-side (`timeutil.py`) |
| IDs | Integers for rows (identity bigints, assumed far below 2^53); fill refs are strings (ADR-009) |
| Errors | RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`, `errors[{field, message}]`). 401 not signed in; 403 forbidden, suspended, or `mfa_required`; 404 for ids that belong to someone else (RLS); 409 conflict; 413 too large; 422 validation; 429 rate limit with `Retry-After` |
| Lists | `limit` (max 200), `offset`, `sort=field` or `sort=-field`; responses `{items, total}` |
| Filters | One query model shared by stats and trades: `preset`, `start`, `end`, `account_type`, `show_archived`, `firms[]`, `account_ids[]`, `symbol_roots[]`, `direction`. **`preset` is resolved server-side in the user's timezone**, which removes the server-clock "today" bug at the source; the client never computes date ranges |
| Versioning | Additive changes within `v1`. After a deploy an open tab still runs the old SPA, so keep the API backward compatible for one release; every response carries `X-App-Version`, and the SPA shows a "reload" banner when it differs from its own build id |
| Rate limits | Per-user token bucket; in-process while there is one instance, Postgres or Redis once there are several |
| **Types cannot drift** | FastAPI emits `openapi.json` with `python -m tradejournal.api.openapi` (no server needed). `openapi-typescript` generates `src/lib/api/schema.d.ts`; `openapi-fetch` is the typed client. Both generated files are committed; **CI regenerates them and fails on any diff** |
| Generator settings (S1) | Pair `openapi-typescript --default-non-nullable false` with Pydantic `json_schema_serialization_defaults_required=True` on a shared base model. Without both, fields that have defaults become *required* in request types (callers must send them) or optional in response types (callers must check them). FastAPI emits separate `-Input` and `-Output` schemas where the two differ |

`GET /api/v1/session` is the SPA's first call and returns: `user`, `settings` (timezone, trading-day start and timezone, theme), `plan` (effective plan, source, expiry), `entitlements`, `csrf_token`, `flags` (`is_admin`, `mfa_fresh`), `app_version`. A 401 sends the browser to `/auth/login?next=<path>` (a full-page OIDC redirect, doc 03 section 4).

## 3. React tooling: recommendation

| Concern | Pick | Main alternatives | Why this one | Watch out / verify |
|---|---|---|---|---|
| Build | **Vite 8** + `@vitejs/plugin-react` 6 | Next.js (rejected, ADR-010), Rsbuild | Static bundle with one external script: a normal CSP, no server to run | S1: Vite 8.3 with `plugin-react` 6 built the toy in under a second; confirmed |
| Language | **TypeScript 6.0.x** strict, `noUncheckedIndexedAccess` (not 7 yet) | Plain JS | The compiler becomes the API-drift detector | **Verified in S1:** `typescript@7.0.2` ships only a native launcher with no JavaScript compiler API, so `openapi-typescript` crashes and `typescript-eslint` (support declared below 6.1) cannot run. Pin 6.0.3; revisit when both support 7 |
| UI library | **React 19** | | | |
| Router | **TanStack Router 1.x** | React Router 8 | Typed route and **typed search params**: filters live in the URL, so they survive reload and can be shared; route-level code splitting for `/admin` | Smaller community. **Confirmed in S1:** typed search params worked and the compiler caught an optional-parameter bug; React Router 8 stays the contained fallback (confined to `routes/`) |
| Server state | **TanStack Query 5** | SWR, RTK Query | Caching, loading and retry states, and invalidation after mutations (an import invalidates stats and trades) | |
| API client | **openapi-typescript 7** + **openapi-fetch 0.17** | hey-api/openapi-ts, Orval | Types and a tiny runtime straight from the schema | `openapi-fetch` is pre-1.0: pin the exact version and wrap it in `lib/api/client.ts` so it is swappable. S1: pass `fetch: (request) => globalThis.fetch(request)` so tests can replace it, and generate with `--default-non-nullable false` (see the generator row above) |
| Styling and components | **Tailwind CSS 4** + **shadcn/ui** (Radix primitives copied into the repo) | MUI, Mantine | Accessible primitives, no runtime CSS-in-JS (CSP-friendly), you own the component code, dark/light through CSS variables (replaces `theme.py`, 320 lines) | shadcn components are copied code, so upstream fixes are manual |
| Table | **TanStack Table** + **TanStack Virtual 3** | AG Grid Community, plain table | Headless, server-side sorting and paging, row virtualization | npm shows v9 as current (v8 was long-lived): spike one in M1, pick one major, do not mix |
| Charts | **Apache ECharts 6**, modular imports | Recharts, visx | Already used (`charts.py`, 204 lines); options port almost 1:1 and the radar and drawdown charts are covered | **S1:** the default tooltip uses inline `style` attributes, which a strict CSP blocks, so use a plain-text `formatter`. Its returned string is inserted as HTML, so escape user-derived text with `echarts.format.encodeHTML` (an XSS sink). Even with modular imports the chart chunk is about 165 KB gzipped: load it lazily |
| Forms | **React Hook Form 7** + **Zod 4** | TanStack Form | Uncontrolled forms, schema validation; server problem+json errors map onto fields | Keep Zod to UX checks; the server stays the authority and types come from OpenAPI |
| Notes editor | **Tiptap 3** (ProseMirror) | Lexical, Quill | Replaces the Quasar editor; HTML in and out, so notes stay HTML | The server sanitizes with `nh3` (allow-list in doc 04 S6); the client renders read-only HTML only through one `SafeHtml` component using DOMPurify 3. Tiptap's schema must be a subset of the allow-list. Normalize old Quasar HTML in the migration script (`div` to `p`, `b` to `strong`, `i` to `em`, `strike` to `s`). **S1:** set `injectCSS: false` (by default Tiptap injects a `<style>` element that a strict CSP blocks) and ship the base ProseMirror rules in our own CSS. DOMPurify *parsing* a hostile `style` attribute logs a CSP violation even though nothing is applied, one more reason to sanitize on write |
| Decimal math | **decimal.js 10** | big.js | Only for the rare client-side total | |
| Tests | **Vitest 5** + **React Testing Library 16** + **MSW 3** | Jest | Shares Vite's config; MSW mocks at the fetch boundary | Vitest 5 and MSW 3 are new majors |
| End to end | **Playwright 1.63** | Cypress | Cross-browser; runs against the built container and a fake IdP | |
| Lint and format | **ESLint 10** + `typescript-eslint`, `react-hooks`, `jsx-a11y`, `react/no-danger`; **Prettier** | Biome | Accessibility rules (the app uses aria today) and a ban on raw HTML injection | **S1:** the peer warnings for `eslint-plugin-react` and `jsx-a11y` are cosmetic; planted `react/no-danger`, `jsx-a11y/alt-text` and `no-explicit-any` violations all fired under ESLint 10 |
| Package manager | **pnpm 12** | npm | Strict dependency tree; **dependency lifecycle scripts blocked by default** since v10 (allow-list via `onlyBuiltDependencies`); `minimumReleaseAge` delays installing freshly published versions, available since v10.16 ([pnpm settings](https://pnpm.io/10.x/settings), [Socket](https://socket.dev/blog/pnpm-10-0-0-blocks-lifecycle-scripts-by-default)) | An advisory once bypassed the script block ([GHSA-379q-355j-w6rj](https://github.com/pnpm/pnpm/security/advisories/GHSA-379q-355j-w6rj)): keep pnpm current. Corepack ships with Node 24 LTS (installed here, v24.21) but is dropped from Node 25 and later ([Socket](https://socket.dev/blog/node-js-tsc-votes-to-stop-distributing-corepack)): pin `packageManager` in `package.json` and install pnpm with its own installer rather than relying on Corepack. **S1:** `minimumReleaseAge` held TanStack Query back from a 24-hour-old release and `strictDepBuilds` with an empty allow-list let everything install; both settings live in `pnpm-workspace.yaml` |
| Node | **24 LTS**, pinned in `.nvmrc`, CI and the Dockerfile | | | |

**Conventions for someone new to TypeScript** (you said: some React, new to TypeScript). Strict mode on; `any` is a lint error; types come from the generated schema, never hand-copied; one pattern per concern (a query hook and a mutation hook per endpoint in `features/<x>/api.ts`, one form pattern, one table pattern, one chart wrapper); small components; a short `frontend/README.md` with those five patterns. I draft each pattern once and the rest copy it, so review effort goes to the patterns, not every line.

## 4. Layout and build

```
frontend/
  package.json  pnpm-lock.yaml  vite.config.ts  tsconfig.json  eslint.config.js  .nvmrc  index.html
  openapi.json                         generated, committed
  src/
    main.tsx   routes/                 TanStack Router route tree
    features/{dashboard,trades,trade-detail,imports,accounts,settings,onboarding,profile,admin}/
        api.ts  components/  hooks/    (admin is a lazy-loaded chunk)
    components/ui/                     shadcn components
    lib/{api/(client.ts, schema.d.ts), money.ts, format.ts, filters.ts, session.ts}
    styles/                            Tailwind, design tokens, dark and light
  e2e/                                 Playwright
```

**One image.** A multi-stage Dockerfile: a Node 24 stage runs `pnpm install --frozen-lockfile && pnpm build`; the Python stage copies `dist/` into the API. FastAPI serves `/assets/*` with `Cache-Control: public, max-age=31536000, immutable` (hashed file names) and `index.html` with `no-cache`; any other `GET` that accepts HTML and is not under `/api`, `/auth`, `/files`, `/webhooks`, `/healthz` or `/readyz` returns `index.html` (the client-side routing fallback).

**Bundle budget (S1 measurement).** An unsplit toy bundle was 1,283 KB, 422 KB gzipped. With the chart (ECharts) and the editor (Tiptap) lazily loaded: entry chunk 133 KB gzipped, chart chunk 165 KB, editor chunk 124 KB, so the dashboard route is about 298 KB gzipped. Code-splitting those two is mandatory. Budget (targets, to re-measure on the real app): entry chunk 150 KB and landing route 300 KB gzipped.

## 5. Page-by-page rebuild list

Sizes are today's NiceGUI line counts. About 60 routes in total.

| Page (route) | Replaces | Endpoints | Notes |
|---|---|---|---|
| **App shell**: header, nav, theme toggle, filter bar | `layout.py` 40, `theme.py` 320, `filters.py` 129, `widgets.py` 160, `state.py` 57, `fmt.py` 33 | `GET /api/v1/session`, `GET /api/v1/filters/options` | Design tokens for dark and light. **Filter state lives in the URL** (`/?preset=30d&firms=Apex`), replacing the global singleton; last-used filters in `localStorage` as a convenience only |
| **Dashboard** `/` | `pages/dashboard.py` 148, `charts.py` 204 | `GET /api/v1/stats/summary`, `.../stats/equity` (cumulative net and drawdown per trade), `.../stats/daily`, `.../stats/score` | KPI tiles, equity and drawdown chart, score radar, daily P&L chart with table view, "How it's scored" panel. All numbers computed on the server by `finance.py` |
| **Trade log** `/trades` | `pages/trades.py` 212 | `GET /api/v1/trades` (filters, sort, paging, per-column filters, `q` search), `GET /api/v1/trades/summary`, `GET /api/v1/trades/{id}/neighbors` | Count and net follow every filter. The previous/next arrows in the detail view need the neighbors endpoint because paging is server-side. Journal column from `journal_summary` |
| **Trade detail** `/trades/$tradeId` | `trade_detail.py` 397 | `GET /api/v1/trades/{id}` (stats and fill pairs); `PUT /api/v1/trades/{id}/notes`; tags: `GET /api/v1/tags`, `PUT /api/v1/trades/{id}/tags/{group_id}`, `POST` and `DELETE /api/v1/trades/{id}/tags/{tag_id}`; screenshots: `GET` and `POST /api/v1/trades/{id}/screenshots`, `DELETE /api/v1/screenshots/{id}`, `GET /files/{id}`, `GET /files/{id}/thumb` | Tiptap with debounced autosave; drop, browse and **paste** upload (replaces the inline `PASTE_SCRIPT`); lightbox viewer; insert-template button |
| **Imports** `/import` | `pages/imports.py` 304 | `POST /api/v1/imports/preview` (multipart: `account_id`, files), `POST /api/v1/imports` (commit), `GET /api/v1/imports`, `POST /api/v1/imports/{id}/undo/preview`, `POST /api/v1/imports/{id}/undo`, `GET /api/v1/accounts` | **Stateless**: today's server-held pending files go away. The browser keeps the `File` objects; commit re-uploads them with the preview's SHA-256 hashes, which the server verifies. CSV size and row caps; the import runs in a thread pool behind a concurrency semaphore |
| **Settings** `/settings/*` | `pages/settings_page.py` 663 (the largest file) | Preferences: `GET` and `PATCH /api/v1/settings`. Accounts: `GET` and `POST /api/v1/accounts`, `PATCH /api/v1/accounts/{id}`, `GET` and `PUT .../fees`, `PATCH .../rules`, `POST .../archive`, `POST .../restore`, `GET /api/v1/accounts/stats` (pass rate). Ledger: `GET` and `POST /api/v1/accounts/{id}/ledger`, `DELETE /api/v1/ledger/{id}`, `GET /api/v1/ledger/summary`. Tags: `GET` and `POST /api/v1/tags`, `PATCH` and `DELETE /api/v1/tags/{id}`, `POST /api/v1/tags/{id}/merge`, `POST`, `PATCH`, `DELETE /api/v1/tag-groups` | Split into sub-routes (`/settings/preferences`, `/accounts`, `/tags`), one component each. Saving fees or trading-day settings recalculates trades; if that takes over about 5 s on 10,000 trades, make it a background job with polling (`GET /api/v1/jobs/{id}`). Measure in M3 |
| **Onboarding** `/welcome` (new, F-P1) | none | `GET /api/v1/onboarding` (progress computed from data), `PATCH /api/v1/settings`, accounts create, imports preview and commit | Confirm timezone, create the first account, how to export from Tradovate, upload a CSV, done. The timezone rule is in doc 03 section 7 |
| **Profile** `/profile` (new) | none | `GET` and `DELETE /api/v1/sessions`, `POST` and `GET /api/v1/exports`, `POST` and `DELETE /api/v1/account/deletion`, `POST /api/v1/grants/redeem`, billing portal link (later) | Email and password changes link out to the IdP |
| **Sign-in** (new) | none | `/auth/login`, `/auth/callback`, `POST /auth/logout` | Browser redirects, not SPA pages, apart from a "signed out / invite required" screen |
| **Admin** `/admin/*` (new) | none | Section 6 | A lazy-loaded chunk, hidden unless `session.flags.is_admin`; the real control is server-side |
| After beta | Calendar and day view (`GET /api/v1/stats/calendar`, `GET` and `PUT /api/v1/day-notes/{date}`), reports, prop-rule tracker, demo mode | | Tier 1 in doc 05 |

## 6. Admin API and pages (design in ADR-012, tables in doc 03)

| Area | Endpoints under `/api/admin/v1` |
|---|---|
| Users | `GET /users?q=&status=&plan=` search; `GET /users/{id}` (account metadata, effective plan and its source, **usage counts from `user_usage`**, session count, flags); `POST /users/{id}/suspend` and `/unsuspend`; `POST /users/{id}/revoke-sessions`; `POST /users/{id}/export`; `POST` and `DELETE /users/{id}/deletion` (schedule, cancel) |
| Plan override | `PUT /users/{id}/plan-override` (`plan`, optional `expires_at`, required `reason`); `DELETE /users/{id}/plan-override` (revoke, with reason); `GET /users/{id}/plan-overrides` (history) |
| Codes | `GET` and `POST /invites`, `POST /invites/{id}/revoke`; `GET` and `POST /grant-codes`, `POST /grant-codes/{id}/revoke`; `GET` and `POST /discount-codes`, `POST /discount-codes/{id}/deactivate` (calls the payment provider's API) |
| Audit and metrics | `GET /audit?actor=&target=&action=&from=&to=`; `GET /metrics/overview` (signups, active users, plan counts, import failure rate) |

Rules for every admin route: the `require_admin` dependency (**`is_admin` and MFA satisfied recently**); the `system_session("admin")` profile, whose DB role has **no grants on tenant tables**; every mutation, and every view of a user's detail page, written to `audit_log`; no response contains trade, note or screenshot data (a contract test scans the admin part of the OpenAPI document for such fields); no impersonation. Admins are created only by the CLI.

## 7. What this changes in testing and delivery

Summarized here; the full strategy is in doc 05 section 5 and CI in doc 02 section 7.

- A **two-tenant test generated from the OpenAPI document**: for every route, user B calls it with user A's ids and must get 404 or an empty result.
- Front end: Vitest and React Testing Library for components, MSW for network, Playwright end to end against the built container with a **fake IdP**; `openapi.json` and `schema.d.ts` drift check.
- Load testing is plain HTTP (k6 or Locust), not websocket sessions.

## 8a. Front-end progress (running log)

**Slice 1 (2026-10-03): shell, dashboard, trade log.** Built from the Design canvas "Trading Journal Mockups" on branch `fe/design-system-and-shell`. Product name in the UI: Roundturn. 125 tests.

What exists: the design tokens (the canvas's dark palette, plus a light theme derived from it); the app shell with the filter-carrying nav, plan chip, theme toggle (applied at once, saved to the account) and account menu; the shared filter bar with all state in the URL; the Dashboard (tiles, equity and drawdown, score radar with its rules, daily P&L as chart or table, per-account table) and the Trade log (sort, search, paging). `/trades/$tradeId`, `/import` and `/settings` are placeholder pages.

Decisions and deviations from the plan in section 3 and 4:

- **Charts are plain SVG, not ECharts**, so the dashboard needs no chart chunk (entry chunk 129 KB gzipped of the 150 KB budget). Each chart is drawn at its measured width so labels stay readable on a phone, and carries a text description for screen readers. Revisit ECharts only if a chart needs zoom, brushing or hundreds of series.
- **No shadcn yet.** The few components needed (card, button, segmented control, popover, chip) are small and live in `components/ui`; adopt shadcn when a screen needs a dialog, combobox or date picker.
- **The radar draws as many axes as the data has.** The mockup shows 5; `finance.performance_score` has 6 (win rate, profit factor, average win/loss, consistency, max drawdown, recovery factor), and the "How it's scored" panel lists each rule from the server.
- **Pages read through a `DataSource` context** (`lib/data`). Today's default is a seeded sample set (`lib/demo`) that ports the backend's `finance` definitions, with a permanent "Sample data" banner. When the stats and trades endpoints exist (M3), the API source becomes the default, its generated types replace `lib/data/model.ts`, and the banner goes. The sample set is not product code and is never reached once the API source is the default.
- **Money stays a decimal string** everywhere; `lib/money.ts` `toChartNumber` is the only conversion to a number, and only for drawing.
- **Fonts.** IBM Plex Sans and Mono (latin, weights 400, 500, 600) are self-hosted from `@fontsource` (installed 2026-10-03 with the owner's approval): the content security policy allows no font host. Other scripts fall back to the system fonts in the stack. Both `woff2` and `woff` files are emitted into `dist/`, but a browser downloads only the `woff2` it uses.
- **Dev stub.** `frontend/dev/session-stub.ts` answers the session calls under `pnpm dev` only (`apply: 'serve'`); `TJ_DEV_API=real` proxies to the API on port 8000 instead.
- Phone width: no page-level sideways scroll; tables scroll inside their card. A card-per-trade layout for the log on a phone is a later improvement.

Next: Trade detail, Import, Settings (accounts, fees, rules, tags, preferences), Onboarding, then the sign-in and admin screens.

**Slice 2 (2026-10-03): Trade detail.** Branch `fe/trade-detail`, from the canvas's TradeDetail screen. The page for `/trades/$tradeId` shows the result (net, gross, fees, points, hold time, trading day), the fill pairs with the arithmetic from gross to net, the notes, tags by group, screenshots (drop, browse or paste; full-size viewer with arrow keys and a confirmed delete) and a journal-status card. Previous and next follow the order and filters of the list the person came from, through a new `DataSource.neighbors` read (the future `GET /api/v1/trades/{id}/neighbors`). The page is its own lazily loaded chunk (16 KB gzipped); the entry chunk is 132 KB of the 150 KB budget, and importing DOMPurify into the entry chunk alone had cost 18 KB, which is why.

Gaps and decisions:

- **Notes use the Tiptap rich-text editor** (installed 2026-10-03 with the owner's approval, with `@tiptap/react`, `@tiptap/pm` and `@tiptap/starter-kit`), loaded as its own chunk (131 KB gzipped) after the rest of the page. Only what the sanitizer allows can be made: bold, italic, underline, bullet and numbered lists, quote, links (http, https, mailto; `javascript:` and the like are refused with a reason). Code, headings, rules, strike-through and the trailing empty paragraph are switched off. Tiptap's injected `<style>` is off (the content security policy blocks it); its few rules live in `styles.css`. The sanitizer still runs on what is loaded, and the server must run it on write (doc 04).
- **The canvas's "Reviewed" row has no backend field** (nothing in docs 03 or 05 or the code), so the status card shows Notes, Setup tag and Screenshots only. Adding a reviewed flag is a feature decision, not a front-end detail.
- **Fill pair "P&L" is gross** (the export's own number); fees are charged per trade, so the table ends with Gross, Fees and Net rows. The canvas shows a per-pair "Net".
- Tags can be several per group; the backend's `set_trade_tags` replaces a whole group, and the API in section 5 has add and remove per tag, which is what the page uses.
- The sample journal (`lib/demo/journal.ts`) keeps edits in memory only, so a reload resets them; the banner says so.

**Slice 3 (2026-10-03): Import.** Branch `fe/import-screen`, from the canvas's Imports screen at `/import`: choose an account, choose or drop a Performance CSV, see a dry-run preview (six counts, any unreadable rows with their line numbers, and a table of rows with New / Already imported / Belongs to another account), confirm, and later undo from the history, where the undo dialog states what it would remove before anything is removed. The page is its own lazily loaded chunk (3.8 KB gzipped).

How it works today: the sample source reads the file in the browser with a small reader (`lib/demo/csv.ts`) that follows the backend parser's header rule, its row rules and its messages, and keeps the result in memory, so importing the same file twice shows every row as already imported and a pair stored under another account is named. **That reader is for the sample only**: the real parser is `parser.py`, the only one that reads a real file (it also applies the timezone and the P&L cross-check).

Gaps and decisions:

- **The preview needs per-row detail the importer does not produce today.** `FileImportReport` has counts and error lists, not a status for each row. The canvas's preview table needs one (New, Already imported, Belongs to Lucid 100K), so `POST /api/v1/imports/preview` has to return the first rows with a status each, plus the account names for pairs stored elsewhere. That is a small backend addition to plan in M3.
- **Limits are placeholders**: 5 MB and 50,000 rows per file in the sample, to be set by the real limits work (doc 04). The 12 MB upload cap in the API config covers one 10 MB screenshot plus overhead.
- **Several files at once** (the plan in section 5 allows it): the canvas shows one, so the page takes one and says so if more are dropped.
- **Resulting trades** in the sample is an estimate (pairs of one contract whose time ranges overlap count as one trade); the server's `grouping.py` is the authority.
- A shared `Modal` (focus kept inside, Escape and backdrop close, focus returned) now backs the image viewer and the undo dialog.

**Slice 4a (2026-10-03): Settings frame, Preferences, Plan & billing.** Branch `fe/settings-shell`. `/settings` is now a layout with the canvas's five sections (Preferences, Prop accounts, Tags, Plan & billing, Data & privacy) and opens at `/settings/preferences`. **Preferences and Plan & billing run on the real API**, not sample data: Preferences reads and saves time zone, trading day time zone and start, and theme through `PATCH /api/v1/settings` (only the fields that changed are sent; the server's field messages appear beside the field; saving a trading-day change warns first that trades are recalculated, and re-reads what is built from trades). Plan & billing shows the session's plan, where it came from, when it ends and the entitlements, read-only. The sample-data banner is hidden on these two pages, because nothing on them is made up. Prop accounts, Tags and Data & privacy are placeholders inside the frame.

- **`unwrap` now throws an `ApiError`** that carries the status, the server's `detail` (or a plain fallback for a 500) and the per-field messages of a 422, so no page shows a generic "request failed".
- Time zone lists come from `Intl.supportedValuesOf('timeZone')` plus UTC and any zone already in use; the stored value is always the IANA name.
- The canvas's "Plan & billing" shows no payment details: payments are not live, and the page says plans come from an invitation or a code until after the beta. Redeeming a code (`POST /api/v1/grants/redeem`) belongs here once that route exists.
- The main navigation now marks Settings (and Trades) as current inside their sub-pages: the link's own search is ignored when deciding.

**Slice 4b (2026-10-03): Prop accounts.** Branch `fe/prop-accounts`, from the canvas's Accounts screen at `/settings/accounts` (sample data, with the banner). The list of active accounts (stage, size, profit-target progress, net result) with the archived ones behind "Show archived" (outcome, end date, note, Restore); the prop business card (evaluation pass rate, costs, payouts, net, and the accounts that have no cost recorded); and for the picked account, as tabs, **Details** (name, firm, stage, size, starting balance), **Fees** (by contract, with "any other"), **Rules** (profit target, max drawdown, trailing type and stop level, daily loss limit, with progress and the drawdown buffer left) and **Ledger** (costs and payouts, with the likeliest next kind suggested). Dialogs: add an account, and archive one (how it ended, end date, note; a passed evaluation offers to set up the funded account next, pre-filled). The picked account and tab are in the address (`?account=4&tab=rules`). New accounts show up in the import page's account choice.

Gaps and decisions:

- **The rule tracker is not in the backend.** The accounts, fees, archive and ledger exist (`accounts.py`, `ledger.py`, with their rules and messages, which the sample source follows), but nothing computes progress to the profit target or the drawdown left: the canvas shows both, and doc 05 lists the prop-rule tracker after the beta. The sample works them out from closed trades (end-of-day and intraday alike, from trade closes, so intraday is approximate, as the page says). The server will own that arithmetic; until then `AccountProgress` is the shape to return. The trailing-stop level is stored and edited but not yet used in the sample's arithmetic.
- **Pass rate** is passed over passed plus failed evaluations; closed evaluations and funded accounts do not count (`archive_summary`).
- **Archive lives in the panel header**, not at the foot of the Rules tab as in the canvas, so it is reachable from every tab.
- Money typed into a box is read by `parseAmount` (cents, no minus sign, empty means "not set"); the server remains the authority and its refusals are shown in words.
- The sample fees are the ones the sample trades were built on, so changing one does not change the trades yet (the real server recalculates).

**Slice 5a (2026-10-03): Invite / sign-in page.** Branch `fe/invite-screen`, from the canvas's "Invite required / sign in" screen at `/invite` (also `/invite?code=TJ-7KQ2-M9XA` from an invite email). It is **public**: the root layout shows no app header and asks for no session on this path. The code is tidied (trimmed, capitals, spaces as dashes) and checked only for its shape, then the browser goes to `/auth/login?invite=<code>`; "Sign in" goes to `/auth/login?next=/`. Whether a code is real and unused is the server's decision, reported when the person returns from the identity provider.

Open points: the server contract for invite-gated sign-up (`/auth/login?invite=` is an assumption, to settle with the Kinde work, issue S1b); the **Terms page does not exist yet** on the marketing site, so that link will 404 until it is published (doc 04 launch gates); the waitlist link goes to the marketing site's `#join` form.

**Not built yet from the canvas:** Onboarding (five steps: confirm timezone, first account, how to export, upload, dashboard; note the backend has one `timezone` setting, while the canvas separates your zone from the CSV's zone, so that needs a `csv_timezone` setting or the per-import `source_timezone` doc 03 mentions), Admin users and plan override, Admin overview, codes and audit (both read; designs match doc 03 and ADR-012), and Tags management (no canvas screen).

## 8. Appendix: NiceGUI-specific assumptions that no longer apply

Everything in the v1 docs that rested on NiceGUI, and what replaces it. Section numbers are those of the current docs; the v1 text of ADRs is kept in doc 06 on purpose. Rows marked *kept* still appear in the docs as history or as findings about today's code.

| Doc and section | v1 assumption | Now |
|---|---|---|
| README, TL;DR 1 and 3 | "UI plumbing" is fixed in place; keep NiceGUI and deploy one container | Domain kept, UI rebuilt; API and SPA in one image (ADR-010) |
| README, findings: global state | Must become per-tab state | URL state in the SPA; the API keeps none |
| README, decisions D1 | Keep NiceGUI, with a seam for a later rewrite | Superseded; the rewrite is the plan |
| README, sources | NiceGUI storage, single process, sticky sessions plus Redis | Historical (ADR-001); no longer drives a decision |
| README, unverified list | `request` injection in `@ui.page`; `unsafe-eval` in a CSP; "a few hundred concurrent users on one instance" | Void |
| 01, F-T3 | Per-session UI state | Per-browser URL state; no per-session server state |
| 01, accessibility | "The UI already uses aria labels" | The rebuild must keep that |
| 01, performance | p95 page load | Split into API response time and SPA first load, plus a bundle budget |
| 01, section 4 | 30 to 50 concurrent **websocket** sessions; one NiceGUI process is the ceiling | Stateless requests; add instances; unmeasured |
| 01, constraints | Existing stack NiceGUI 3.17 | Being replaced; FastAPI, React and TypeScript added |
| 02, diagram | One NiceGUI process serving UI pages over a websocket | API container serving the SPA and JSON on one origin |
| 02, components: edge | Cloudflare "proxies websockets; confirm in S4" | No websockets; the edge caches `/assets/*` |
| 02, components: app | NiceGUI plus FastAPI routes, one process, no multiple workers | Stateless FastAPI, any number of instances |
| 02, components: jobs | Imports via `run.io_bound` | A thread pool inside the request, behind a semaphore |
| 02, request flow | Page function closure, websocket events, session revocation lagging an open socket | Per-request session check (cached 60 s); no long-lived connections |
| 02, code layout | `ui/` changed for per-tab filters and an auth gate; "seam for a future UI rewrite" | `ui/` deleted after parity; `api/` and `frontend/` added; the seam is the plan |
| 02, hosting: Render | "Websocket support to confirm in S4" | Not needed; S4 checks caching, SPA fallback, deploy behavior and memory |
| 02, sizing and deploys | 2 GB for NiceGUI; 50 simulated websocket sessions; deploys drop sockets | Memory unmeasured and expected lower; HTTP load test; stale tabs handled by backward compatibility |
| 02, `.gitignore` | `.nicegui/` | Drop it when `ui/` is deleted; `node_modules/` and `dist/` already covered |
| 02, scale path | Second NiceGUI instance with sticky sessions and Redis | Add stateless instances; rate limiter to Postgres or Redis |
| 03, tenancy rules | `system_session` never from "UI code" | Never from tenant routes; admin routes never import `tenant_session` |
| 03, session | CSRF by `SameSite=Lax` plus an Origin check on HTTP routes | Four layers (ADR-011) |
| 03, session | Auth state in our table, **not** `app.storage.user`; `storage_secret` for `app.storage.tab` | No NiceGUI storage at all; no per-tab server state |
| 03, authorization | `is_admin` unlocks an aggregates page | CLI-only, MFA-gated, content-blind admin back end (ADR-012) |
| 03, spike S1 note | Page handlers can receive `request`; Socket.IO checks `Origin` | Replaced by the cookie, CSRF, MFA-claim and CSP questions |
| 03, files | `ui.upload(max_file_size, ...)` | `POST` route with edge, header and streaming limits |
| 03, lifecycle | UI "today" defaults from the browser or server clock (`ui/state.py:55` and others) | The API resolves presets in the user's timezone |
| 03, port checklist | `ui/`: replace `open_db()` with `tenant_session`, per-tab state | `ui/` deleted; `api/` and `frontend/` |
| 04, T4 | Escaped rendering (`esc()` in `trades.py:58`), no `ui.html` of user data | React escaping, one `SafeHtml` component, CSP |
| 04, T5 | Websocket floods | Request floods |
| 04, T14 | Cross-site websocket hijack | Removed; replaced by CSRF, with CORS, SPA credential exposure, admin abuse and code abuse added as T15 to T18 |
| 04, findings S3, S7, S10, S14 | Singleton filters, `ui.upload` limits, server clock, NiceGUI CSP | Disappear with `ui/`; the rule behind S3, S7 and S10 carries to the API (kept as findings about today's code) |
| 04, checklist | "No UI code can reach `system_session`"; limits "at proxy, widget and server"; `esc()` audit; "CSP as strict as NiceGUI allows"; websocket `Origin` check | Reworded for routes; `SafeHtml` and `react/no-danger`; a strict CSP; websocket item removed |
| 04, privacy | Cookies include "NiceGUI tab storage" | Session cookie, OIDC state cookie, `localStorage` for UI preferences |
| 05, tier 0 | "Per-tab state" in tenancy | URL state; API and SPA rows added |
| 05, spikes | S1 NiceGUI multi-user; S3 `ui.image`; S4 websockets through Cloudflare | S1 SPA and cookie auth; S3 `<img>` under the CSP; S4 one-image deploy |
| 05, risks | R2 "NiceGUI limits found late" | Replaced by "SPA rebuild overruns" |
| 05, test strategy | NiceGUI `user` test fixture; 200 websocket sessions | Vitest, React Testing Library, MSW, Playwright; HTTP load test |
| 05, tier 2 | Public API via "FastAPI routers per doc 02 section 5" | The API exists from day one |
| 06, ADR-001 | Keep NiceGUI | **Superseded** by ADR-010 (text kept) |
| 06, ADR-003 | Own sessions because `app.storage.user` is file-backed | Own sessions for the reasons in ADR-011; amended |
| 06, ADR-005 | Confirm websocket handling in S4 | Amended; not needed |
