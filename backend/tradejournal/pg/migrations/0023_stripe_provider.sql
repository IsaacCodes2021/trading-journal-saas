-- 0023: Stripe beside Paddle (docs/design/stripe-plan.md, P4.1).
--
-- Every billing row records the provider it belongs to. Migrations 0021 and 0022 allowed 'fake' and 'paddle'; this adds
-- 'stripe', so the Stripe connector can be switched on (TJ_BILLING_PROVIDER=stripe) without touching Paddle's rows or
-- code. The same six rules, under the same names, each with one more value.
--
-- Additive: every row that was valid stays valid, so the running app works on the new schema before and after its deploy.

ALTER TABLE subscriptions DROP CONSTRAINT subscriptions_provider_check;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_provider_check
    CHECK (provider IN ('fake', 'paddle', 'stripe'));

ALTER TABLE billing_customers DROP CONSTRAINT billing_customers_provider_check;
ALTER TABLE billing_customers ADD CONSTRAINT billing_customers_provider_check
    CHECK (provider IN ('fake', 'paddle', 'stripe'));

ALTER TABLE billing_checkouts DROP CONSTRAINT billing_checkouts_provider_check;
ALTER TABLE billing_checkouts ADD CONSTRAINT billing_checkouts_provider_check
    CHECK (provider IN ('fake', 'paddle', 'stripe'));

ALTER TABLE billing_events DROP CONSTRAINT billing_events_provider_check;
ALTER TABLE billing_events ADD CONSTRAINT billing_events_provider_check
    CHECK (provider IN ('fake', 'paddle', 'stripe'));

ALTER TABLE billing_cancellations DROP CONSTRAINT billing_cancellations_provider_check;
ALTER TABLE billing_cancellations ADD CONSTRAINT billing_cancellations_provider_check
    CHECK (provider IN ('fake', 'paddle', 'stripe'));

ALTER TABLE grant_codes DROP CONSTRAINT grant_codes_provider;
ALTER TABLE grant_codes ADD CONSTRAINT grant_codes_provider
    CHECK (provider IN ('fake', 'paddle', 'stripe'));
