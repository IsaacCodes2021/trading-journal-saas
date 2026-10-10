-- 0016: the "your access has ended" email, for the people whose 30 days simply ran out.
--
-- An operator who revokes someone's access sends that email in the same request (migration 0015). Everyone else whose
-- time-limited access ended gets it from a scheduled job (`python -m tradejournal.pg.sweep ended-notices`), run daily on the
-- host. The job runs as the `sweep` role, which can read none of the columns it needs (an address, a first name, grants), so
-- like the sign-in role in 0014 it goes through two narrow functions and learns nothing else.
--
-- A person is due a notice when ALL of these hold:
--   * a grant of theirs ended (it ran out, or an operator revoked it) and no notice for it has been recorded;
--   * it ended within the window the job passes (so switching the job on does not write to people about last spring);
--   * their account is active (not suspended, not being deleted);
--   * nothing else gives them full access right now (another grant that is running, or a plan override that is in force),
--     because "your access has ended" would be untrue, and that is the same rule `entitlements.access_of` applies;
--   * one notice per person: the latest ended grant stands for all of them, and recording it covers the earlier ones.

CREATE FUNCTION ended_notices_due(p_now timestamptz, p_since timestamptz, p_limit integer)
RETURNS TABLE (redemption_id bigint, user_id uuid, email text, first_name text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH ended AS (
        SELECT g.id, g.user_id, least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) AS ended_at
        FROM grant_redemptions g
        WHERE g.ended_notice_sent_at IS NULL
    ), latest AS (
        SELECT DISTINCT ON (e.user_id) e.id, e.user_id, e.ended_at
        FROM ended e
        WHERE e.ended_at >= p_since AND e.ended_at <= p_now
        ORDER BY e.user_id, e.ended_at DESC, e.id DESC
    )
    SELECT l.id, l.user_id, u.email, coalesce(b.first_name, 'there')
    FROM latest l
    JOIN users u ON u.id = l.user_id AND u.status = 'active'
    LEFT JOIN beta_signups b ON b.email = lower(u.email)
    WHERE NOT EXISTS (
              SELECT 1 FROM grant_redemptions o
              WHERE o.user_id = l.user_id AND o.starts_at <= p_now AND o.ends_at > p_now AND o.revoked_at IS NULL)
      AND NOT EXISTS (
              SELECT 1 FROM plan_overrides po
              WHERE po.user_id = l.user_id AND po.revoked_at IS NULL AND (po.expires_at IS NULL OR po.expires_at > p_now))
    ORDER BY l.ended_at, l.id
    LIMIT greatest(1, least(p_limit, 500))
$$;

-- Record that the email left, for this grant and for every earlier ended grant of the same person that had no notice
-- (so a person who used two codes is told once). False when it was already recorded.
CREATE FUNCTION mark_ended_notice_sent(p_redemption bigint, p_at timestamptz) RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH me AS (
        SELECT id, user_id FROM grant_redemptions WHERE id = p_redemption AND ended_notice_sent_at IS NULL
    ), done AS (
        UPDATE grant_redemptions g SET ended_notice_sent_at = p_at
        FROM me
        WHERE g.user_id = me.user_id AND g.ended_notice_sent_at IS NULL
          AND (g.ends_at <= p_at OR g.revoked_at IS NOT NULL)
        RETURNING g.id
    )
    SELECT EXISTS (SELECT 1 FROM done WHERE id = p_redemption)
$$;

REVOKE ALL ON FUNCTION ended_notices_due(timestamptz, timestamptz, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION mark_ended_notice_sent(bigint, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ended_notices_due(timestamptz, timestamptz, integer) TO app_sweep;
GRANT EXECUTE ON FUNCTION mark_ended_notice_sent(bigint, timestamptz) TO app_sweep;
