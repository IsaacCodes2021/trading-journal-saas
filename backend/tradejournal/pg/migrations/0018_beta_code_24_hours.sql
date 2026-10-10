-- 0018: a beta sign-up code works for 24 hours, not 14 days.
--
-- The owner decided (2026-10-10) that a person who asks to join and does not make an account within a day is not kept: their
-- sign-up (address, first name, favourite firm) and codes are removed by the sweep once the last code is 24 hours old
-- (`erase_unused_beta_signups`, migration 0017, run from `sweep all`). A code that lived 14 days would outlive the sign-up it
-- belongs to, so the code now lives 24 hours too, and the email says so.
--
-- The function is replaced whole, because Postgres cannot change one line of it: it is migration 0014's `beta_join` with the
-- one interval changed. Its grants stay as they were (CREATE OR REPLACE keeps them). Codes already made keep the expiry they
-- were made with.

CREATE OR REPLACE FUNCTION beta_join(p_email text, p_first_name text, p_prop_firm text, p_code_hash bytea) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_email   text := lower(btrim(p_email));
    v_name    text := btrim(p_first_name);
    v_firm    text := btrim(p_prop_firm);
    v_now     timestamptz := now();
    v_grant   bigint;
    v_signup  beta_signups%ROWTYPE;
    v_invite  bigint;
BEGIN
    IF coalesce(length(v_email), 0) NOT BETWEEN 3 AND 254
       OR coalesce(length(v_name), 0) NOT BETWEEN 1 AND 80
       OR coalesce(length(v_firm), 0) NOT BETWEEN 1 AND 80
       OR v_email !~ E'^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$'
       OR p_code_hash IS NULL OR octet_length(p_code_hash) <> 32 THEN
        RETURN NULL;
    END IF;

    -- One address at a time, so two requests cannot both make a code.
    PERFORM pg_advisory_xact_lock(7283004, hashtext(v_email));

    IF EXISTS (SELECT 1 FROM users WHERE lower(email) = v_email) THEN
        RETURN NULL;
    END IF;

    -- The grant every beta code carries: 30 days of Pro, made once and kept. Revoking it (the operator's list of codes) is
    -- how the beta is paused: no new code is made while it is revoked.
    SELECT id INTO v_grant FROM grant_codes WHERE created_by = 'system:beta' ORDER BY id LIMIT 1;
    IF v_grant IS NULL THEN
        INSERT INTO grant_codes (code_hash, label, plan, days, created_by)
        VALUES (sha256(convert_to('beta-grant:' || gen_random_uuid()::text, 'UTF8')), 'Beta sign-up, 30 days', 'pro', 30, 'system:beta')
        RETURNING id INTO v_grant;
    ELSIF EXISTS (SELECT 1 FROM grant_codes WHERE id = v_grant AND revoked_at IS NOT NULL) THEN
        RETURN NULL;
    END IF;

    SELECT * INTO v_signup FROM beta_signups WHERE email = v_email FOR UPDATE;
    IF FOUND THEN
        IF v_signup.invite_id IS NOT NULL
           AND EXISTS (SELECT 1 FROM invite_redemptions WHERE invite_id = v_signup.invite_id) THEN
            RETURN NULL;                                    -- they already used their code
        END IF;
        IF v_signup.codes_made >= 5
           OR (v_signup.last_code_at > v_now - interval '1 hour' AND v_signup.email_failed_at IS NULL) THEN
            RETURN NULL;   -- too many, or one was made a moment ago and (as far as we know) is on its way
        END IF;
        UPDATE invites SET revoked_at = v_now WHERE id = v_signup.invite_id AND revoked_at IS NULL;
    ELSE
        INSERT INTO beta_signups (email, first_name, prop_firm) VALUES (v_email, v_name, v_firm) RETURNING * INTO v_signup;
    END IF;

    INSERT INTO invites (code_hash, label, email, max_uses, expires_at, grant_code_id)
    VALUES (p_code_hash, 'Beta sign-up #' || v_signup.id, v_email, 1, v_now + interval '24 hours', v_grant)
    RETURNING id INTO v_invite;

    UPDATE beta_signups
       SET invite_id = v_invite, codes_made = codes_made + 1, last_code_at = v_now,
           email_sent_at = NULL, email_failed_at = NULL
     WHERE id = v_signup.id;

    -- The address is not recorded in the log: the sign-up's id is enough to find the person.
    INSERT INTO audit_log (actor, action, entity, entity_id, meta)
    VALUES ('system', 'beta_code_made', 'beta_signup', v_signup.id::text, jsonb_build_object('codes_made', v_signup.codes_made + 1));

    RETURN v_signup.id;
END
$$;
