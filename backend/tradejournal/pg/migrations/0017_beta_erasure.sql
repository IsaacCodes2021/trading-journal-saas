-- 0017: what the beta keeps about a person, and for how long.
--
-- A beta sign-up holds an address, a first name and a favourite prop firm (0014), and the personal code made for it is an
-- invite tied to the address. Neither hangs off `users`, so deleting an account (0008) removed the person's journal and
-- sessions and left both behind. Two things fix that.
--
-- 1. A person's account being deleted now removes the sign-up and every invite tied to that address, in the same statement.
--    It is a trigger, not a step in the sweep, so it holds for every way a user row can go.
--
-- 2. People who asked to join and never made an account are not kept forever. `erase_unused_beta_signups` removes the
--    sign-ups (and their codes) of addresses that have no account, whose last code was made before a date the caller passes.
--    The date is the caller's choice on purpose: how long to keep them is a decision for the owner, and the job refuses to run
--    without it. A sign-up whose address has an account is never touched here; it goes with the account.
--
-- Neither function returns an address, a name or a code; the second returns a count.

CREATE FUNCTION erase_beta_traces() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    -- Sign-ups first: they point at an invite.
    DELETE FROM beta_signups WHERE email = lower(OLD.email);
    DELETE FROM invites WHERE lower(email) = lower(OLD.email);
    RETURN NULL;
END
$$;

REVOKE ALL ON FUNCTION erase_beta_traces() FROM PUBLIC;

CREATE TRIGGER users_erase_beta_traces
    AFTER DELETE ON users
    FOR EACH ROW EXECUTE FUNCTION erase_beta_traces();

-- p_apply false counts what would go and changes nothing. The limit keeps one run small; the next run takes the rest.
CREATE FUNCTION erase_unused_beta_signups(p_before timestamptz, p_limit integer, p_apply boolean) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_limit integer := greatest(1, least(p_limit, 500));
    v_count integer;
BEGIN
    IF p_before IS NULL THEN
        RAISE EXCEPTION 'a date is needed';
    END IF;

    IF NOT p_apply THEN
        SELECT count(*) INTO v_count FROM (
            SELECT 1 FROM beta_signups s
            WHERE coalesce(s.last_code_at, s.created_at) < p_before
              AND NOT EXISTS (SELECT 1 FROM users u WHERE lower(u.email) = s.email)
            LIMIT v_limit
        ) due;
        RETURN v_count;
    END IF;

    WITH stale AS (
        SELECT s.id FROM beta_signups s
        WHERE coalesce(s.last_code_at, s.created_at) < p_before
          AND NOT EXISTS (SELECT 1 FROM users u WHERE lower(u.email) = s.email)
        ORDER BY s.id
        LIMIT v_limit
        FOR UPDATE OF s SKIP LOCKED
    ), gone AS (
        DELETE FROM beta_signups s USING stale WHERE s.id = stale.id RETURNING s.email
    ), codes AS (
        DELETE FROM invites i WHERE lower(i.email) IN (SELECT email FROM gone) RETURNING 1
    )
    SELECT count(*) INTO v_count FROM gone;
    RETURN v_count;
END
$$;

REVOKE ALL ON FUNCTION erase_unused_beta_signups(timestamptz, integer, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_unused_beta_signups(timestamptz, integer, boolean) TO app_sweep;
