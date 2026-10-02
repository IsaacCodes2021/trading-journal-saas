# 06. Decisions (ADRs)

Status of every decision below is **Proposed** until you confirm it, and the spikes in doc 05 can amend any of them. Append the spike conclusions to the relevant ADR rather than rewriting history.

**Revision 2026-10-02.** ADR-001 is **superseded** by ADR-010 (FastAPI + React). ADR-003, 005, 006 and 007 carry dated amendment notes; ADR-010, 011 and 012 are new. The original text of every earlier ADR is kept as written. Milestone numbers inside ADR-001 to ADR-009 refer to roadmap v1; doc 05 section 3 maps old numbers to new ones.

---

## ADR-001: Keep NiceGUI for the launch UI

**Status: Superseded by ADR-010 (2026-10-02).** The text below is the original decision, kept unchanged for history.

**Context.** The UI is about 3,000 lines of NiceGUI (Python, server-rendered over a websocket, Quasar components, ECharts). Hosting it for many users raises two questions: can it serve them, and is it polished enough to sell?

| Option | For | Against |
|---|---|---|
| **A. Keep NiceGUI** | No rewrite; one language; domain and UI stay together; FastAPI underneath so JSON routes can be added later | One process per instance (no multi-worker; scaling out needs sticky sessions + Redis); per-session server memory; CSP is awkward; less visual control than a custom front end |
| B. Rewrite as React/Next.js + FastAPI | Full UI control, normal horizontal scaling, native-app path | Rewrites about 3,000 UI lines and adds a second language, build chain and auth integration; weeks to months with no new user value |
| C. Another Python UI framework | | Same rewrite cost as B with less ecosystem |

**Decision: A.** Capacity is not the constraint: 1,000 registered users is roughly 30 to 50 concurrent sessions (doc 01), and a single instance is reported to handle a few hundred (a forum estimate; load-test in M8). Marketing pages live in a separate static site, so NiceGUI's weakness there is irrelevant.

**Consequences.** Single instance; deploys drop websockets for seconds; auth integrates through Starlette middleware (spike S1); we keep the domain and data layers free of UI imports so B stays possible.

**Revisit when:** sustained 300+ concurrent sessions or memory pressure; a native mobile app is wanted; the CSP or accessibility needs of a customer cannot be met.

---

## ADR-002: Postgres with shared schema, `user_id` and forced RLS

**Context.** Every tenant table needs isolation. A single missed filter among about 100 SQL statements is a breach.

| Option | Isolation strength | Ops | Port effort | Scale | Notes |
|---|---|---|---|---|---|
| **A. Postgres, shared schema, RLS + composite FKs** | High, enforced by DB | Managed backups and PITR | Medium (dialect port, doc 03 section 9) | Horizontal-ready | Standard SaaS pattern |
| B. Postgres, shared schema, app-level `WHERE user_id` only | Depends on discipline | Same | Medium | Same | Rejected: no safety net |
| C. **SQLite file per user** | Highest, by construction | You manage N files; backups, migrations over N DBs; one node | **Lowest**; `open_db()` becomes `open_db(user_id)` | One node only | No cross-user analytics; billing and users still need a control DB |
| D. Postgres schema per tenant | High | Migrations over N schemas; catalog bloat | Medium | Poor beyond thousands | Rejected |

**Decision: A.** Managed PITR on a database users cannot recreate is the deciding factor, along with PaaS-friendliness (no reliance on a persistent local disk) and the ability to run a second instance later. RLS plus composite FKs plus a defaulted `user_id` (doc 03 section 1) gives three independent safeguards.

**Honest note on C.** It is a legitimate, cheaper option for this codebase and I would not call it wrong. It loses on backup/restore tooling, the single-node ceiling, and a later Postgres migration being a per-tenant ETL.

**Consequences.** All 12 tenant tables change; money stays exact via `NUMERIC`; CI needs Postgres; a one-time data migration; local-first mode goes away (ADR-008).

**Revisit when:** spike S2 shows the port exceeds roughly 90 hours or surfaces blocking incompatibilities; or you decide self-hosted/local mode is a product feature. Then reconsider C.

---

## ADR-003: Hosted identity provider (OIDC) with our own server-side sessions

**Context.** Sign-up, login, reset, verification, MFA and brute-force defense are the riskiest code to write.

