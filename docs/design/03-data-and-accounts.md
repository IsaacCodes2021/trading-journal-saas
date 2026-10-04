# 03. Data model, accounts, billing, files

*Revised 2026-10-02 for ADR-010 to ADR-012: the UI is now a React SPA over a stateless FastAPI API, so sessions, CSRF and client state change (section 4), and the admin back end adds tables and a plan-resolution rule (sections 2 and 5).*

## 1. Tenancy model

**Shared database, shared schema, `user_id` on every tenant-owned table, row-level security (RLS) enforced by Postgres.** Rationale and alternatives are in [ADR-002](06-decisions.md).

Three layers, each independently sufficient to stop a cross-tenant leak:

1. **RLS policy** on every tenant table (`FORCE ROW LEVEL SECURITY`), keyed on a per-transaction setting.
2. **Composite foreign keys** `(user_id, parent_id)`, so a row physically cannot reference another user's parent row even if application code is wrong.
3. **Column default** `user_id DEFAULT app_user_id()`, so existing `INSERT` statements need no new parameter and an insert with no tenant context fails on `NOT NULL`.

```sql
-- Roles: `owner` runs migrations. `app_rw` serves requests: NOT superuser, NOT BYPASSRLS.
-- Managed providers often hand you a powerful default role; create these explicitly.

CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;
-- NULLIF matters: after a transaction-local setting ends, Postgres reports '' (not NULL),
-- and ''::uuid would raise instead of cleanly matching nothing.

CREATE TABLE accounts (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL DEFAULT app_user_id() REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL,
    -- ... existing columns ...
    UNIQUE (user_id, id)                               -- target of composite FKs
);
CREATE UNIQUE INDEX accounts_user_name ON accounts (user_id, lower(name));

CREATE TABLE trades (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL DEFAULT app_user_id(),
    account_id  bigint NOT NULL,
    -- ... existing columns ...
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);

ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON trades
    USING      (user_id = app_user_id())
    WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON trades TO app_rw;
```

**Per-request context.** `infra/db.py` exposes one entry point:

```python
with tenant_session(user_id) as conn:        # pool.connection(); BEGIN
    # SELECT set_config('app.user_id', %s, true)   <- true = transaction-local
    ...                                       # existing domain code, same signatures
# COMMIT (or ROLLBACK); the setting dies with the transaction
```

In the API this sits behind a FastAPI dependency: `user = Depends(current_user)` resolves and validates the session, and the route handler then opens `tenant_session(user.id)`.

`set_config(..., true)` is transaction-scoped, so it is safe with a connection pool (and with PgBouncer in transaction mode). Never use a session-level `SET`; it would leak identity to the next request on that pooled connection.

**Rules that go with it**

- Every index leads with `user_id`, so the policy predicate is an index prefix.
- A separate, tiny `system_session(profile)` exists only for four profiles: `login` (lookup by IdP subject, session lookup by token hash, create sessions), `webhook` (billing events), `sweep` (background jobs) and `admin` (the operator API). Each profile maps to its own DB role with a narrow grant list, and the `admin` role has **no grants on tenant tables** (ADR-012). It is used in a handful of audited places and **never from tenant routes**; admin routes never import `tenant_session`. An import-boundary test enforces both directions. **Built in M2 (migration 0004, `pg/system.py`):** four login roles (`app_login`, `app_webhook`, `app_sweep`, `app_admin`), one pool each, with column-level grants on `users` and nothing on any tenant table. Only `app_admin`'s read of `user_usage`, through a read-only `admin_read` policy, is an exception. The sweep deletes a user and the foreign keys cascade through every tenant table without any grant on them: referential actions are not checked against the caller's table privileges (tested). Sessions, the audit log and billing tables, and their grants, come with M5, M9 and M10.
- **CI schema lint** (a test): every table with a `user_id` column has RLS enabled and forced, has a policy, and has an index starting with `user_id`; the `app_rw` role is not superuser and lacks `BYPASSRLS`.
- **CI two-tenant suite:** for each domain function, create data as user A, call it as user B, assert nothing is visible and nothing can be written or changed. Parametrize over all tenant tables with a generic `SELECT count(*)` as B must be 0.
- Postgres 15+ gotcha for composite FKs: `ON DELETE SET NULL` would also null `user_id`. Use the column-list form `ON DELETE SET NULL (trade_id)` for `fill_pairs.trade_id`.

## 2. Schema delta

### Changed tables (all 12 get `user_id`, RLS, composite FKs, per-user uniqueness)

