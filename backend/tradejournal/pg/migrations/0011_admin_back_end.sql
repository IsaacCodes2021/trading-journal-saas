-- 0011: what the operator's database role may do (doc 03 section 1, doc 07 section 6, ADR-012).
--
-- The operator runs the beta: finds a person, sees their plan and counters, suspends or restores them, gives or takes a plan,
-- hands out invites and plan codes, and reads the audit log. The role the admin API connects as (`app_admin`) does that and
-- nothing more. It has NO grant on any tenant table (a test connects as it and tries), so it can never read a trade, a note
-- or a screenshot; it sees counters (`user_usage`, migration 0004), never rows.
--
-- Every column is named. It cannot read a code's hash (a stolen admin credential must not be able to redeem or replay a code),
-- cannot read a session's token hash, and cannot write `users.is_admin` (the owner's command only; migration 0004).

-- Accounts: what the list and the detail page show, and the deletion date. Status is already writable (0004).
GRANT SELECT (deletion_scheduled_at) ON users TO app_admin;
GRANT EXECUTE ON FUNCTION request_account_deletion(uuid, timestamptz) TO app_admin;
GRANT EXECUTE ON FUNCTION cancel_account_deletion(uuid) TO app_admin;

-- How many places a person is signed in. Ending them goes through the sign-in role's own revoke (it also drops this
-- instance's cache), not through a grant here.
GRANT SELECT (user_id, revoked_at, expires_at) ON sessions TO app_admin;

-- Plans: read what decides one, give or take an override (the reason is required by the table), and read who redeemed what.
GRANT SELECT ON plan_overrides TO app_admin;
GRANT INSERT (user_id, plan, expires_at, reason, created_by) ON plan_overrides TO app_admin;
GRANT UPDATE (revoked_at, revoked_by, revoke_reason) ON plan_overrides TO app_admin;
GRANT USAGE ON SEQUENCE plan_overrides_id_seq TO app_admin;
GRANT SELECT (grant_code_id, user_id, plan, starts_at, ends_at) ON grant_redemptions TO app_admin;

-- Plan codes and invites: list them (never their hashes), make them (the hash is computed by the caller, the code is shown
-- once), and revoke them. Nothing else about them can change.
GRANT SELECT (id, label, plan, days, max_redemptions, expires_at, created_by, created_at, revoked_at) ON grant_codes TO app_admin;
GRANT INSERT (code_hash, label, plan, days, max_redemptions, expires_at, created_by) ON grant_codes TO app_admin;
GRANT UPDATE (revoked_at) ON grant_codes TO app_admin;
GRANT SELECT (id, label, email, max_uses, expires_at, revoked_at, created_at, grant_code_id) ON invites TO app_admin;
GRANT INSERT (code_hash, label, email, max_uses, expires_at, grant_code_id) ON invites TO app_admin;
GRANT UPDATE (revoked_at) ON invites TO app_admin;
GRANT SELECT ON invite_redemptions TO app_admin;

-- The audit log: the operator reads it and writes to it (every action they take is a row about them, and every look at a
-- person's detail page). The log is append-only for everyone (migration 0007's trigger), the owner included.
GRANT SELECT, INSERT ON audit_log TO app_admin;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO app_admin;
