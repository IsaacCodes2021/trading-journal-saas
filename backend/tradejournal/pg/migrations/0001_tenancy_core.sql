-- 0001: the tenant tables, with row-level security (doc 03 sections 1 and 2).
--
-- Runs as the schema owner (tj_owner). Three layers each stop a cross-tenant leak on their own:
--   1. RLS, forced, on every table that has user_id (the policy at the bottom);
--   2. composite foreign keys (user_id, parent_id), so a row cannot point at another user's parent;
--   3. user_id DEFAULT app_user_id(), so an insert outside a tenant session fails on NOT NULL.
-- Never edit a migration that has shipped: add 0002 instead.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_rw') THEN
        RAISE EXCEPTION 'role app_rw must exist before migrating (see tradejournal/pg/provision.py)';
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO app_rw;

-- Postgres hands back timestamptz values in the connection's time zone, which would silently move the date of
-- anything near midnight (`entry_at.date()`). The domain code works in UTC, so the database defaults to it. The pool
-- also sets it per connection (session.py); this one survives a pooler that ignores startup parameters.
DO $$
BEGIN
    EXECUTE format('ALTER DATABASE %I SET timezone TO ''UTC''', current_database());
END $$;

-- The tenant for this transaction. NULLIF matters: after a transaction-local setting ends, Postgres reports ''
-- (not NULL), and ''::uuid would raise instead of cleanly matching nothing.
CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('app.user_id', true), '')::uuid $$;

-- ---------------------------------------------------------------------------------------------------------------
-- The account. Not under tenant RLS: the request-serving role has no grant on it at all, and reaches it only
-- through a narrow system role (login, webhook, sweep, admin), added with sessions in M5.

CREATE TABLE users (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    idp_subject        text NOT NULL UNIQUE,
    email              text NOT NULL,
    email_verified_at  timestamptz,
    status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'pending_deletion')),
    is_admin           boolean NOT NULL DEFAULT false,
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_login_at      timestamptz
);
CREATE UNIQUE INDEX users_email_lower ON users (lower(email));

-- ---------------------------------------------------------------------------------------------------------------
-- Tenant tables

