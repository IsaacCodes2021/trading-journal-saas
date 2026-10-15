-- 0021: billing (the billing design, docs/design/billing-plan.md Phase 2, and the Paddle guide's additions).
--
-- Everything a subscription needs, with nothing that can charge anyone: that is the payment provider's, behind
-- `TJ_BILLING_ENABLED`, which is off in production. Additive only; nothing is backfilled (an UPDATE in a migration cannot see
-- tenant rows, and none of these tables is a tenant table anyway).
--
--   1. The trial is a grant. One `grant_codes` row of kind 'trial' (7 days, made by 'system:trial'); its hash is of a random
--      value nobody keeps, so the code can never be typed. Each person's trial is a `grant_redemptions` row, and the existing
--      "one row per person per code" key makes it one trial per account.
--   2. `subscriptions`: one row per person, made by the provider's first event about their subscription. Statuses are the
--      provider's, mapped: trialing, active, past_due, paused, canceled. Access is worked out from them by
--      `entitlements.subscription_live_until`, and `billing_live_until` below says the same in SQL (a test compares the two).
--   3. `billing_customers` (the provider's customer for a person, so they keep one across subscriptions) and
--      `billing_checkouts` (each checkout the server opened, with whose it is: the webhook links a new subscription to a
--      person through this, never through anything the browser said).
--   4. `billing_events`: every event the provider sent, keyed by its own id (a repeat does nothing), with personal details
--      removed before it is written. Kept 7 years for tax; the person's id is emptied when the account is deleted.
--   5. `billing_cancellations`: subscriptions still to end at the provider after an account was deleted. Not tied to the
--      account, so the entry outlives it, like `identity_removals`.
--   6. The daily job's emails (trial ends in 2 days, trial ended, subscription ended), through narrow functions, because the
--      sweep role reads no address; and the "access ended" list learns to skip people with a live subscription.
--
-- None of these is under tenant row-level security: they belong to the account, like the grant tables. The request role has
-- no grant on any of them, and the operator's role reads billing state but never an event's payload (tests pin every grant).

-- 1 ---------------------------------------------------------------------------------------------------------------------

ALTER TABLE grant_codes DROP CONSTRAINT grant_codes_kind;
ALTER TABLE grant_codes ADD CONSTRAINT grant_codes_kind CHECK (kind IN ('free_days', 'trial'));
CREATE UNIQUE INDEX grant_codes_one_trial ON grant_codes (kind) WHERE kind = 'trial';
INSERT INTO grant_codes (code_hash, label, plan, days, kind, created_by)
VALUES (sha256(convert_to(gen_random_uuid()::text || gen_random_uuid()::text, 'UTF8')), '7-day trial', 'pro', 7, 'trial',
        'system:trial');

-- The sign-in role finds the trial by its kind and leaves trials out of "one code at a time" (a trial never blocks a promo).
GRANT SELECT (kind) ON grant_codes TO app_login;

-- When the "your trial ends in 2 days" email left, once per trial.
ALTER TABLE grant_redemptions ADD COLUMN reminder_sent_at timestamptz;

-- 2 ---------------------------------------------------------------------------------------------------------------------

CREATE TABLE subscriptions (
    user_id                   uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    provider                  text NOT NULL CHECK (provider IN ('fake', 'paddle')),
    provider_customer_id      text,
    provider_subscription_id  text NOT NULL UNIQUE,
    plan                      text NOT NULL DEFAULT 'pro' CHECK (plan IN ('pro')),
    status                    text NOT NULL CHECK (status IN ('trialing', 'active', 'past_due', 'paused', 'canceled')),
    current_period_end        timestamptz,
    cancel_at_period_end      boolean NOT NULL DEFAULT false,
    -- Who asked for the cancel: the person, or their account deletion (then signing in again within the 7 days resumes it).
    cancel_requested_by       text CHECK (cancel_requested_by IN ('person', 'deletion')),
    past_due_since            timestamptz,   -- the first failed payment of this run of failures: grace is 7 days from it
    ended_at                  timestamptz,   -- when the provider ended it
    ended_notice_sent_at      timestamptz,   -- the "your subscription has ended" email left
    provider_updated_at       timestamptz,   -- when the newest event applied here happened: older ones are not applied
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_status ON subscriptions (status);

-- When a stored subscription stops giving its plan: NULL when it never will again, 'infinity' when live with no end known.
-- The same rule as `entitlements.subscription_live_until` (renewing: a day past the period; cancelling: the period's end;
-- a failed payment: 7 days from the first failure); `tests/pg/test_pg_billing.py` holds the two together.
CREATE FUNCTION billing_live_until(
    p_status text, p_period_end timestamptz, p_cancelling boolean, p_past_due_since timestamptz
) RETURNS timestamptz
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN p_status = 'past_due' THEN coalesce(p_past_due_since, p_period_end) + interval '7 days'
        WHEN p_status NOT IN ('active', 'trialing') THEN NULL
        WHEN p_period_end IS NULL THEN 'infinity'::timestamptz
        WHEN p_cancelling THEN p_period_end
        ELSE p_period_end + interval '1 day'
    END
$$;

-- 3 ---------------------------------------------------------------------------------------------------------------------

CREATE TABLE billing_customers (
    user_id               uuid PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    provider              text NOT NULL CHECK (provider IN ('fake', 'paddle')),
    provider_customer_id  text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, provider_customer_id)
);

CREATE TABLE billing_checkouts (
    provider_checkout_id  text PRIMARY KEY,      -- the provider's id for it (Paddle: the transaction, txn_...)
    provider              text NOT NULL CHECK (provider IN ('fake', 'paddle')),
    user_id               uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at            timestamptz NOT NULL DEFAULT now(),
    completed_at          timestamptz             -- a subscription came of it
);
CREATE INDEX billing_checkouts_user ON billing_checkouts (user_id, created_at);

-- 4 ---------------------------------------------------------------------------------------------------------------------

CREATE TABLE billing_events (
    id                        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider                  text NOT NULL CHECK (provider IN ('fake', 'paddle')),
    event_key                 text NOT NULL CHECK (length(event_key) BETWEEN 1 AND 200),  -- the provider's event id
    type                      text NOT NULL CHECK (length(type) BETWEEN 1 AND 100),
    occurred_at               timestamptz,
    user_id                   uuid REFERENCES users (id) ON DELETE SET NULL,
    provider_subscription_id  text,
    received_at               timestamptz NOT NULL DEFAULT now(),
    outcome                   text NOT NULL DEFAULT 'received' CHECK (outcome IN (
                                  'received', 'applied', 'stale', 'unknown_type', 'no_account', 'refund_recorded',
                                  'reconciled', 'alert')),
    payload                   jsonb NOT NULL DEFAULT '{}'::jsonb,  -- ids, statuses, dates, amounts: no name, email or card
    UNIQUE (provider, event_key)
);
CREATE INDEX billing_events_user ON billing_events (user_id, received_at);
CREATE INDEX billing_events_subscription ON billing_events (provider_subscription_id, received_at);

-- 5 ---------------------------------------------------------------------------------------------------------------------

CREATE TABLE billing_cancellations (
    provider                  text NOT NULL CHECK (provider IN ('fake', 'paddle')),
    provider_subscription_id  text NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    attempts                  integer NOT NULL DEFAULT 0,
    last_error                text,
    PRIMARY KEY (provider, provider_subscription_id)
);

-- 6 ---------------------------------------------------------------------------------------------------------------------

-- "Your access has ended" now also skips anyone a live subscription keeps on Pro, and says which kind of grant ended, so a
-- trial gets its own words. Otherwise the rules of migration 0016 stand.
DROP FUNCTION ended_notices_due(timestamptz, timestamptz, integer);
CREATE FUNCTION ended_notices_due(p_now timestamptz, p_since timestamptz, p_limit integer)
RETURNS TABLE (redemption_id bigint, user_id uuid, email text, first_name text, kind text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH ended AS (
        SELECT g.id, g.user_id, g.grant_code_id, least(g.ends_at, coalesce(g.revoked_at, g.ends_at)) AS ended_at
        FROM grant_redemptions g
        WHERE g.ended_notice_sent_at IS NULL
    ), latest AS (
        SELECT DISTINCT ON (e.user_id) e.id, e.user_id, e.grant_code_id, e.ended_at
        FROM ended e
        WHERE e.ended_at >= p_since AND e.ended_at <= p_now
        ORDER BY e.user_id, e.ended_at DESC, e.id DESC
    )
    SELECT l.id, l.user_id, u.email, coalesce(b.first_name, 'there'), c.kind
    FROM latest l
    JOIN users u ON u.id = l.user_id AND u.status = 'active'
    JOIN grant_codes c ON c.id = l.grant_code_id
    LEFT JOIN beta_signups b ON b.email = lower(u.email)
    WHERE NOT EXISTS (
              SELECT 1 FROM grant_redemptions o
              WHERE o.user_id = l.user_id AND o.starts_at <= p_now AND o.ends_at > p_now AND o.revoked_at IS NULL)
      AND NOT EXISTS (
              SELECT 1 FROM plan_overrides po
              WHERE po.user_id = l.user_id AND po.revoked_at IS NULL AND (po.expires_at IS NULL OR po.expires_at > p_now))
      AND NOT EXISTS (
              SELECT 1 FROM subscriptions s
              WHERE s.user_id = l.user_id
                AND billing_live_until(s.status, s.current_period_end, s.cancel_at_period_end, s.past_due_since) > p_now)
    ORDER BY l.ended_at, l.id
    LIMIT greatest(1, least(p_limit, 500))
$$;

-- Trials ending within `p_within` (two days, from the job) whose reminder has not left, for active accounts that nothing
-- else will keep on Pro when the trial ends (a later grant, an override, or a subscription already started).
CREATE FUNCTION trial_reminders_due(p_now timestamptz, p_within interval, p_limit integer)
RETURNS TABLE (redemption_id bigint, user_id uuid, email text, first_name text, ends_at timestamptz)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT g.id, g.user_id, u.email, coalesce(b.first_name, 'there'), g.ends_at
    FROM grant_redemptions g
    JOIN grant_codes c ON c.id = g.grant_code_id AND c.kind = 'trial'
    JOIN users u ON u.id = g.user_id AND u.status = 'active'
    LEFT JOIN beta_signups b ON b.email = lower(u.email)
    WHERE g.reminder_sent_at IS NULL AND g.revoked_at IS NULL
      AND g.ends_at > p_now AND g.ends_at <= p_now + p_within
      AND NOT EXISTS (
              SELECT 1 FROM grant_redemptions o
              WHERE o.user_id = g.user_id AND o.id <> g.id AND o.revoked_at IS NULL AND o.ends_at > g.ends_at)
      AND NOT EXISTS (
              SELECT 1 FROM plan_overrides po
              WHERE po.user_id = g.user_id AND po.revoked_at IS NULL AND (po.expires_at IS NULL OR po.expires_at > p_now))
      AND NOT EXISTS (
              SELECT 1 FROM subscriptions s
              WHERE s.user_id = g.user_id AND s.status IN ('trialing', 'active', 'past_due'))
    ORDER BY g.ends_at, g.id
    LIMIT greatest(1, least(p_limit, 500))
$$;

CREATE FUNCTION mark_trial_reminder_sent(p_redemption bigint, p_at timestamptz) RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH done AS (
        UPDATE grant_redemptions SET reminder_sent_at = p_at
        WHERE id = p_redemption AND reminder_sent_at IS NULL
        RETURNING id
    )
    SELECT EXISTS (SELECT 1 FROM done)
$$;

-- Subscriptions that stopped giving Pro within the window and whose ending email has not left, for active accounts that
-- nothing else keeps on Pro now. When it stopped is the same moment `entitlements.subscription_lapsed_at` gives.
CREATE FUNCTION subscription_ended_notices_due(p_now timestamptz, p_since timestamptz, p_limit integer)
RETURNS TABLE (user_id uuid, email text, first_name text, ended_at timestamptz)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH lapsed AS (
        -- A renewing one is never "ended" here: past its period it is a late webhook, which the nightly check settles.
        SELECT s.user_id,
               CASE WHEN s.status IN ('active', 'trialing') AND NOT s.cancel_at_period_end THEN NULL
                    WHEN s.status IN ('active', 'trialing', 'past_due')
                    THEN billing_live_until(s.status, s.current_period_end, s.cancel_at_period_end, s.past_due_since)
                    ELSE coalesce(s.ended_at, s.current_period_end, s.past_due_since)
               END AS ended_at
        FROM subscriptions s
        WHERE s.ended_notice_sent_at IS NULL
    )
    SELECT l.user_id, u.email, coalesce(b.first_name, 'there'), l.ended_at
    FROM lapsed l
    JOIN users u ON u.id = l.user_id AND u.status = 'active'
    LEFT JOIN beta_signups b ON b.email = lower(u.email)
    WHERE l.ended_at IS NOT NULL AND l.ended_at <= p_now AND l.ended_at >= p_since
      AND NOT EXISTS (
              SELECT 1 FROM grant_redemptions o
              WHERE o.user_id = l.user_id AND o.starts_at <= p_now AND o.ends_at > p_now AND o.revoked_at IS NULL)
      AND NOT EXISTS (
              SELECT 1 FROM plan_overrides po
              WHERE po.user_id = l.user_id AND po.revoked_at IS NULL AND (po.expires_at IS NULL OR po.expires_at > p_now))
    ORDER BY l.ended_at, l.user_id
    LIMIT greatest(1, least(p_limit, 500))
$$;

CREATE FUNCTION mark_subscription_ended_notice_sent(p_user uuid, p_at timestamptz) RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    WITH done AS (
        UPDATE subscriptions SET ended_notice_sent_at = p_at
        WHERE user_id = p_user AND ended_notice_sent_at IS NULL
        RETURNING user_id
    )
    SELECT EXISTS (SELECT 1 FROM done)
$$;

REVOKE ALL ON FUNCTION ended_notices_due(timestamptz, timestamptz, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION trial_reminders_due(timestamptz, interval, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION mark_trial_reminder_sent(bigint, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION subscription_ended_notices_due(timestamptz, timestamptz, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION mark_subscription_ended_notice_sent(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ended_notices_due(timestamptz, timestamptz, integer) TO app_sweep;
GRANT EXECUTE ON FUNCTION trial_reminders_due(timestamptz, interval, integer) TO app_sweep;
GRANT EXECUTE ON FUNCTION mark_trial_reminder_sent(bigint, timestamptz) TO app_sweep;
GRANT EXECUTE ON FUNCTION subscription_ended_notices_due(timestamptz, timestamptz, integer) TO app_sweep;
GRANT EXECUTE ON FUNCTION mark_subscription_ended_notice_sent(uuid, timestamptz) TO app_sweep;

-- Grants ------------------------------------------------------------------------------------------------------------------

-- The sign-in role serves the person's own billing: it reads their plan's subscription, starts a checkout (the customer and
-- the checkout it opened), marks a cancel or a resume it asked the provider for, and gives the trial at sign-up (a grant
-- redemption, already granted). It cannot write a status or a date: those come from the provider, through the webhook.
GRANT SELECT ON subscriptions TO app_login;
GRANT UPDATE (cancel_at_period_end, cancel_requested_by, updated_at) ON subscriptions TO app_login;
GRANT SELECT, INSERT ON billing_customers TO app_login;
GRANT SELECT, INSERT ON billing_checkouts TO app_login;

-- The webhook role mirrors what the provider says and nothing else: billing tables only, and the three columns of `users`
-- it already reads (migration 0004). It can queue the cancel of a subscription that belongs to no account any more.
GRANT SELECT, INSERT, UPDATE ON subscriptions TO app_webhook;
GRANT SELECT, INSERT, UPDATE ON billing_events TO app_webhook;
GRANT USAGE ON SEQUENCE billing_events_id_seq TO app_webhook;
GRANT SELECT, INSERT ON billing_customers TO app_webhook;
GRANT SELECT ON billing_checkouts TO app_webhook;
GRANT UPDATE (completed_at) ON billing_checkouts TO app_webhook;
GRANT INSERT ON billing_cancellations TO app_webhook;
GRANT INSERT ON audit_log TO app_webhook;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO app_webhook;

-- The sweep queues and sends the final cancel of a deleted account's subscription, and runs the nightly check, which
-- corrects a subscription from the provider's own answer (the dates and the status) and records that it did.
GRANT SELECT ON subscriptions TO app_sweep;
GRANT UPDATE (status, current_period_end, cancel_at_period_end, past_due_since, ended_at, provider_updated_at, updated_at)
    ON subscriptions TO app_sweep;
GRANT INSERT ON billing_events TO app_sweep;
GRANT USAGE ON SEQUENCE billing_events_id_seq TO app_sweep;
GRANT SELECT, INSERT, UPDATE, DELETE ON billing_cancellations TO app_sweep;

-- The operator sees billing state, view only, and never what an event carried.
GRANT SELECT ON subscriptions TO app_admin;
GRANT SELECT ON billing_customers TO app_admin;
GRANT SELECT (id, provider, event_key, type, occurred_at, user_id, provider_subscription_id, received_at, outcome)
    ON billing_events TO app_admin;
GRANT SELECT ON billing_cancellations TO app_admin;