| Option | For | Against |
|---|---|---|
| **A. Hosted IdP over OIDC; own `users` and `sessions` tables** | Offloads the riskiest code; free tiers advertise 25,000 MAU (verify); MFA, breach checks included | Moderate lock-in; another subprocessor; a monthly cost above the free tier |
| B. Self-managed (Authlib + argon2id + pyotp + own email flows) | Full control; no vendor | 2 to 3 more weeks; you own every flaw and the on-call for them |
| C. Social login only (Google, GitHub) | No passwords at all; least code | Excludes people without those accounts; weaker control over MFA |

**Decision: A**, provider chosen in spike S1b against the criteria in doc 03 section 4. Sessions are **our own opaque, hashed, revocable tokens in Postgres**, not `app.storage.user`, because NiceGUI's user storage is file-backed by default and not centrally revocable.

**Consequences.** `users.idp_subject` links identity; deletion must call the IdP's management API; login latency includes a redirect; we need a tiny fake IdP for tests.

**Revisit when:** IdP pricing becomes material (well above 25,000 MAU); a required feature (for example enterprise SSO) is missing; or the provider's reliability disappoints. Exit path is documented: export users, switch to plan B.

**Amendment (2026-10-02, ADR-010 and ADR-011).** The own-session decision stands. The NiceGUI reason (`app.storage.user` is file-backed) no longer applies because there is no NiceGUI; the session cookie is now consumed by a stateless FastAPI API on the same origin as the SPA, with CSRF protection (ADR-011). Spike S1b gains two criteria because admin access requires recent MFA (ADR-012): the ID token carries an MFA indicator (`amr` or `acr`), and the provider supports step-up re-authentication. Whether each shortlisted provider offers these is **unverified**.

---

## ADR-004: Cloudflare R2 for screenshots, accessed only through authorization

**Context.** Screenshots are sensitive. Today they sit in a public static folder.

| Option | For | Against |
|---|---|---|
| **A. R2, private, presigned GET via `/files/{id}`** | Zero egress cost; S3 API (portable); 10 GB free then $0.015 per GB-month | Another vendor; presigned-URL handling |
| B. S3 | Mature ecosystem | Egress charges; more configuration |
| C. Postgres `bytea` | One system; transactional with the row | Bloats the DB and backups; slow serving; bad for 100 GB |
| D. Local disk on the host | Simple | Ephemeral on most PaaS; no redundancy |

**Decision: A.** Pillow decode and re-encode on upload; thumbnails; quota by `size_bytes`; orphan job that reports before it deletes (doc 03 section 6).

**Revisit when:** volume passes about 1 TB (lifecycle/Infrequent Access) or a customer demands a specific cloud or region.

---

## ADR-005: Render for hosting, packaged as a Docker image

**Context.** One part-time developer; the data is irreplaceable; budget about $60 per month.

| Option | Verdict |
|---|---|
| **Render**: flat pricing, managed Postgres with PITR (3-day on Hobby workspace, 7-day on Pro), GitHub auto-deploy | **Chosen** for low ops and managed recovery. About $21 to 34 per month for web + DB (third-party figure) |
| Railway | Cheaper (about $10 to 15) and good DX; backup guarantees for its Postgres need checking before relying on it |
| Fly.io | Excellent compute pricing and regions; its Postgres is unmanaged, its managed Postgres starts higher |
| Hetzner VPS + Docker/Coolify | Cheapest; you become DBA and sysadmin |

**Decision: Render**, with a **Dockerfile** so moving is a one-day job. Confirm websocket behavior and memory in spike S4. Choose a US region unless EU users matter (README question 4).

**Revisit when:** costs matter more than your time (move to a VPS), a multi-region need appears (Fly.io), or Render's websocket handling disappoints in S4.

**Amendment (2026-10-02, ADR-010).** Websockets are no longer needed, so Render's websocket handling is no longer a risk. The container now holds the API and the built SPA (one image, one origin); the marketing site stays on Cloudflare Pages. Because the API is stateless it can run on several instances behind Render's load balancer, and memory per user should be far below NiceGUI's server-held UI state (to be measured in S4; the 2 GB sizing in doc 02 is a starting guess, not a requirement). Whether Render deploys a single instance without dropping in-flight requests is **unverified**.

---

## ADR-006: Private GitHub repo, trunk-based, GitHub Actions CI, deploy gated on CI

**Context.** There is no version control today. GitHub Free offers no branch protection or rulesets on private repos ([docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)).

