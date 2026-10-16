-- 0020: promo codes. The operator types the code ("FREE30") and it is kept readable (docs/design/promo-codes.md, ADR-012).
--
-- Plan codes until now were random (GR-XXXX-XXXX), shown once and stored only as a hash. A promo code is meant to be
-- printed and shared, so hiding it protects nothing: it is stored as typed, uppercase, beside its hash. The hash stays the
-- lookup key for every code, old and new, so redeeming one is unchanged and the old codes keep working.
--
--   code  the code as the operator typed it, uppercase letters and digits, 3 to 20 of them. NULL for a random code (those
--         stay secret). Unique ignoring case, revoked and expired codes included: a code is never reused, so a person
--         holding an old one can never be handed something new by it.
--   kind  what the code gives. Only 'free_days' for now (days of Pro); the billing migrations widen the list.
--
-- Additive only: no existing row changes (old codes get kind 'free_days' from the default and keep a NULL code).

ALTER TABLE grant_codes ADD COLUMN code text CONSTRAINT grant_codes_code_shape CHECK (code ~ '^[A-Z0-9]{3,20}$');
CREATE UNIQUE INDEX grant_codes_code ON grant_codes (upper(code)) WHERE code IS NOT NULL;

ALTER TABLE grant_codes ADD COLUMN kind text NOT NULL DEFAULT 'free_days'
    CONSTRAINT grant_codes_kind CHECK (kind IN ('free_days'));

-- The operator's role reads and writes the two new columns. It still cannot read a hash (migration 0011), so a random code
-- stays unreadable to it; a typed code is readable because it is meant to be. The sign-in role needs neither: it looks a
-- code up by its hash, as before.
GRANT SELECT (code, kind) ON grant_codes TO app_admin;
GRANT INSERT (code, kind) ON grant_codes TO app_admin;
