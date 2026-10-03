-- 0003: per-user usage counters (doc 03 section 2).
--
-- Quotas read these, and so will the operator back end, which must never read tenant tables. Application code
-- recomputes them with tradejournal.pg.usage.refresh in the same transaction as every change to what they count
-- (a recount, not an increment, so a counter cannot drift). A tenant table like any other: forced RLS, the
-- user_id default, and the same policy as in 0001. storage_bytes stays 0 until files get a size column (M6).

CREATE TABLE user_usage (
    user_id            uuid PRIMARY KEY DEFAULT app_user_id() REFERENCES users (id) ON DELETE CASCADE,
    accounts_count     integer NOT NULL DEFAULT 0 CHECK (accounts_count >= 0),
    trades_count       integer NOT NULL DEFAULT 0 CHECK (trades_count >= 0),
    screenshots_count  integer NOT NULL DEFAULT 0 CHECK (screenshots_count >= 0),
    storage_bytes      bigint NOT NULL DEFAULT 0 CHECK (storage_bytes >= 0),
    last_import_at     timestamptz,
    updated_at         timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE user_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_usage FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON user_usage USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON user_usage TO app_rw;
