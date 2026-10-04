-- 0007: sessions, invites and the audit log (doc 03 sections 2 and 4, doc 04 authentication gates).
--
-- These are system tables, not tenant tables: they have a user_id, but a person's own requests (app_rw) must never read
-- or write them, so they have no row-level policy and app_rw has no grant on them at all (a test pins that). The
-- `login` role, the one that finds and creates users at sign-in, gets exactly what the sign-in flow needs.

-- ---------------------------------------------------------------------------------------------------------------
-- Sessions: server-side, revocable, survive a deploy. Only the hash of the cookie's token is stored, so a copy of this
-- table cannot be replayed as cookies.

CREATE TABLE sessions (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id     uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,   -- what the CSRF token is derived from: random, never the row id
    user_id       uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    token_hash    bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),   -- sha256 of the token in the cookie
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),               -- slides: idle for 14 days ends a session
    expires_at    timestamptz NOT NULL,                             -- absolute: 90 days after creation
    revoked_at    timestamptz,
    mfa_at        timestamptz,                                      -- when the identity provider last reported MFA
    user_agent    text,
    ip_hash       text
);
CREATE INDEX sessions_user_live ON sessions (user_id) WHERE revoked_at IS NULL;
CREATE INDEX sessions_expires ON sessions (expires_at);             -- the retention sweep

GRANT SELECT, INSERT ON sessions TO app_login;
GRANT UPDATE (last_seen_at, revoked_at, mfa_at) ON sessions TO app_login;
-- Rows go 30 days after they expire, by the sweep job (which can see nothing else about a session).
GRANT SELECT (id, expires_at) ON sessions TO app_sweep;
GRANT DELETE ON sessions TO app_sweep;

-- ---------------------------------------------------------------------------------------------------------------
-- Invites: the private beta is sign-up by invitation. A code is stored hashed, may carry an email it is for, and may be
-- used more than once. Creating and revoking them is the operator's (the owner-level CLI now, the admin back end later).

CREATE TABLE invites (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code_hash   bytea NOT NULL UNIQUE CHECK (octet_length(code_hash) = 32),   -- sha256 of the normalized code
    label       text NOT NULL DEFAULT '',                                    -- who it is for, in the operator's words
    email       text,                                                        -- if set, only this address may redeem it
    max_uses    integer NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
    expires_at  timestamptz,
    revoked_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE invite_redemptions (
    invite_id    bigint NOT NULL REFERENCES invites (id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    redeemed_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (invite_id, user_id)
);
CREATE INDEX invite_redemptions_user ON invite_redemptions (user_id);

GRANT SELECT (id, code_hash, email, max_uses, expires_at, revoked_at) ON invites TO app_login;
GRANT SELECT, INSERT ON invite_redemptions TO app_login;

-- ---------------------------------------------------------------------------------------------------------------
-- The audit log: append-only security events (sign-in, failures, and later every admin action). Request roles may
-- INSERT only; a trigger refuses UPDATE and DELETE for everyone except the owner, who deletes rows past their
-- retention. user_id is the person it is about, actor who did it (a user, an admin, the CLI or the system).

CREATE TABLE audit_log (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        uuid REFERENCES users (id) ON DELETE SET NULL,        -- the history outlives a deleted account
    actor_user_id  uuid REFERENCES users (id) ON DELETE SET NULL,
    actor          text NOT NULL,                                        -- 'user', 'admin', 'cli:<name>' or 'system'
    action         text NOT NULL,                                        -- 'login', 'login_failed', 'logout', ...
    entity         text,
    entity_id      text,
    ip_hash        text,
    meta           jsonb NOT NULL DEFAULT '{}'::jsonb,                   -- never trade content, notes or file names
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_user ON audit_log (user_id, created_at);
CREATE INDEX audit_log_created ON audit_log (created_at);

CREATE FUNCTION audit_log_is_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF current_user = 'tj_owner' AND TG_OP IN ('DELETE', 'TRUNCATE') THEN
        -- Retention: the owner removes rows past their time (and a test database is emptied between tests).
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;
        RETURN NULL;
    END IF;
    IF TG_OP = 'UPDATE'
       AND (NEW.id, NEW.actor, NEW.action, NEW.entity, NEW.entity_id, NEW.ip_hash, NEW.meta, NEW.created_at)
           IS NOT DISTINCT FROM
           (OLD.id, OLD.actor, OLD.action, OLD.entity, OLD.entity_id, OLD.ip_hash, OLD.meta, OLD.created_at)
       AND (NEW.user_id IS NULL OR NEW.user_id = OLD.user_id)
       AND (NEW.actor_user_id IS NULL OR NEW.actor_user_id = OLD.actor_user_id)
    THEN
        -- The one change allowed: detaching the history from a person who was deleted (the foreign keys' SET NULL).
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'audit_log is append-only';
END
$$;

CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only();

GRANT INSERT ON audit_log TO app_login;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO app_login;