| Option | Verdict |
|---|---|
| **Private repo + Actions + host deploys only after CI passes; add GitHub Pro to protect `main` at launch** | **Chosen** |
| Public repo (free protections, free code scanning) | Gives away the product code and any mistakes; rejected unless you decide to open-source |
| GitLab/Bitbucket | You asked for GitHub; no strong reason to differ |

**Decision.** Branches `feat/*` and `fix/*`, squash-merge to `main`; `main` deploys to staging; fast-forwarding `production` deploys to production. CI: ruff (with `S` rules), pytest on Postgres, `pip-audit`, gitleaks, Docker build. Dependabot on. Free-plan substitutes for CodeQL are ruff-`S` and `pip-audit`.

**Revisit when:** a second contributor joins (require reviews, add CODEOWNERS), or a legal entity exists (move to an organization).

**Amendment (2026-10-02, ADR-010).** M0 is complete: the private repo exists and CI is green on `main` (lint, tests, dependency audit, secret scan, image build). CI gains a front-end job (pnpm install with a frozen lock file, ESLint, type check, Vitest, build, `pnpm audit`, and a check that the generated API types are up to date) and the image build becomes multi-stage. Dependabot adds the npm ecosystem (it should read `pnpm-lock.yaml`; confirm in M1). Everything else is unchanged.

---

## ADR-007: Billing via a hosted provider, model decided first

**Context.** Not decided: free, paid, or free-then-paid.

**Decision.** Build **entitlements** now as code (cheap, needed for quotas anyway) and the **billing integration late (M7)**. Lean toward a **merchant of record** (Paddle or Lemon Squeezy) so VAT and sales tax are not your problem as a solo seller; the cost is a higher fee and less control. Stripe plus Stripe Tax is the lower-fee alternative if you are comfortable carrying tax registration. **Apply for the account in M1**; some providers restrict financial-services categories and I have not verified current policies for a journaling tool.

**Consequences.** `subscriptions` mirrors provider state; webhooks are idempotent; lapse is read-only, never deletion.

**Revisit when:** fees become material (switch to Stripe), or you need invoicing features the provider lacks.

**Amendment (2026-10-02, ADR-012).** The billing integration is milestone M9 in the new roadmap (it was M7). Entitlements now resolve an *effective plan* from three sources (operator override, local grant, billing mirror), and discount codes are created through the provider's API from the admin back end. Both are specified in ADR-012 and doc 03 section 5.

---

## ADR-008: Retire local-first mode; freeze it as a tag

**Context.** The README promises "no cloud, no accounts, no telemetry". Supporting both a local SQLite mode and a hosted Postgres mode doubles the data layer, tests and bugs.

**Decision.** Tag the current state `v0.1.0-local`, then go hosted only. Data portability is delivered through the export feature, not a second storage engine. Change the README's privacy section to describe the hosted product honestly.

**Consequences.** Local-first users (today: you) migrate once with the script in doc 03 section 8. If a self-hosted edition is ever wanted, it would be a separate, deliberate product decision.

**Revisit when:** user demand for self-hosting is real and measurable (it can run the same Docker image plus Postgres).

---

## ADR-009: Generalize fill-pair identity now

**Context.** Fill pairs are uniquely identified by two Tradovate integer fill IDs. Other platforms identify executions differently.

**Decision.** In the Postgres schema from day one, use `source text`, `buy_ref text`, `sell_ref text` with `UNIQUE (user_id, source, buy_ref, sell_ref)`. Existing rows get `source = 'tradovate_performance'` and the IDs as text. No adapters are built until beta users provide real sample files (doc 05, note B).

**Consequences.** Slightly less compact keys; all dedupe logic in `importer.py` reads `(source, buy_ref, sell_ref)`; adding a platform later needs no schema change.

**Revisit when:** a source appears that cannot be reduced to buy/sell references (then consider a first-class `executions` table).

---

## ADR-010: FastAPI JSON API with a React and TypeScript SPA (supersedes ADR-001)

**Status:** Proposed, 2026-10-02. Supersedes ADR-001.

