# 03. Data model, accounts, billing, files

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

`set_config(..., true)` is transaction-scoped, so it is safe with a connection pool (and with PgBouncer in transaction mode). Never use a session-level `SET`; it would leak identity to the next request on that pooled connection.

**Rules that go with it**

- Every index leads with `user_id`, so the policy predicate is an index prefix.
- A separate, tiny `system_session()` (owner-like role with a narrow grant list) exists only for: login lookup by IdP subject, session lookup by token hash, webhook handling, and background sweeps. It is used in a handful of audited places and never from UI code.
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
| `users` | `id uuid PK`, `idp_subject UNIQUE`, `email` (unique on `lower`), `email_verified_at`, `status` (`active`/`suspended`/`pending_deletion`), `is_admin`, `tos_version`, `tos_accepted_at`, `created_at`, `last_login_at`, `deletion_scheduled_at` | The account. Not under tenant RLS (accessed via `system_session`) |
| `sessions` | `id`, `user_id`, `token_hash UNIQUE` (sha256), `created_at`, `last_seen_at`, `expires_at`, `revoked_at`, `user_agent`, `ip_hash` | Server-side sessions; revocable; survive deploys |
| `user_settings` | `user_id PK`, `timezone`, `trading_day_timezone`, `trading_day_start`, `theme` | Replaces global `settings` |
| `subscriptions` | `user_id PK`, `provider`, `provider_customer_id`, `provider_subscription_id`, `plan`, `status`, `trial_ends_at`, `current_period_end`, `cancel_at_period_end`, `updated_at` | Local mirror of billing state |
| `billing_events` | `provider_event_id PK`, `type`, `payload jsonb`, `received_at`, `processed_at` | Webhook idempotency and audit |
| `audit_log` | `id`, `user_id NULL`, `actor`, `action`, `entity`, `entity_id`, `ip_hash`, `meta jsonb`, `created_at` | Append-only security events (login, failed login, email/password change, import, undo, export, delete, admin action). `app_rw` has INSERT only |
| `data_exports` | `id`, `user_id`, `status`, `object_key`, `requested_at`, `expires_at` | Async export jobs |
| `invites` | `code PK`, `created_by`, `email NULL`, `used_by NULL`, `used_at`, `expires_at` | Private-beta gating |

Retention: `sessions` rows deleted 30 days after expiry; `audit_log` kept 12 months; `billing_events` kept 7 years (tax) with PII minimized.

## 3. Type mapping (SQLite to Postgres)

| SQLite today | Postgres | Effect on code |
|---|---|---|
| Money, prices, fee rates as TEXT decimal strings | `NUMERIC(20,6)` for money, `NUMERIC(20,8)` for prices and tick sizes | psycopg returns `Decimal` natively. `finance.py` is unchanged; the `money_str()` / `plain()` string helpers at the storage boundary are replaced by passing `Decimal` |
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

## 4. Authentication and sessions

### Why a hosted IdP

