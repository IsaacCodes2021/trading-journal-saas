-- 0022: discount codes (docs/design/promo-codes.md section 6 and decision 15; docs/design/paddle-integration.md 5.8).
--
-- A discount code is a promo code of another kind: "LAUNCH20" takes 20% off the subscription's first payment, its first
-- months, or every payment. It lives on `grant_codes` beside the free-days codes (ADR-012, amendment of 2026-10-15), is
-- always typed (a provider needs a code it can show), and is pushed to the payment provider, whose checkout applies it.
-- The row here is the source of truth; the provider's copy follows it (`sync_status`).
--
--   1. `grant_codes` gains the discount's terms and its provider copy. `days` becomes empty for a discount (it gives no
--      days of Pro); one CHECK holds each kind to its own columns. Free-days codes and the trial are unchanged.
--   2. `pending_promos`: a discount code a person typed (in Settings or at sign-up), remembered until a checkout uses it.
--   3. `billing_checkouts.grant_code_id`: the discount a checkout was opened with, so a code's uses can be counted from the
--      people who subscribed with it (a discount is not a grant of Pro, so `grant_redemptions` never holds one).
--
-- Additive: no existing row changes. Nothing here is a tenant table; each role gets only what it uses (tests pin it).

-- 1 ---------------------------------------------------------------------------------------------------------------------

ALTER TABLE grant_codes DROP CONSTRAINT grant_codes_kind;
ALTER TABLE grant_codes ADD CONSTRAINT grant_codes_kind CHECK (kind IN ('free_days', 'trial', 'discount'));
ALTER TABLE grant_codes ALTER COLUMN days DROP NOT NULL;

ALTER TABLE grant_codes
    ADD COLUMN percent_off      smallint CONSTRAINT grant_codes_percent_off CHECK (percent_off BETWEEN 1 AND 100),
    ADD COLUMN duration         text CONSTRAINT grant_codes_duration CHECK (duration IN ('once', 'repeating', 'forever')),
    ADD COLUMN duration_months  smallint CONSTRAINT grant_codes_duration_months CHECK (duration_months BETWEEN 1 AND 24),
    ADD COLUMN provider         text CONSTRAINT grant_codes_provider CHECK (provider IN ('fake', 'paddle')),
    ADD COLUMN provider_discount_id text CONSTRAINT grant_codes_provider_discount_id
        CHECK (length(provider_discount_id) BETWEEN 1 AND 100),
    -- pending: not at the provider yet (no provider on this server, or not tried); synced: the provider has it, matching;
    -- failed: the last push or archive did not go through (`sync_error` says why, in a short code).
    ADD COLUMN sync_status      text CONSTRAINT grant_codes_sync_status CHECK (sync_status IN ('pending', 'synced', 'failed')),
    ADD COLUMN sync_error       text CONSTRAINT grant_codes_sync_error CHECK (length(sync_error) <= 200),
    ADD COLUMN synced_at        timestamptz;

-- Each kind uses its own columns and no other. A discount is typed, has a percentage and a duration (with months exactly
-- when it repeats) and a sync state, and no days. Every other kind has days and none of the discount's columns.
ALTER TABLE grant_codes ADD CONSTRAINT grant_codes_columns_by_kind CHECK (
    CASE WHEN kind = 'discount' THEN
        days IS NULL AND code IS NOT NULL AND percent_off IS NOT NULL AND duration IS NOT NULL
        AND (duration = 'repeating') = (duration_months IS NOT NULL) AND sync_status IS NOT NULL
    ELSE
        days IS NOT NULL AND percent_off IS NULL AND duration IS NULL AND duration_months IS NULL
        AND provider IS NULL AND provider_discount_id IS NULL AND sync_status IS NULL AND sync_error IS NULL
        AND synced_at IS NULL
    END
);

-- 2 ---------------------------------------------------------------------------------------------------------------------

CREATE TABLE pending_promos (
    user_id        uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,  -- one remembered discount per person
    grant_code_id  bigint NOT NULL REFERENCES grant_codes (id),
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- 3 ---------------------------------------------------------------------------------------------------------------------

ALTER TABLE billing_checkouts ADD COLUMN grant_code_id bigint REFERENCES grant_codes (id);

-- Grants ------------------------------------------------------------------------------------------------------------------

-- The sign-in role checks a discount code a person types (by its hash, as for every code) and remembers it, reads the terms
-- to show them and the provider's id to put on the checkout. It still cannot read a code, and never writes one.
GRANT SELECT (percent_off, duration, duration_months, provider, provider_discount_id, sync_status)
    ON grant_codes TO app_login;
GRANT SELECT, INSERT, UPDATE, DELETE ON pending_promos TO app_login;

-- The webhook role forgets a remembered discount once a subscription comes of the checkout that used it.
GRANT SELECT, DELETE ON pending_promos TO app_webhook;

-- The operator makes discount codes and records what the provider did with them, and counts how many people subscribed
-- with each (three columns of the checkouts: the code, whose, and whether it completed; nothing about the payment).
GRANT SELECT (percent_off, duration, duration_months, provider, provider_discount_id, sync_status, sync_error, synced_at)
    ON grant_codes TO app_admin;
GRANT INSERT (percent_off, duration, duration_months, provider, sync_status) ON grant_codes TO app_admin;
GRANT UPDATE (provider, provider_discount_id, sync_status, sync_error, synced_at) ON grant_codes TO app_admin;
GRANT SELECT (grant_code_id, completed_at, user_id) ON billing_checkouts TO app_admin;
