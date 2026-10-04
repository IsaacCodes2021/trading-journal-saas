-- 0010: plans, grant codes and operator overrides (doc 03 section 5, ADR-012).
--
-- The effective plan is computed from these tables every time and never stored (`entitlements.effective_plan`), so nothing
-- that writes billing state can overwrite what an operator decided. This migration has the three tables that decide it
-- apart from the subscription mirror, which arrives with billing (M9):
--
--   grant_codes        codes the operator hands out: "90 days of Pro". Stored hashed, like invites.
--   grant_redemptions  one row per person who used one. A person can use a given code once.
--   plan_overrides     an operator's decision about one person's plan, with a required reason; at most one is active.
--
-- None of these is under tenant row-level security: they belong to the account, not to the journal, so the request role has
-- no grant on them at all and the sign-in role reads and writes only what it needs (a test pins both).

CREATE TABLE grant_codes (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code_hash        bytea NOT NULL UNIQUE CHECK (octet_length(code_hash) = 32),   -- sha256 of the normalized code
    label            text NOT NULL DEFAULT '',                                      -- who or what it is for, in the operator's words
    plan             text NOT NULL CHECK (plan IN ('pro')),                         -- a grant gives a paid plan
    days             integer NOT NULL CHECK (days BETWEEN 1 AND 3650),
    max_redemptions  integer CHECK (max_redemptions IS NULL OR max_redemptions >= 1),
    expires_at       timestamptz,                                                   -- after this the code can no longer be used
    created_by       text NOT NULL DEFAULT 'cli:owner',
    created_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz
);

CREATE TABLE grant_redemptions (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    grant_code_id  bigint NOT NULL REFERENCES grant_codes (id),
    user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    plan           text NOT NULL CHECK (plan IN ('pro')),
    starts_at      timestamptz NOT NULL,
    ends_at        timestamptz NOT NULL,
    CHECK (ends_at > starts_at),
    UNIQUE (grant_code_id, user_id)
);
CREATE INDEX grant_redemptions_user ON grant_redemptions (user_id, ends_at);

CREATE TABLE plan_overrides (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    plan           text NOT NULL CHECK (plan IN ('free', 'pro')),
    expires_at     timestamptz,
    -- Not blank: trimmed of the usual white space and of the Unicode spaces, spelled out. A regular-expression class such as
    -- [[:space:]] depends on the server's locale (a no-break space passes it under one and not another), and this rule must not
    -- depend on where the database happens to run.
    reason         text NOT NULL CHECK (length(btrim(reason, E' \t\r\n\f\v' ||
                       U&'\00A0\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200A\200B\202F\205F\3000')) > 0),  -- never shown to the person
    created_by     text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    revoked_at     timestamptz,
    revoked_by     text,
    revoke_reason  text
);
-- At most one active override per person; history is kept.
CREATE UNIQUE INDEX plan_overrides_one_active ON plan_overrides (user_id) WHERE revoked_at IS NULL;

-- An invite can carry a grant ("90 days of Pro for testers"); it is given inside the sign-up transaction.
ALTER TABLE invites ADD COLUMN grant_code_id bigint REFERENCES grant_codes (id);

-- The sign-in role (it also serves the account's own plan and the redeem route) reads codes by hash, reads what decides a
-- person's plan, and records a redemption. It cannot create, change or revoke a code or an override: that is the owner's CLI
-- now and the admin role's (M10).
GRANT SELECT (id, code_hash, plan, days, max_redemptions, expires_at, revoked_at) ON grant_codes TO app_login;
GRANT SELECT, INSERT ON grant_redemptions TO app_login;
GRANT USAGE ON SEQUENCE grant_redemptions_id_seq TO app_login;
GRANT SELECT (user_id, plan, expires_at, revoked_at) ON plan_overrides TO app_login;
GRANT SELECT (grant_code_id) ON invites TO app_login;