Passwords, MFA, email verification, reset flows, breached-password checks and login throttling are the highest-risk code to write and the cheapest to rent. Hosted providers advertise free tiers of 25,000 monthly active users ([Auth0](https://auth0.com/pricing), [Kinde](https://www.kinde.com/comparisons/top-10-authentication-providers-for-b2c-software-2026/); re-verify at signup), far above launch scale. The cost is moderate lock-in, which we limit by owning our own `users` and `sessions` tables and speaking standard OIDC.

**Shortlist and selection criteria** (decide in spike S1b): Auth0, Kinde, Clerk, WorkOS AuthKit, Supabase Auth. Must have: Google sign-in, email + password, TOTP MFA, email verification, custom domain/branding, standard OIDC with PKCE, a management API to delete users, ability to export users, free tier of at least 5,000 MAU.

**Fallback if none fit:** self-managed with `Authlib` (Google OIDC), `argon2-cffi` (argon2id), TOTP via `pyotp`, our own email tokens. Roughly 2 to 3 weeks more, plus an ongoing security-maintenance burden. Documented only as plan B.

### Login flow (OIDC authorization code with PKCE)

```
Browser           App (/auth/*)                      IdP
  | GET /login      |                                  |
  |---------------->| create state, nonce, PKCE verifier (signed short-lived cookie)
  |<-- 302 ---------|-----------------------------> /authorize
  |  user authenticates at IdP (password / Google / MFA) |
  |<------------------ 302 /auth/callback?code&state ----|
  |---------------->| verify state; exchange code (server to server); validate ID token
  |                 |   (iss, aud, exp, nonce); require email_verified = true
  |                 | system_session: upsert users by idp_subject
  |                 |   first login -> provision_user() in one transaction
  |                 | create sessions row; Set-Cookie
  |<-- 302 / -------|
```

### Session

| Item | Decision |
|---|---|
| Token | 32 random bytes, base64url. Only `sha256(token)` is stored |
| Cookie | `__Host-tj_session`; `HttpOnly; Secure; SameSite=Lax; Path=/`; no `Domain` (the `__Host-` prefix enforces this) |
| Lifetime | 14 days idle (sliding), 90 days absolute |
| Rotation | New token on login and after password or MFA change; all sessions revoked on password change |
| Revocation | `sessions.revoked_at`; "log out everywhere" revokes all of a user's rows; `tenant_session()` re-checks (cached 60 s) |
| CSRF | `SameSite=Lax` plus an `Origin` check on our own state-changing HTTP routes; logout is `POST`; webhooks are exempt but signature-verified |
| Auth state storage | **Our `sessions` table, not `app.storage.user`.** NiceGUI's user storage is file-backed by default ([docs](https://nicegui.io/documentation/storage)), which is lost on ephemeral PaaS disks and cannot be revoked centrally. NiceGUI still needs its own `storage_secret` for `app.storage.tab` (per-tab filter state) |

Authorization is deliberately simple at launch: a user owns everything under their `user_id`; `is_admin` unlocks the admin page (requires MFA, shows aggregates only, no access to trade data). Impersonation is out of scope.

To confirm in spike S1: NiceGUI page handlers can receive the Starlette `request` (so middleware can attach the user), and the Socket.IO endpoint checks `Origin` (cross-site websocket hijacking). I believe both hold but have not verified them.

## 5. Plans, entitlements and billing

**Decide the business model first** (README question 1). The design supports "free beta, then paid" without rework.

- **Entitlements are code, not UI.** A small module maps plan to limits: `max_active_accounts`, `max_storage_mb`, `history_months`, `prop_rules`, `reports`. Enforced in the service/domain functions (for example `create_account` raises `EntitlementError`), so the UI cannot be bypassed. **Export and delete are never gated.**
- **Provider:** use the provider's hosted checkout and customer portal. We store only IDs and status.
  - *Merchant of record* (Paddle, Lemon Squeezy): handles VAT and sales tax worldwide; higher per-transaction fee; less control. Good default for a solo seller.
  - *Stripe (+ Stripe Tax)*: lower fees; you remain the seller of record and carry tax registration duties.
  - **Risk to clear early:** payment providers restrict some financial-services categories. A journaling tool is analytics, not advice or signals, but approval is not guaranteed and I have not verified the current policies. **Apply for the account in M1**, before building billing around it.
- **Webhooks:** verify the signature; insert into `billing_events` with the provider event ID as primary key (idempotent); update `subscriptions`; return 2xx fast. A nightly reconcile job re-reads each active subscription so a missed webhook cannot strand someone on the wrong plan.
- **Lifecycle:** trial 14 days without a card; payment failure gives a 7-day grace period, then **read-only** (can view, export, delete; cannot import or add). Data is never deleted because of non-payment.

## 6. File storage (screenshots)

**Upload**

1. `ui.upload(max_file_size=10 MB, max_files=10, accept=image types)` plus a body-size cap at the proxy. Today neither upload sets a limit.
2. Server checks quota, then decodes with Pillow (cap pixel count to defeat decompression bombs), strips metadata, re-encodes to WebP or PNG, and makes a 480 px thumbnail. Decoding is stronger than the current magic-byte check and rejects polyglot files.
3. Write both objects to R2 at `u/<user_uuid>/<uuid>.webp` and `..._t.webp`, then insert the `screenshots` row. If the insert fails, delete the objects. This mirrors the compensation pattern already in `journal.add_screenshot`.

**Serve:** `GET /files/{screenshot_id}` authenticates the session, loads the row **under RLS** (so another user's id returns 404), then redirects (302) to a presigned R2 URL valid 60 seconds. The bucket is private; there are no public URLs. Responses carry `X-Content-Type-Options: nosniff` and an explicit content type.

**Delete:** remove the row, and after commit remove the objects (the existing "delete the file after the transaction commits" rule in `journal.delete_screenshot`). Failures are retried by the orphan job.

**Orphan job:** replaces `remove_orphan_files` (which today deletes anything unknown at every startup). New rules: lists one user's prefix at a time; considers only objects older than 24 h; **reports first**, deletes only after the same object has been unreferenced across two runs at least 7 days apart; never runs at startup; refuses to run if the DB's latest `screenshots.created_at` is older than the newest object (a sign of a restore from backup).

**Quota:** per-plan `max_storage_mb`, summed from `size_bytes`.

## 7. Account lifecycle

| Stage | What happens |
|---|---|
| Sign up | IdP creates the identity; email verified; first callback runs `provision_user()` in one transaction: `users`, `user_settings`, default tag categories (Setup, Mistake, Emotion; currently seeded by migration v1), a free `subscriptions` row |
| **Timezone step (important)** | Today `settings.DEFAULTS` hardcodes `America/Los_Angeles`, and the importer interprets every CSV timestamp in that timezone. For a user elsewhere, trades silently land hours off and on the wrong trading day. Onboarding must preselect the browser's timezone and make the user **confirm the CSV timezone**; imports already record `source_timezone`, so a later correction can re-run affected imports |
| Use | Every request through `tenant_session`; UI "today" defaults use the user's timezone, not the server's (`ui/state.py:55`, and the date inputs in `settings_page.py:340,393` and `widgets.py:131`) |
| Export | Request creates a `data_exports` row; a job builds a ZIP (`trades.csv`, `fill_pairs.csv`, `accounts.csv`, `ledger.csv`, `tags.csv`, notes as HTML and plain text, `screenshots/`, `manifest.json` with schema version, counts and checksums); stored under the user's prefix; email a link to an authenticated download route; link expires in 24 h; limit one per hour |
| Delete | Re-authenticate; set `status = pending_deletion` and `deletion_scheduled_at = now + 7 days`; revoke sessions; cancel the subscription. A sweep job then deletes the R2 prefix, runs `DELETE FROM users` (FK cascades remove all tenant rows), deletes the user at the IdP, and anonymizes audit rows. Keep a minimal tombstone (hashed email, timestamps) for billing reconciliation and abuse control. State in the Privacy Policy that backups age out within their retention window |

## 8. Migrating your existing local journal

You are the only existing user and the data is small (23 trades), so this is a one-off script, not a feature.

1. Create your `users` row (via the normal signup path in staging).
2. `tools/migrate_local.py --sqlite data/journal.db --user <email>` reads SQLite **read-only**, inserts in dependency order inside one `tenant_session`, remapping IDs, and uploads screenshots to R2.
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
| `cli.py` | 2 | Becomes an admin tool taking `--user` |
| `ui/` | about 45 `open_db()` references | Replace with `tenant_session`; per-tab filter state; auth gate; no module-level mutable state |
| Tests | 16 files | New Postgres fixture (per-test transaction rollback), plus the schema lint and two-tenant suites. **Port the harness first**: green tests on Postgres are the proof the port is correct |
