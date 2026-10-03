-- 0004: the four system_session roles and their grant lists (doc 03 section 1, doc 04 GATE, ADR-012).
--
-- Request-serving code reaches the tenant tables only as app_rw, bound to one user by row-level security. A few jobs
-- must work without a tenant: sign-in looks a user up by identity-provider subject, billing webhooks and background
-- sweeps act on accounts, and the operator back end lists and suspends users. Each gets its own login role with the
-- smallest grants that job needs, so a bug or a stolen credential in one of them reaches little.
--
--   app_login    find or create a user at sign-in, record the login
--   app_webhook  read users (billing tables arrive with billing)
--   app_sweep    delete accounts scheduled for deletion; the delete cascades through every tenant table without any
--                grant on them, because referential actions are not checked against the caller's table privileges
--   app_admin    list users, suspend and unsuspend them, read every user's usage counters; NO grant on any tenant table
--
-- No role can write users.is_admin: column-level grants leave it to the owner (the CLI that creates operators).
-- Tables for sessions, the audit log and billing are added by the milestones that need them, with their own grants.

DO $$
DECLARE
    system_role text;
BEGIN
    FOREACH system_role IN ARRAY ARRAY['app_login', 'app_webhook', 'app_sweep', 'app_admin'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = system_role) THEN
            RAISE EXCEPTION 'role % must exist before migrating (see tradejournal/pg/provision.py)', system_role;
        END IF;
        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', system_role);
    END LOOP;
END $$;

-- The login profile may read an account, create one (a new row is active and not an operator by default), and update
-- the fields the identity provider owns. It cannot change status or is_admin.
GRANT SELECT (id, idp_subject, email, email_verified_at, status, is_admin, created_at, last_login_at) ON users TO app_login;
GRANT INSERT (idp_subject, email, email_verified_at) ON users TO app_login;
GRANT UPDATE (email, email_verified_at, last_login_at) ON users TO app_login;

GRANT SELECT (id, email, status) ON users TO app_webhook;

GRANT SELECT (id, status) ON users TO app_sweep;
GRANT DELETE ON users TO app_sweep;

GRANT SELECT (id, email, email_verified_at, status, is_admin, created_at, last_login_at) ON users TO app_admin;
GRANT UPDATE (status) ON users TO app_admin;

-- The operator sees counters, never rows. Policies are permissive and combine with OR, so this one adds a read for
-- app_admin on top of the tenant policy that app_rw is held to.
GRANT SELECT ON user_usage TO app_admin;
CREATE POLICY admin_read ON user_usage FOR SELECT TO app_admin USING (true);
