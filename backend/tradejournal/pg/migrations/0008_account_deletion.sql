-- 0008: deleting an account (requirement F-A6, doc 03 section 8 "Delete", doc 04 launch gate).
--
-- The flow: a person who has just proved who they are asks to delete; the account becomes `pending_deletion` with a date
-- seven days away, every session ends, and signing in again before that date cancels it. A sweep job then deletes what is
-- due: the identity at the provider first, then the row, whose foreign keys remove every tenant row.
--
-- Three things are added here.
--
-- 1. `sessions.authenticated_at`: when the person last actually authenticated at the identity provider (its `auth_time`),
--    which can be days before the session began if the provider signed them in silently. "Recently" is judged on this.
-- 2. `users.deletion_scheduled_at`, tied to the status by a constraint, so a pending account always has a date and no other
--    account has one.
-- 3. Two narrow functions the sign-in role may call, because it may not write `users.status` directly (a suspended person
--    must never be able to un-suspend themselves through a bug in sign-in): one moves ACTIVE to PENDING, the other moves
--    PENDING back to ACTIVE, and nothing else.
--
-- The sweep role gets what it needs to find and remove due accounts and to write what it did to the audit log.

ALTER TABLE sessions ADD COLUMN authenticated_at timestamptz;
UPDATE sessions SET authenticated_at = created_at;
ALTER TABLE sessions ALTER COLUMN authenticated_at SET NOT NULL;
ALTER TABLE sessions ALTER COLUMN authenticated_at SET DEFAULT now();

ALTER TABLE users ADD COLUMN deletion_scheduled_at timestamptz;
UPDATE users SET deletion_scheduled_at = now() WHERE status = 'pending_deletion' AND deletion_scheduled_at IS NULL;
ALTER TABLE users ADD CONSTRAINT users_deletion_matches_status
    CHECK ((status = 'pending_deletion') = (deletion_scheduled_at IS NOT NULL));

-- Only an active account can ask to be deleted (a suspended one cannot: that is for support, who can still honour the
-- request), and only a pending one can be restored.
CREATE FUNCTION request_account_deletion(p_user uuid, p_at timestamptz) RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH moved AS (
        UPDATE users SET status = 'pending_deletion', deletion_scheduled_at = p_at
        WHERE id = p_user AND status = 'active'
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM moved)
$$;

CREATE FUNCTION cancel_account_deletion(p_user uuid) RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH moved AS (
        UPDATE users SET status = 'active', deletion_scheduled_at = NULL
        WHERE id = p_user AND status = 'pending_deletion'
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM moved)
$$;

REVOKE ALL ON FUNCTION request_account_deletion(uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION cancel_account_deletion(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION request_account_deletion(uuid, timestamptz) TO app_login;
GRANT EXECUTE ON FUNCTION cancel_account_deletion(uuid) TO app_login;

-- The sweep: find the accounts that are due (and the identity to remove at the provider), delete them, and say so.
GRANT SELECT (idp_subject, deletion_scheduled_at) ON users TO app_sweep;
GRANT INSERT ON audit_log TO app_sweep;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO app_sweep;

-- The identity at the provider has to go too, but it is removed after the account, in a separate step that can fail and be
-- retried. So that a failure there cannot leave an identity nobody remembers, the sweep writes down which one to remove in
-- the same transaction that deletes the account (the provider's id, nothing else), and removes the entry once the provider
-- confirms. A system table with no user_id: it outlives the account on purpose, and holds nothing that identifies a person
-- to anyone but the provider.
CREATE TABLE identity_removals (
    idp_subject  text PRIMARY KEY,
    created_at   timestamptz NOT NULL DEFAULT now(),
    attempts     integer NOT NULL DEFAULT 0,
    last_error   text                                           -- a short reason code, never a message from the provider
);
GRANT SELECT, INSERT, DELETE ON identity_removals TO app_sweep;
GRANT UPDATE (attempts, last_error) ON identity_removals TO app_sweep;