CREATE TABLE user_settings (
    user_id               uuid PRIMARY KEY DEFAULT app_user_id() REFERENCES users (id) ON DELETE CASCADE,
    timezone              text NOT NULL DEFAULT 'America/Los_Angeles',
    trading_day_timezone  text NOT NULL DEFAULT 'America/New_York',
    trading_day_start     text NOT NULL DEFAULT '18:00' CHECK (trading_day_start ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
    theme                 text NOT NULL DEFAULT 'dark' CHECK (theme IN ('dark', 'light'))
);

CREATE TABLE accounts (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             uuid NOT NULL DEFAULT app_user_id() REFERENCES users (id) ON DELETE CASCADE,
    name                text NOT NULL,
    firm                text NOT NULL DEFAULT 'Other',
    account_type        text NOT NULL DEFAULT 'evaluation' CHECK (account_type IN ('evaluation', 'funded')),
    account_size        numeric(20, 2),
    starting_balance    numeric(20, 2) NOT NULL DEFAULT 0,
    -- Prop firm rules. NULL means the rule is not tracked for this account.
    max_drawdown        numeric(20, 2),
    trailing_type       text CHECK (trailing_type IN ('intraday', 'eod')),
    trail_lock_enabled  boolean NOT NULL DEFAULT false,
    trail_lock_level    numeric(20, 2),
    daily_loss_limit    numeric(20, 2),
    profit_target       numeric(20, 2),
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- An account with an outcome is archived: hidden unless asked, never deleted.
    outcome             text CHECK (outcome IN ('passed', 'failed', 'closed')),
    ended_on            date,
    archive_note        text NOT NULL DEFAULT '',
    UNIQUE (user_id, id)                      -- the target of composite foreign keys
);
CREATE UNIQUE INDEX accounts_user_name ON accounts (user_id, lower(name));

-- $ per contract round trip. symbol_root '*' applies to roots without their own row.
CREATE TABLE fee_schedules (
    user_id              uuid NOT NULL DEFAULT app_user_id(),
    account_id           bigint NOT NULL,
    symbol_root          text NOT NULL,
    fee_per_contract_rt  numeric(20, 6) NOT NULL CHECK (fee_per_contract_rt >= 0),
    PRIMARY KEY (user_id, account_id, symbol_root),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);

CREATE TABLE imports (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          uuid NOT NULL DEFAULT app_user_id(),
    account_id       bigint NOT NULL,
    filename         text NOT NULL,
    file_sha256      text NOT NULL,
    source_timezone  text NOT NULL,
    imported_at      timestamptz NOT NULL DEFAULT now(),
    rows_parsed      integer NOT NULL DEFAULT 0,
    rows_added       integer NOT NULL DEFAULT 0,
    rows_duplicate   integer NOT NULL DEFAULT 0,
    rows_error       integer NOT NULL DEFAULT 0,
    trades_affected  integer NOT NULL DEFAULT 0,
    undone_at        timestamptz,
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);
CREATE INDEX imports_user_account_hash ON imports (user_id, account_id, file_sha256);

-- Prices and quantities derived from them are exact decimals of any scale, so they are plain numeric. Money is
-- always whole cents (finance.to_money rounds it before it is stored), so numeric(20, 2) never truncates and an amount
-- reaches the API as "-475.00" rather than "-475.000000". Only a per-contract fee rate keeps six decimals.
CREATE TABLE trades (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          uuid NOT NULL DEFAULT app_user_id(),
    account_id       bigint NOT NULL,
    symbol           text NOT NULL,
    symbol_root      text NOT NULL,
    contract_month   text,
    direction        text NOT NULL CHECK (direction IN ('long', 'short')),
    entry_at         timestamptz NOT NULL,
    exit_at          timestamptz NOT NULL,
    trade_date       date NOT NULL,
    hold_seconds     integer NOT NULL,
    total_qty        integer NOT NULL,
    max_position     integer NOT NULL,
    fill_pair_count  integer NOT NULL,
    tick_size        numeric NOT NULL,
    avg_entry        numeric NOT NULL,
    avg_exit         numeric NOT NULL,
    points           numeric NOT NULL,
    ticks            numeric NOT NULL,
    gross_pnl        numeric(20, 2) NOT NULL,
    fees             numeric(20, 2) NOT NULL,
    net_pnl          numeric(20, 2) NOT NULL,
    notes            text NOT NULL DEFAULT '',
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);
CREATE INDEX trades_user_account_date ON trades (user_id, account_id, trade_date);
CREATE INDEX trades_user_account_symbol_time ON trades (user_id, account_id, symbol, entry_at, exit_at);

-- Raw rows from the export, linked to the trade they were grouped into. Identity is (source, buy_ref, sell_ref) so
-- other platforms can join later (ADR-009); `source` is 'tradovate_performance' for today's data.
CREATE TABLE fill_pairs (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id            uuid NOT NULL DEFAULT app_user_id(),
    account_id         bigint NOT NULL,
    trade_id           bigint,
    source             text NOT NULL DEFAULT 'tradovate_performance',
    buy_ref            text NOT NULL,
    sell_ref           text NOT NULL,
    symbol             text NOT NULL,
    tick_size          numeric NOT NULL,
    price_format       text NOT NULL,
    price_format_type  text NOT NULL,
    qty                integer NOT NULL CHECK (qty > 0),
    buy_price          numeric NOT NULL,
    sell_price         numeric NOT NULL,
    pnl                numeric(20, 2) NOT NULL,
    bought_at          timestamptz NOT NULL,
    sold_at            timestamptz NOT NULL,
    duration_raw       text NOT NULL,
    duration_seconds   integer NOT NULL,
    direction          text NOT NULL CHECK (direction IN ('long', 'short')),
    UNIQUE (user_id, id),
    UNIQUE (user_id, source, buy_ref, sell_ref),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE,
    -- The column list matters: a plain SET NULL would also null user_id, which is NOT NULL (Postgres 15 and later).
    FOREIGN KEY (user_id, trade_id) REFERENCES trades (user_id, id) ON DELETE SET NULL (trade_id)
);
CREATE INDEX fill_pairs_user_trade ON fill_pairs (user_id, trade_id);

-- Every import that contained a fill pair. Undoing an import deletes a pair only when no other import has it.
CREATE TABLE import_fill_pairs (
    user_id       uuid NOT NULL DEFAULT app_user_id(),
    import_id     bigint NOT NULL,
    fill_pair_id  bigint NOT NULL,
    PRIMARY KEY (user_id, import_id, fill_pair_id),
    FOREIGN KEY (user_id, import_id) REFERENCES imports (user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, fill_pair_id) REFERENCES fill_pairs (user_id, id) ON DELETE CASCADE
);
CREATE INDEX import_fill_pairs_pair ON import_fill_pairs (user_id, fill_pair_id);

CREATE TABLE tag_groups (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL DEFAULT app_user_id(),
    name        text NOT NULL,
    sort_order  integer NOT NULL DEFAULT 0,
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX tag_groups_user_name ON tag_groups (user_id, lower(name));

CREATE TABLE tags (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id   uuid NOT NULL DEFAULT app_user_id(),
    group_id  bigint NOT NULL,
    name      text NOT NULL,
    color     text NOT NULL DEFAULT '#2a78d6' CHECK (color ~ '^#[0-9a-f]{6}$'),
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, group_id) REFERENCES tag_groups (user_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX tags_user_group_name ON tags (user_id, group_id, lower(name));

CREATE TABLE trade_tags (
    user_id   uuid NOT NULL DEFAULT app_user_id(),
    trade_id  bigint NOT NULL,
    tag_id    bigint NOT NULL,
    PRIMARY KEY (user_id, trade_id, tag_id),
    FOREIGN KEY (user_id, trade_id) REFERENCES trades (user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, tag_id) REFERENCES tags (user_id, id) ON DELETE CASCADE
);
CREATE INDEX trade_tags_tag ON trade_tags (user_id, tag_id);

CREATE TABLE day_notes (
    user_id     uuid NOT NULL DEFAULT app_user_id() REFERENCES users (id) ON DELETE CASCADE,
    trade_date  date NOT NULL,
    notes       text NOT NULL DEFAULT '',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, trade_date)
);

-- stored_name is the file name today; M6 replaces it with an object key in R2 (doc 03 section 2).
CREATE TABLE screenshots (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        uuid NOT NULL DEFAULT app_user_id(),
    trade_id       bigint NOT NULL,
    stored_name    text NOT NULL UNIQUE,
    original_name  text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, trade_id) REFERENCES trades (user_id, id) ON DELETE CASCADE
);
CREATE INDEX screenshots_user_trade ON screenshots (user_id, trade_id, created_at);

CREATE TABLE account_ledger (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL DEFAULT app_user_id(),
    account_id  bigint NOT NULL,
    kind        text NOT NULL,
    amount      numeric(20, 2) NOT NULL,
    entry_date  date NOT NULL,
    note        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);
CREATE INDEX account_ledger_user_account ON account_ledger (user_id, account_id, entry_date);

-- ---------------------------------------------------------------------------------------------------------------
-- The policy. FORCE makes it apply to the table owner too, so a migration bug cannot quietly bypass it.
-- A table added later must be listed here (or in its own migration): the schema lint test fails if one is missed.

DO $$
DECLARE
    tenant_table text;
BEGIN
    FOREACH tenant_table IN ARRAY ARRAY[
        'user_settings', 'accounts', 'fee_schedules', 'imports', 'trades', 'fill_pairs', 'import_fill_pairs',
        'tag_groups', 'tags', 'trade_tags', 'day_notes', 'screenshots', 'account_ledger'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tenant_table);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tenant_table);
        EXECUTE format(
            'CREATE POLICY tenant ON %I USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id())',
            tenant_table
        );
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_rw', tenant_table);
    END LOOP;
END $$;