`accounts`, `fee_schedules`, `imports`, `trades`, `fill_pairs`, `import_fill_pairs`, `tag_groups`, `tags`, `trade_tags`, `day_notes`, `screenshots`, `account_ledger`.

| Constraint today (global) | Becomes |
|---|---|
| `accounts.name UNIQUE NOCASE` | `UNIQUE (user_id, lower(name))` |
| `tag_groups.name UNIQUE NOCASE` | `UNIQUE (user_id, lower(name))` |
| `tags UNIQUE (group_id, name)` | `UNIQUE (user_id, group_id, lower(name))` |
| `fill_pairs UNIQUE (buy_fill_id, sell_fill_id)` | `UNIQUE (user_id, source, buy_ref, sell_ref)` (identity generalized per ADR-009; `source` is `tradovate_performance` for today's data); the existing "belongs to another account" skip logic keeps working within one user |
| `day_notes PRIMARY KEY (trade_date)` | `PRIMARY KEY (user_id, trade_date)` |
| `settings (key)` | replaced by `user_settings` |
| `screenshots.stored_name UNIQUE` | `object_key text UNIQUE` plus `content_type`, `size_bytes`, `sha256`, `width`, `height`, `thumb_key` |

### New tables

| Table | Key columns | Purpose |
|---|---|---|
| `users` | `id uuid PK`, `idp_subject UNIQUE`, `email` (unique on `lower`), `email_verified_at`, `status` (`active`/`suspended`/`pending_deletion`), `is_admin`, `tos_version`, `tos_accepted_at`, `created_at`, `last_login_at`, `deletion_scheduled_at` | The account. Not under tenant RLS (accessed via `system_session`). **`is_admin` is not writable by any request-serving role**: column-level grants leave it to the owner-level CLI only |
| `sessions` | `id`, `public_id` (random uuid, what the CSRF token derives from; **built in M5, migration 0007**), `user_id`, `token_hash UNIQUE` (sha256), `created_at`, `last_seen_at`, `expires_at`, `revoked_at`, `mfa_at`, `user_agent`, `ip_hash` | Server-side sessions; revocable; survive deploys. `mfa_at` records when the IdP last reported MFA (used by admin routes) |
| `user_settings` | `user_id PK`, `timezone`, `csv_timezone` (nullable: null means the same as `timezone`; migration 0006), `trading_day_timezone`, `trading_day_start`, `theme` | Replaces global `settings` |
| `subscriptions` | `user_id PK`, `provider`, `provider_customer_id`, `provider_subscription_id`, `plan`, `status`, `trial_ends_at`, `current_period_end`, `cancel_at_period_end`, `updated_at` | Local mirror of billing state |
| `billing_events` | `provider_event_id PK`, `type`, `payload jsonb`, `received_at`, `processed_at` | Webhook idempotency and audit |
| `audit_log` | `id`, `user_id NULL` (subject), `actor_user_id NULL`, `actor` (user, admin, `cli:<name>`, system), `action`, `entity`, `entity_id`, `ip_hash`, `meta jsonb`, `created_at` | Append-only security events (login, failed login, email/password change, import, undo, export, delete) **and every admin mutation and admin view of a user's detail page**. Request-serving roles have INSERT only (admin also SELECT), and a trigger rejects UPDATE and DELETE; retention deletes run as `owner` |
| `data_exports` | `id`, `user_id`, `status`, `object_key`, `requested_at`, `expires_at` | Async export jobs (also triggerable by an admin; the admin sees status only) |
| `invites` | `id`, `code_hash UNIQUE`, `label`, `created_by`, `email NULL`, `max_uses` (default 1), `grant_code_id NULL`, `expires_at`, `revoked_at` | Private-beta gating. Extended from single-use: multi-use allowed, hashed at rest, and an invite can bundle a plan grant |
| `invite_redemptions` | `invite_id`, `user_id`, `redeemed_at`, `UNIQUE (invite_id, user_id)` | Replaces the old `used_by` / `used_at` columns |
| `plan_overrides` | `id`, `user_id`, `plan`, `expires_at NULL`, `reason NOT NULL`, `created_by`, `created_at`, `revoked_at`, `revoked_by`, `revoke_reason` | Operator override of a user's plan. **Partial unique index `(user_id) WHERE revoked_at IS NULL`**: at most one active override. History is kept |
| `grant_codes` | `id`, `code_hash UNIQUE`, `label`, `plan`, `days`, `max_redemptions NULL`, `expires_at NULL`, `created_by`, `created_at`, `revoked_at` | Local codes giving N days of a plan |
| `grant_redemptions` | `id`, `grant_code_id`, `user_id`, `plan`, `starts_at`, `ends_at`, `UNIQUE (grant_code_id, user_id)` | One redemption of a code per user |
| `discount_codes` | `id`, `provider`, `provider_code_id`, `code`, `description`, `terms jsonb`, `expires_at NULL`, `created_by`, `created_at`, `deactivated_at` | Local mirror of discounts created through the payment provider's API, so the admin can list and audit them. Redemption happens in the provider's checkout |
| `user_usage` | `user_id PK`, `accounts_count`, `trades_count`, `screenshots_count`, `storage_bytes`, `last_import_at`, `updated_at` | Counters maintained by application code in the same transaction as the change. Used for quotas **and for the admin back end, which therefore never reads tenant tables**. **Built in M2 (`pg/usage.py`, migration 0003): `usage.refresh` recounts instead of incrementing, so a counter cannot drift; `storage_bytes` stays 0 until files have a size (M6). The admin role's read access to these rows comes with the system roles** |

Retention: `sessions` rows deleted 30 days after expiry; `audit_log`, `plan_overrides` and `grant_*` kept at least 12 months; `billing_events` kept 7 years (tax) with PII minimized.

## 3. Type mapping (SQLite to Postgres)

| SQLite today | Postgres | Effect on code |
|---|---|---|
| Money, prices, fee rates as TEXT decimal strings | `NUMERIC(20,2)` for money (**amended 2026-10-03 from `(20,6)`**: the domain rounds every amount to cents, and six decimals reached the API as `"50000.000000"`), `NUMERIC(20,6)` for a per-contract fee rate, plain `NUMERIC` for prices, tick sizes and derived points | psycopg returns `Decimal` natively. `finance.py` is unchanged; the `money_str()` / `plain()` string helpers at the storage boundary are replaced by passing `Decimal` |
| Timestamps as TEXT `YYYY-MM-DDTHH:MM:SSZ` | `timestamptz` | psycopg returns aware `datetime`; `to_utc_iso` / `from_utc_iso` at the boundary go away |
| `trade_date` TEXT | `date` | |
| `INTEGER PRIMARY KEY` + `cursor.lastrowid` | `bigint GENERATED ALWAYS AS IDENTITY` + `INSERT ... RETURNING id` | Every insert site |
| `COLLATE NOCASE` | `lower(...)` in unique indexes and `ORDER BY` (or the `citext` extension) | `journal.py` lines 113, 186, 227 |
| `CHECK (color GLOB '#[0-9a-f]...')` | `CHECK (color ~ '^#[0-9a-f]{6}$')` | |
| `INSERT OR IGNORE` | `ON CONFLICT DO NOTHING` | `journal.py`, `importer.py` |
| `INTEGER` 0/1 flags | `boolean` | `trail_lock_enabled` |
| `?` placeholders, `sqlite3.Row` | `%s`, `dict_row` | All call sites |
| `IN (?, ?, ...)` built with `'?' * n` | `= ANY(%s)` with a list | `queries.py` |
| `PRAGMA user_version` + `executescript` migrations | `schema_migrations` table + advisory-locked runner | `db.py` |
| Savepoint helper `db.transaction(rollback=True)` used by import previews | `conn.transaction()` nests as savepoints; implement "rollback after run" by raising a private sentinel exception | Preview semantics preserved |
| Alias in `HAVING` (`HAVING has_notes OR shots > 0`, `journal.py:360`) | Postgres rejects aliases in `HAVING`; repeat the expression | One known trap; the test suite will find others |
| Notes HTML as TEXT | `text`, **sanitized on write** (doc 04) | |

Money crosses the HTTP API as JSON strings (doc 07 section 2), so `NUMERIC`, `Decimal` and the wire format stay exact end to end.

## 4. Authentication and sessions

### Why a hosted IdP

Passwords, MFA, email verification, reset flows, breached-password checks and login throttling are the highest-risk code to write and the cheapest to rent. Free plans differ in what they include. Read on 2026-10-02 (doc 08): **Auth0's free plan has 25,000 MAU but no MFA**, while **Kinde's has 10,500 MAU with authenticator-app MFA, Google login and a custom domain**. Both are above launch scale; MFA is the feature that decides. The cost is moderate lock-in, which we limit by owning our own `users` and `sessions` tables and speaking standard OIDC.

**Shortlist and selection criteria** (decided in spike S1b on 2026-10-04: Kinde, see ADR-003 in doc 06): Auth0, Kinde, Clerk, WorkOS AuthKit, Supabase Auth. Must have: Google sign-in, email + password, TOTP MFA, email verification, custom domain/branding, standard OIDC with PKCE, a management API to delete users, ability to export users, free tier of at least 5,000 MAU **that includes TOTP MFA**, and **an MFA indicator in the ID token (`amr` or `acr`) plus step-up re-authentication**, which admin access needs (ADR-012; doc 08: documented for Auth0, WorkOS and Supabase, and only in an unmerged docs pull request for Kinde, the front-runner).

**Fallback if none fit:** self-managed with `Authlib` (Google OIDC), `argon2-cffi` (argon2id), TOTP via `pyotp`, our own email tokens. Roughly 2 to 3 weeks more, plus an ongoing security-maintenance burden. Documented only as plan B.

### Login flow (OIDC authorization code with PKCE)

```
SPA               API (/auth/*)                      IdP
  | GET /api/v1/session  -> 401
  | location = /auth/login?next=/trades
  |---------------->| create state, nonce, PKCE verifier (signed short-lived cookie); keep `next`
  |<-- 302 ---------|-----------------------------> /authorize
  |  user authenticates at IdP (password / Google / MFA) |
  |<------------------ 302 /auth/callback?code&state ----|
  |---------------->| verify state; exchange code (server to server); validate ID token
  |                 |   (iss, aud, exp, nonce); require email_verified = true
  |                 | MFA = (acr == "mfa"); auth_time -> sessions.mfa_at
  |                 | system_session("login"): upsert users by idp_subject
  |                 |   first login -> provision_user() in one transaction
  |                 | create sessions row; Set-Cookie
  |<-- 302 next ----|   (next must be a same-origin path; anything else goes to /)
  | GET /api/v1/session  -> 200
```

### Session

| Item | Decision |
|---|---|
| Token | 32 random bytes, base64url. Only `sha256(token)` is stored |
| Cookie | `__Host-tj_session`; `HttpOnly; Secure; SameSite=Lax; Path=/`; no `Domain` (the `__Host-` prefix enforces this). **Same origin as the SPA** (ADR-011), so it is never sent to the marketing site or any other host |
| Lifetime | 14 days idle (sliding), 90 days absolute |
| Rotation | New token on login and after password or MFA change; all sessions revoked on password change |
| Revocation | `sessions.revoked_at`; "log out everywhere" revokes all of a user's rows; every API request re-checks the session (cached 60 s per instance) |
| **CSRF** | Four layers on every unsafe method (ADR-011): `SameSite=Lax`; `Origin` or `Sec-Fetch-Site: same-origin` validated, and requests with neither are rejected; a per-session token (an HMAC of the session id with a server secret, so nothing is stored) returned by `GET /api/v1/session` and sent as `X-CSRF-Token`; JSON or multipart bodies only. Logout is `POST`; webhooks are exempt but signature-verified. No CORS middleware is installed |
| Credentials in the browser | **None readable by JavaScript.** The session is an `HttpOnly` cookie, the SPA holds no tokens, and nothing sensitive goes in `localStorage`. Auth state lives in our `sessions` table, so it survives deploys and can be revoked centrally |
| Admin sessions | Admin routes additionally require `sessions.mfa_at` within 15 minutes (otherwise 403 `mfa_required` and the SPA starts a step-up login) and use a 15-minute idle timeout (ADR-012) |

Authorization is deliberately simple at launch: a user owns everything under their `user_id`. `is_admin` is set only by the owner-level CLI and unlocks the admin API (ADR-012): MFA required, content-blind through database grants, every action audited. Impersonation is out of scope.

To confirm in spike S1: the IdP's ID token carries an MFA indicator and supports step-up; the `__Host-` cookie flow works end to end behind Cloudflare, including the `next` redirect; the CSRF header flow works with multipart uploads; and a strict CSP survives the chosen libraries (doc 04). None of this is verified yet.

### Client state replaces the filter singleton

| State | Where it lives now | Replaces |
|---|---|---|
| Dashboard and trade-log filters | **URL search params**, typed by the router, so they survive a reload and can be shared. Last-used filters are kept in `localStorage` as a per-browser convenience only | The process-wide `filters` object in `ui/state.py`, shared by every visitor |
| Server data | The query cache in the tab (TanStack Query), invalidated after mutations | Per-connection UI state held by the server |
| Preferences (timezone, trading-day start, theme) | `user_settings` through `/api/v1/settings`, returned by `/api/v1/session` | The global `settings` table |
| Date presets (`30d`, `mtd`, `ytd`) | Sent as `preset`; the **server** resolves them in the user's timezone | `date.today()` on the server clock |
| Per-tab server state | **None.** The API keeps no per-tab or per-connection state | `app.storage.tab` and `storage_secret` |

## 5. Plans, entitlements and billing

**Decide the business model first** (README question 1). The design supports "free beta, then paid" without rework.

- **Entitlements are code, not UI.** A small module maps plan to limits: `max_active_accounts`, `max_storage_mb`, `history_months`, `prop_rules`, `reports`. Enforced in the service/domain functions (for example `create_account` raises `EntitlementError`), so the UI cannot be bypassed. **Export and delete are never gated.**
- **Provider:** use the provider's hosted checkout and customer portal. We store only IDs and status.
  - *Merchant of record* (Paddle, Lemon Squeezy): handles VAT and sales tax worldwide; higher per-transaction fee; less control. Good default for a solo seller.
  - *Stripe (+ Stripe Tax)*: lower fees; you remain the seller of record and carry tax registration duties.
  - **Risk to clear early:** payment providers restrict financial-services categories, and the policies read on 2026-10-02 are broader than hoped (doc 08). Stripe prohibits funded prop trading and restricts investment services; Paddle prohibits trading platforms and trading signals or strategies and keeps discretion; Lemon Squeezy lists neither and tells sellers to ask support first. A trade journal for prop-firm traders is analytics, not advice, but approval is not guaranteed and the wording on the marketing site matters. **Ask Lemon Squeezy and Stripe support to pre-clear the product in M1**, before building billing around it.
- **Webhooks:** verify the signature; insert into `billing_events` with the provider event ID as primary key (idempotent); update `subscriptions`; return 2xx fast. A nightly reconcile job re-reads each active subscription so a missed webhook cannot strand someone on the wrong plan.
- **Lifecycle:** trial 14 days without a card; payment failure gives a 7-day grace period, then **read-only** (can view, export, delete; cannot import or add). Data is never deleted because of non-payment.

### Plan resolution, operator overrides and codes (ADR-012)

**The effective plan is computed, never stored.** `entitlements.effective_plan(user)` evaluates, in order:

1. an active **operator override** (`plan_overrides`: not revoked, not expired) wins outright, even if it lowers the plan;
2. otherwise the **higher** of an active local grant (`grant_redemptions` where now is between `starts_at` and `ends_at`) and the billing mirror (`subscriptions` in an active, trialing or grace state);
3. otherwise `free`.

Nothing writes the result back to `users`, so **billing webhooks and the nightly reconcile job cannot overwrite an override**: they only touch `subscriptions`. `GET /api/v1/session` returns the effective plan and its *source* (`override`, `grant`, `subscription`, `free`) so the UI can say "complimentary until 12 Jan"; an override's internal reason is never shown to the user.

| Item | Rule |
|---|---|
| Override | Plan, optional expiry and a **required reason**, created and revoked only through the admin API; at most one active per user (partial unique index); every change audited |
| Grant codes | N days of a plan; optional `max_redemptions`; **one redemption per user** (`UNIQUE (grant_code_id, user_id)`); stored hashed. `POST /api/v1/grants/redeem` is rate-limited and returns the same error for unknown, expired, revoked and used-up codes. A user with an active grant cannot redeem another until it ends (a simple rule I chose; change it if you want stacking) |
| Discount codes | Created from the admin back end through the payment provider's API (`infra/billing.py` interface: `create_discount`, `deactivate_discount`), mirrored in `discount_codes`, and redeemed only inside the provider's checkout. That API (Stripe coupons and promotion codes, Paddle discounts, or similar) is **unverified** and decides the exact fields |
| Beta invites | While the beta is gated, sign-up needs a valid invite code, carried through the OIDC `state` and redeemed inside `provision_user()`. An invite can bundle a grant code (for example 90 days of Pro for testers) |
| Expiry | When a grant or override ends, the user falls back to the next source. Anything over the new limits (for example more accounts than the plan allows) stays and becomes read-only; **data is never deleted** |

## 6. File storage (screenshots)

**Upload**

1. `POST /api/v1/trades/{id}/screenshots` (multipart) with limits at every layer: a body-size cap at the edge, a `Content-Length` check, and a streaming read that aborts at 10 MB (at most 10 files per request). Today neither upload sets any limit, and the whole file is in memory before the 25 MB check.
2. Server checks quota, then decodes with Pillow (cap pixel count to defeat decompression bombs), strips metadata, re-encodes to WebP or PNG, and makes a 480 px thumbnail. Decoding is stronger than the current magic-byte check and rejects polyglot files.
3. Write both objects to R2 at `u/<user_uuid>/<uuid>.webp` and `..._t.webp`, then insert the `screenshots` row. If the insert fails, delete the objects. This mirrors the compensation pattern already in `journal.add_screenshot`.

**Serve:** `GET /files/{screenshot_id}` authenticates the session, loads the row **under RLS** (so another user's id returns 404), then redirects (302) to a presigned R2 URL valid 60 seconds. The bucket is private; there are no public URLs. Responses carry `X-Content-Type-Options: nosniff` and an explicit content type. A same-origin `<img src="/files/{id}">` sends the session cookie and is a safe method, so no CSRF token is needed. **CSP caveat:** the browser applies `img-src` to the redirect target too, so the CSP must allow the R2 host, which weakens `img-src 'self'`; the alternative is streaming the bytes through the API (costing API bandwidth and the direct R2 path). Decide in spike S3.

**Delete:** remove the row, and after commit remove the objects (the existing "delete the file after the transaction commits" rule in `journal.delete_screenshot`). Failures are retried by the orphan job.

**Orphan job:** replaces `remove_orphan_files` (which today deletes anything unknown at every startup). New rules: lists one user's prefix at a time; considers only objects older than 24 h; **reports first**, deletes only after the same object has been unreferenced across two runs at least 7 days apart; never runs at startup; refuses to run if the DB's latest `screenshots.created_at` is older than the newest object (a sign of a restore from backup).

**Quota:** per-plan `max_storage_mb`, summed from `size_bytes`.

## 7. Account lifecycle

| Stage | What happens |
|---|---|
| Sign up | IdP creates the identity; email verified; first callback runs `provision_user()` in one transaction: `users`, `user_settings`, default tag categories (Setup, Mistake, Emotion; currently seeded by migration v1), a free `subscriptions` row, and the invite redemption while the beta is gated |
| **Timezone step (important)** | Today `settings.DEFAULTS` hardcodes `America/Los_Angeles`, and the importer interprets every CSV timestamp in that timezone. For a user elsewhere, trades silently land hours off and on the wrong trading day. Onboarding must preselect the browser's timezone and make the user **confirm the CSV timezone**; imports already record `source_timezone`, so a later correction can re-run affected imports. **Done (2026-10-04, migration 0006):** the CSV zone is its own setting, `csv_timezone`, which imports read; null follows the person's own timezone, so existing users change nothing. Re-running past imports after a correction is still to build |
| Use | Every request through `tenant_session`. "Today" is never taken from a server clock: the API resolves date presets in the user's timezone and the SPA formats dates in it. The bug lives in `ui/state.py:55` and the date inputs in `settings_page.py:340,393` and `widgets.py:131`, all deleted with `ui/`; the rule carries over |
| Export | Request creates a `data_exports` row; a job builds a ZIP (`trades.csv`, `fill_pairs.csv`, `accounts.csv`, `ledger.csv`, `tags.csv`, notes as HTML and plain text, `screenshots/`, `manifest.json` with schema version, counts and checksums); stored under the user's prefix; email a link to an authenticated download route; link expires in 24 h; limit one per hour. **Amended 2026-10-04:** built first as a synchronous download, `GET /api/v1/export` (doc 05 section 11, slice 5): the job, the copy in object storage and the emailed link wait for the file store (M6) and an email provider; the ZIP is what that job will produce |
| Suspend | An admin sets `status = suspended` and revokes the user's sessions; sign-in is refused with a clear message; data is untouched; an admin can unsuspend. Export and deletion rights are still honored on request through support |
| Delete | Re-authenticate; set `status = pending_deletion` and `deletion_scheduled_at = now + 7 days`; revoke sessions; cancel the subscription. A sweep job then deletes the R2 prefix, runs `DELETE FROM users` (FK cascades remove all tenant rows), deletes the user at the IdP, and anonymizes audit rows. Keep a minimal tombstone (hashed email, timestamps) for billing reconciliation and abuse control. State in the Privacy Policy that backups age out within their retention window | **Built as (2026-10-04):** re-authentication is a provider sign-in within ten minutes plus the typed address; signing in again is the only way to cancel (no cancel route); the sweep is `python -m tradejournal.pg.sweep deletions --apply` as the sweep role; the provider identity goes into an outbox table in the same transaction as the row delete and is removed at Kinde afterwards, retried until it is. Not yet: the R2 prefix (M6), the subscription cancel (M9), the tombstone (with billing).

## 8. Migrating your existing local journal

You are the only existing user and the data is small (23 trades), so this is a one-off script, not a feature.

1. Create your `users` row (via the normal signup path in staging).
2. `tools/migrate_local.py --sqlite data/journal.db --user <email>` reads SQLite **read-only**, inserts in dependency order inside one `tenant_session`, remapping IDs, and uploads screenshots to R2. Notes are normalized on the way in: sanitized with the allow-list and converted from the Quasar editor's HTML to the Tiptap-compatible subset (`div` to `p`, `b` to `strong`, `i` to `em`, `strike` to `s`).
3. **Golden check:** compute dashboard statistics and per-account totals from the old and new databases with `finance.py` and assert they are identical; assert row counts per table.
4. Keep the local folder untouched until you have verified the hosted copy and exported a backup from it.

## 9. Port checklist by module

| Module | Call sites | Change |
|---|---|---|
| `db.py` | 15 | Rewrite: pool, `tenant_session`, `system_session`, migration runner with advisory lock and `schema_migrations`; migrations become `.sql` files; the existing "never edit a shipped migration" rule stays |
| `accounts.py` | 11 | Placeholders, `RETURNING`, `lower()` uniqueness, booleans, `Decimal` directly |
| `ledger.py` | 4 | Same |
| `journal.py` | 30 | `lower()` ordering, `ON CONFLICT`, `HAVING` alias fix, screenshot functions to object storage |
| `store.py` | 17 | `RETURNING id`, `executemany` to `psycopg` batching |
| `importer.py` | 15 | Dry-run sentinel rollback; per-user duplicate detection; size and row caps |
| `queries.py` | 3 | `= ANY(%s)`; typed columns |
| `settings.py` | 2 | Per-user table; timezone default from the browser |
| `parser.py` | n/a | Add size and row caps; optional: replace pandas with `csv` |
| `cli.py` | 2 | Becomes an operator tool: `--user`, plus `admin grant`, `revoke` and `list`, run as a one-off job with the owner-level database URL |
| `ui/` | about 45 `open_db()` references | **Deleted after the SPA reaches parity.** Replaced by `api/` (about 60 routes, each behind `current_user` and `tenant_session`) and `frontend/` (doc 07) |
| Tests | 16 files | New Postgres fixture (per-test transaction rollback), plus the schema lint and two-tenant suites. **Port the harness first**: green tests on Postgres are the proof the port is correct. Then route tests with FastAPI's test client, a two-tenant test generated from the OpenAPI document, and a test that connects as the admin role and proves it cannot read tenant tables |

### 9.1 SQL audit of the current code (2026-10-02)

Every SQL statement in `backend/tradejournal/` was read against the table above. The checklist is right on the whole; this adds what it missed. **"Verified"** means checked in the code or by running Python on this machine. **"Expected"** is my reading of Postgres rules, **not run**. *(Update 2026-10-03: spike S2 ran the two "Expected" query items below on PostgreSQL 18.6 and both were confirmed; the conclusion and five new findings, including the time zone and money-scale ones, are under ADR-002 in doc 06. The advisory-lock rule at the end of this section is still a proposal.)*

**Changes that give a wrong answer without an error (the dangerous kind)**

| Where | What happens | Status |
|---|---|---|
| `journal.py:139` `dict(cursor.fetchall())` | With `dict_row`, each row is a two-key dict, and `dict()` of a list of those silently returns `{'color': 'count'}`. Every `in_use.get(color, 0)` is then 0, so every new tag gets the first preset color. Fix: build the dict from named columns | Verified (ran it) |
| Case-insensitive `=` lookups: `accounts.py:181`, `journal.py:81, 90, 121` | `accounts.name`, `tag_groups.name` and `tags.name` are declared `COLLATE NOCASE`, so `WHERE name = ?` ignores case today. On Postgres it will not: a duplicate check for "setup" against "Setup" stops matching and the user gets a database error instead of the friendly message. The table above lists only the unique indexes and `ORDER BY` | Verified in the schema (`db.py`); Postgres behavior is standard |
| Implicit `ORDER BY name`: `accounts.py:188`, `journal.py:75` | These sort case-insensitively only because of the column collation (no `COLLATE` in the query), so a search for `NOCASE` misses them. Use `ORDER BY lower(name)` | Verified |

**Changes that fail loudly (the test suite will find them)**

| Where | What happens | Status |
|---|---|---|
| `journal.py:108-113` | `GROUP BY tags.id` but `ORDER BY tag_groups.sort_order`. Postgres allows ungrouped columns only from the table whose primary key is grouped, and `sort_order` belongs to `tag_groups`. Expect "must appear in the GROUP BY clause". Fix: group by `tags.id, tag_groups.sort_order` | **Verified on PostgreSQL 18.6 (S2, 2026-10-03):** `GroupingError`, as predicted |
| `journal.py:366-369` | The alias-in-`HAVING` trap already listed above; the line is now 366 to 369, not 360 | **Verified on PostgreSQL 18.6:** `UndefinedColumn: column "has_notes" does not exist` |
| Positional row access: `accounts.py:158`, `cli.py:94`, `journal.py:83`, `queries.py:103`, `store.py:233, 238, 240` | `sqlite3.Row` allows `row[0]` and `row["id"]`; `dict_row` allows only the name. The table above says "`sqlite3.Row` to `dict_row`, all call sites", which hides that these 7 sites also change. (`db.py:258` and `ui/pages/settings_page.py:129` go away with the rewrite and the UI deletion) | Verified |
| 8 `cursor.lastrowid` sites | `accounts.py`, `importer.py`, `ledger.py` once each, `journal.py` three times, `store.py` twice. Covered by `RETURNING id` above; counted here so none is forgotten | Verified |

**Not a problem** (checked and absent): `LIKE`, `strftime` or `date()` in SQL, `AUTOINCREMENT`, `IFNULL`, JSON functions. `GLOB` appears only in the migration that adds the tag color check. The one `LIMIT` (`importer.py:183`) is valid Postgres.

**A concurrency question the checklist does not ask.** `store.rebuild_trades` reads every trade a new fill pair might touch, regroups them in Python, then writes. SQLite plus one user made that safe in practice (one writer at a time, one person). On Postgres, two overlapping writes for the same user (two tabs, a double-submitted import, a client retry after a timeout) can both read the old trades and both write, which can leave duplicate or orphaned trades. This is reasoning from `store.py:116-192`, not a reproduced failure. Proposed rule, to be confirmed in S2: every write path that regroups (import, undo import, `rebuild_account`, `recalculate_trades`, and anything that deletes fill pairs) first takes `pg_advisory_xact_lock` keyed on the user, inside `tenant_session`; the duplicate check in `importer.py:114` is replaced by `INSERT ... ON CONFLICT DO NOTHING RETURNING` on the per-user unique key. Add a test that runs the same import twice at once and expects exactly one set of rows.

**Statement counts that matter for speed** (from the baseline benchmark, doc 05 section 7): `importer.py:114` looks up each pair with its own `SELECT`, `importer.py:121` inserts one pair at a time and `importer.py:131` links one pair at a time (about 3 statements a row); `store.py:129` fetches each seed pair with its own `SELECT`; `store.py:105` writes trades one at a time. Batch all of these in the port: one `SELECT ... WHERE (buy_fill_id, sell_fill_id) = ANY(...)` per file, `executemany` or `COPY` for inserts, `WHERE id = ANY(%s)` for the seed pairs.

**Both implemented and tested in M2 (2026-10-03, `pg/store.py` and `pg/importer.py`).**

- *The lock.* `store.lock_user` takes `pg_advisory_xact_lock(7283, hashtext(user_id))` and every regrouping path calls it first: `import_files`, `undo_import`, `rebuild_account`, `recalculate_trades` and `rebuild_trades`. One test per path holds the user's lock from another connection and checks that the path waits while another user's import does not. A test running three overlapping imports at once (a morning file, the full file and a slice of it) checks the end state is the serial result. Mutations that remove each lock call, or make every user share one key, each fail a test. One thing differs from the proposal above: the duplicate check stays a single lookup per file (under the lock) rather than `ON CONFLICT DO NOTHING RETURNING`, because the importer must also tell "already in this account" from "in another account" for its report.
- *The batching.* An import now runs 17 statements however many rows the file has (measured 17 for 300 rows; the SQLite importer ran about 3 a row, 900 for the same file). A test fails if the count grows with the rows or passes 25. Over network latency this removes the risk named in doc 05 section 7 (point 3); the timing itself is re-measured when the benchmark is re-run.
- *New findings from the port.* `SELECT DISTINCT x ... ORDER BY x COLLATE "C"` is an error in Postgres ("ORDER BY expressions must appear in select list"); the port wraps the `DISTINCT` in a subquery. `psycopg` `executemany(..., returning=True)` returns one result set per row, read with `nextset()`. Fill ids are stored as text `buy_ref` and `sell_ref` and converted back to integers when a `FillPair` is rebuilt, which is correct only for the one import adapter that exists (Tradovate); a second adapter with non-numeric references must change `pair_from_row`.