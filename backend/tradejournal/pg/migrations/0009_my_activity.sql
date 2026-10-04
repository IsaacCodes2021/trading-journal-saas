-- 0009: a person's own security activity (the audit viewer; doc 04 launch checklist, "Audit log events written").
--
-- The audit log holds a keyed hash of each address and small facts about each event, for the operator. A person may see
-- what happened to their own account (sign-ins, sign-outs, an export, a deletion request), and only that, and only the
-- parts that mean something to them: not the address hash, not the rest of `meta`.
--
-- The sign-in role writes the log and may not read it (it has INSERT only, so a bug or a stolen credential there cannot read
-- everyone's history). So reading goes through one narrow function that takes the person and returns their own rows,
-- and only the kinds of event listed here. A kind that is not listed (an operator looking at the account, a failed sign-in
-- that belongs to no one) never reaches the person; adding one is a deliberate edit of this list.

CREATE FUNCTION my_activity(p_user uuid, p_limit integer)
RETURNS TABLE (id bigint, at timestamptz, action text, ended integer, mfa boolean)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT a.id,
           a.created_at,
           a.action,
           CASE WHEN (a.meta ->> 'ended') ~ '^[0-9]{1,6}$' THEN (a.meta ->> 'ended')::integer END,
           CASE WHEN jsonb_typeof(a.meta -> 'mfa') = 'boolean' THEN (a.meta ->> 'mfa')::boolean END
    FROM audit_log a
    WHERE a.user_id = p_user
      AND a.action IN ('signup', 'login', 'login_failed', 'logout', 'session_revoked', 'logout_everywhere', 'export',
                       'deletion_requested', 'deletion_cancelled')
    ORDER BY a.created_at DESC, a.id DESC
    LIMIT GREATEST(1, LEAST(p_limit, 100))
$$;

REVOKE ALL ON FUNCTION my_activity(uuid, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION my_activity(uuid, integer) TO app_login;