**Context.** ADR-001 kept NiceGUI to avoid a rewrite. Two things have changed. First, the NiceGUI UI is a single-user proof of concept (2,752 lines, no users yet), so a rewrite is as cheap as it will ever be. Second, NiceGUI's model is the platform's scaling ceiling: one process per instance with UI state held on the server for every open connection (it does not support multiple workers; scaling out needs sticky sessions plus Redis, see the [NiceGUI discussion](https://github.com/zauberzeug/nicegui/discussions/4483)), and a strict CSP is awkward with its inline scripts and runtime template compiler (unverified). For a paid product, UI control, accessibility and horizontal scaling matter.

| Option | For | Against |
|---|---|---|
| A. Keep NiceGUI (ADR-001) | No rewrite; one language | Single-process ceiling and per-connection server memory; weak CSP story; less UI control |
| **B. FastAPI JSON API + Vite/React/TypeScript SPA, served from the API container** | Stateless API scales horizontally; one external script allows a normal CSP; full UI control; types generated from OpenAPI; any later mobile app or public API reuses the same JSON API | Rewrite of about 2,750 UI lines plus about 60 routes; a second language and build chain; npm supply-chain exposure; the developer is new to TypeScript |
| C. FastAPI + Next.js, static export | Familiar framework, file router | A static export has no rewrites, middleware, cookies, redirects or headers, and dynamic route params need build-time values ([docs](https://nextjs.org/docs/app/guides/static-exports)); a strict CSP depends on an experimental hash-based option because nonces force dynamic rendering ([docs](https://nextjs.org/docs/app/guides/content-security-policy)) |
| D. FastAPI + Next.js as a Node service | SSR available; rewrites can proxy `/api` | A second runtime to patch and secure; nonce CSP makes every page dynamic; two services to deploy |
| E. Server-rendered templates (Jinja/HTMX) | Python only | Fits poorly with the editor, virtualized table and chart interactions this app needs |

**Decision: B.** FastAPI exposes `/api/v1` over the existing domain modules; a Vite + React 19 + TypeScript SPA (tooling in doc 07) is built into the same image and served by FastAPI on one origin (ADR-011). Next.js was considered at the owner's request and set aside after the checks in C and D. Python keeps `parser.py`, `grouping.py`, `trades.py`, `finance.py`, `timeutil.py`, `symbols.py`, `models.py` and their tests; money stays exact `Decimal` and crosses the API as strings (doc 07 section 2).

**Consequences.**
- Effort roughly doubles: about 430 to 650 hours to private beta and 650 to 1,000 to public launch (doc 05; it was 200 to 300 and 330 to 525).
- NiceGUI-only concerns disappear: websocket hijacking (old T14), per-tab server state, NiceGUI CSP caveats, sticky sessions. New concerns: CSRF and CORS, npm supply chain, token handling in the SPA, API and SPA drift (doc 04).
- Filters become URL state; imports become stateless (the client re-uploads at commit and the server verifies the hashes).
- Deploys no longer drop websockets, but an open tab runs the old SPA against the new API for a while: keep the API backward compatible for one release and show a reload banner.
- `ui/` stays as the behavioral reference until M4 exits, then is deleted. Doc 07 section 8 lists every NiceGUI assumption in the docs that no longer applies.

**Revisit when:** bundle size or build time becomes a problem; SEO or server rendering of app pages becomes a requirement (then re-evaluate Next.js in server mode); or the TypeScript toolchain proves too costly for one developer after M4 (the API stays regardless).

---

## ADR-011: Same-origin session cookie with layered CSRF protection

**Status:** Proposed, 2026-10-02.

**Context.** The SPA authenticates to the API with the opaque session from ADR-003. The `__Host-` cookie prefix requires `Secure`, `Path=/` and **no `Domain`**, so the cookie belongs to exactly one host. That decides how the browser may reach the API.

| Option | For | Against |
|---|---|---|
| **A. SPA served by the API container, one origin** | `__Host-` cookie works; no CORS at all; CSP `connect-src 'self'`; one deploy, so the SPA and the generated API types are always the same version | Gives up Cloudflare Pages' independent static deploys and per-PR previews; a front-end change redeploys the API (cheap, since it is stateless) |
| B. SPA on Pages, `/api` proxied to Render through a Pages Function or Worker (one origin at the edge) | Keeps the original layout | An extra hop and Cloudflare-specific configuration on every API call; request quotas on the free tier (unverified); two deploys to keep in step |
| C. Separate subdomains (`app.` and `api.`) | No proxy | CORS with credentials; the cookie needs `Domain=`, which rules out `__Host-` and widens exposure to every subdomain; most moving parts |

**Decision: A.** Cloudflare sits in front as DNS, TLS, WAF and a cache for the hashed `/assets/*`. The marketing site stays on Cloudflare Pages at the apex domain and the app lives on `app.<domain>`.

CSRF protection has four layers, all enforced on unsafe methods: (1) `SameSite=Lax` cookie; (2) the `Origin` header, or `Sec-Fetch-Site: same-origin`, is validated and requests with neither are rejected; (3) a per-session token (an HMAC of the session id with a server secret, so nothing is stored) returned by `GET /api/v1/session` and sent as `X-CSRF-Token`; (4) JSON or multipart bodies only. No CORS middleware is installed; if one is ever added it must never combine a wildcard origin with credentials. `/auth/callback` validates `state`, `nonce` and PKCE, and restricts the `next` parameter to same-origin paths (open-redirect defense).

**Consequences.** No credentials in JavaScript-accessible storage; the SPA never sees the session secret. Moving the SPA to Pages later means switching to option B, and the API contract does not change.

**Revisit when:** front-end deploy frequency makes the coupling painful, or a separate team owns the front end (then B with a reviewed proxy).

---

## ADR-012: Admin back end: CLI-granted, MFA-gated and blind to user content

**Status:** Proposed, 2026-10-02.

**Context.** The operator needs to manage users, override plans, issue discounts and grants, and review an audit trail, without ever being able to read a user's trades, notes or screenshots, and without the admin surface becoming the weakest link.

| Question | Decision | Why |
|---|---|---|
| Where does admin live? | In the same API and SPA, under `/api/admin/v1` and `/admin/*`, as a lazy-loaded chunk | A separate admin service is a second deployment and a second auth system for one operator. The separation that matters is enforced by a DB role, not by deployment |
| Who is an admin? | `users.is_admin` **and** a session with MFA satisfied recently. `is_admin` changes only through the CLI (`python -m tradejournal admin grant`, `revoke`, `list`), run as a one-off job with the owner-level database URL, so that credential is not on the web processes or, by default, the dev machine. Column-level grants stop the API's DB roles from updating `is_admin` even through a bug or SQL injection | Admin cannot be created from the UI, the API or a stolen session |
| How is MFA proven? | The OIDC ID token (`amr` or `acr`) at login is recorded as `sessions.mfa_at`. Admin routes require it within 15 minutes, otherwise 403 `mfa_required` and the SPA starts a step-up login. Provider support for these claims is **unverified** (a criterion in spike S1b) | MFA state comes from the IdP, not from a client claim |
| How is "never sees content" enforced? | `system_session("admin")` maps to DB role `app_admin`, whose grants cover only: `users` (chosen columns), `subscriptions` (read), `sessions` (read, revoke), `plan_overrides`, `grant_codes`, `grant_redemptions` (read), `invites`, `discount_codes`, `user_usage` (read), `data_exports` (insert, read metadata), `audit_log` (insert, read). **No SELECT on any tenant table.** Usage counts come from `user_usage`. A CI test connects as that role and proves it | A promise in code is weaker than a privilege the database refuses |
| Plan override | The effective plan is **computed at read time**: an active operator override wins; otherwise the higher of an active local grant and the billing mirror; otherwise free. It is never stored in a `users.plan` column. Webhooks and the nightly reconcile write only `subscriptions`, so they cannot overwrite an override. An override has a plan, an optional expiry and a required reason; at most one is active per user | Precedence and durability by construction |
| Codes | Discount codes are created through the payment provider's API (a local mirror lists them). Local grant codes give N days of a plan, one redemption per user, hashed at rest. The `invites` table is extended for multi-use beta codes with an optional bundled grant | Money-affecting codes live where payments are; access grants stay local |
| Audit | Every admin mutation and every view of a user's detail page goes to `audit_log`, which is append-only (INSERT-only grants plus a trigger that rejects UPDATE and DELETE) | Tamper resistance short of the owner role |
| Impersonation | Not offered. Support asks the user to share what is needed | Preserves the content-blind guarantee |

**Consequences.** One more DB role and grant list to maintain and test; admin routes cannot import `tenant_session` and tenant routes cannot import `system_session` (an import-boundary test); the admin SPA chunk is not a security control, only the server-side checks are.

**Revisit when:** a second operator or support staff is hired (add roles, least privilege per role, an approval flow for overrides), or compliance requires separate admin infrastructure.
