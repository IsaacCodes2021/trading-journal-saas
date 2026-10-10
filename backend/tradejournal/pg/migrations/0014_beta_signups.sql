-- 0014: the beta: a personal sign-up code for each person who joins, 30 days of full access, and read-only afterwards.
--
-- Four things are added here.
--
-- 1. `grant_redemptions.revoked_at` and `ended_notice_sent_at`. Full access given by a code or an invite is a row in
--    `grant_redemptions` (migration 0010) with a start and an end. An operator can end it early (`revoked_at`), and the
--    "your access has ended" email is sent once per grant (`ended_notice_sent_at`), whether it ran out or was revoked.
--
-- 2. A cap: a code that gives full access gives at most 30 days. It is added NOT VALID, so it applies to every code made from
--    now on and does not look at ones that already exist.
--
-- 3. `beta_signups`: who asked to join (an address, a first name, a favourite prop firm), and the personal code that was made
--    for them. The code itself is an ordinary invite (migration 0007): single use, tied to the address, expiring, and carrying
--    the 30-day grant, so redeeming it at sign-up is the one path to a beta account and nothing new decides who may sign in.
--
-- 4. Two narrow functions the sign-in role may call, because the request that takes the form has no session and the sign-in
--    role must not be able to make invites on its own: `beta_join` records the sign-up and makes the personal code in one
--    step, under rules that live here and not in the application (one code an hour per address, five in all, none for an
--    address that already has an account or already used its code), and `beta_email_result` records whether the email left.
--    Neither returns anything about who else has signed up.

-- 1 ---------------------------------------------------------------------------------------------------------------------

ALTER TABLE grant_redemptions ADD COLUMN revoked_at timestamptz;
ALTER TABLE grant_redemptions ADD COLUMN ended_notice_sent_at timestamptz;

-- 2 ---------------------------------------------------------------------------------------------------------------------

ALTER TABLE grant_codes ADD CONSTRAINT grant_codes_thirty_days_at_most CHECK (days <= 30) NOT VALID;

-- 3 ---------------------------------------------------------------------------------------------------------------------

CREATE TABLE beta_signups (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email           text NOT NULL CHECK (length(email) BETWEEN 3 AND 254 AND email = lower(email)),
    first_name      text NOT NULL CHECK (length(first_name) BETWEEN 1 AND 80),
    prop_firm       text NOT NULL CHECK (length(prop_firm) BETWEEN 1 AND 80),
    invite_id       bigint REFERENCES invites (id),           -- the latest personal code; earlier ones are revoked
    codes_made      integer NOT NULL DEFAULT 0,               -- how many codes have been made for this address
    last_code_at    timestamptz,
    email_sent_at   timestamptz,                              -- when the email with the latest code left; null if it has not
    email_failed_at timestamptz,                              -- when sending the latest code's email failed, if it did
    email_failures  integer NOT NULL DEFAULT 0,               -- failures in all
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX beta_signups_email ON beta_signups (email);
CREATE INDEX beta_signups_created ON beta_signups (created_at);

-- No role reads or writes this table directly yet: the sign-in role goes through the functions below, and the operator's
-- role is given its read (and the revoke) with the operator's list, in the migration that adds it.

-- 4 ---------------------------------------------------------------------------------------------------------------------

-- Returns the sign-up's id when a NEW code was made (the caller then emails it), and NULL when nothing was made: a
-- malformed address, an address that already has an account, a code already used, a code made less than an hour ago, five
-- already made, or no beta code to give. The page says the same thing to everyone, so the answer teaches a stranger nothing.
CREATE FUNCTION beta_join(p_email text, p_first_name text, p_prop_firm text, p_code_hash bytea) RETURNS bigint
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
    VALUES (p_code_hash, 'Beta sign-up #' || v_signup.id, v_email, 1, v_now + interval '14 days', v_grant)
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

-- Whether the email with the latest code left. A failure does not hold the person up: the next request may try again.
CREATE FUNCTION beta_email_result(p_signup bigint, p_ok boolean) RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    UPDATE beta_signups
       SET email_sent_at = CASE WHEN p_ok THEN now() ELSE email_sent_at END,
           email_failed_at = CASE WHEN p_ok THEN NULL ELSE now() END,
           email_failures = email_failures + CASE WHEN p_ok THEN 0 ELSE 1 END
     WHERE id = p_signup
$$;

REVOKE ALL ON FUNCTION beta_join(text, text, text, bytea) FROM PUBLIC;
REVOKE ALL ON FUNCTION beta_email_result(bigint, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION beta_join(text, text, text, bytea) TO app_login;
GRANT EXECUTE ON FUNCTION beta_email_result(bigint, boolean) TO app_login;
