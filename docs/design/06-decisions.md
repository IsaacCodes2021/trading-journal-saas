# 06. Decisions (ADRs)

Status of every decision below is **Proposed** until you confirm it, and the spikes in doc 05 can amend any of them. Append the spike conclusions to the relevant ADR rather than rewriting history.

---

## ADR-001: Keep NiceGUI for the launch UI

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

---

## ADR-007: Billing via a hosted provider, model decided first

**Context.** Not decided: free, paid, or free-then-paid.

**Decision.** Build **entitlements** now as code (cheap, needed for quotas anyway) and the **billing integration late (M7)**. Lean toward a **merchant of record** (Paddle or Lemon Squeezy) so VAT and sales tax are not your problem as a solo seller; the cost is a higher fee and less control. Stripe plus Stripe Tax is the lower-fee alternative if you are comfortable carrying tax registration. **Apply for the account in M1**; some providers restrict financial-services categories and I have not verified current policies for a journaling tool.

**Consequences.** `subscriptions` mirrors provider state; webhooks are idempotent; lapse is read-only, never deletion.

**Revisit when:** fees become material (switch to Stripe), or you need invoicing features the provider lacks.

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
