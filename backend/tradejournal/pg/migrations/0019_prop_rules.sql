-- 0019: prop-firm rule templates, a verified rule set per account, and payouts with their gross, split and fee.
--
-- `rule_templates` holds the firms' published rules, one row per firm + program + size + phase + rule version (the seed is
-- the research CSV, loaded by `python -m tradejournal.pg.rule_templates import`). It belongs to nobody: every person reads
-- it to pick their account's rules, only the operator's role and the owner change it. Its columns are
-- `tradejournal.prop_rules.FIELDS`, in the same order (a test compares the two). A cell of the CSV that is not a clean
-- value for its column (`max_loss_usd` = "up to 1500") leaves the column empty and keeps its text in `source_notes`, so
-- nothing is ever guessed.
--
-- `account_rules` is a person's own copy of a rule set for one account, checked and submitted by them. Copying (rather
-- than pointing at the template) is the point: an operator editing a template later never changes anyone's account. A
-- new submission supersedes the previous row instead of overwriting it, so there is one active row per account and a
-- record of what was verified when.
--
-- Accounts gain the day they were bought (which rule version applies), their phase (a funded account can be live), and
-- when the "evaluation passed" prompt was dismissed. Payouts stay ledger rows (business totals already read them), with
-- the gross requested, the split and a fee; `amount` stays what the person received before any fee: gross x split.

-- ---------------------------------------------------------------------------------------------------------------
-- Templates

CREATE TABLE rule_templates (
    id                                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Rule set
    firm                              text NOT NULL CHECK (length(firm) BETWEEN 1 AND 120),
    program                           text NOT NULL CHECK (length(program) BETWEEN 1 AND 120),
    account_size                      text NOT NULL CHECK (length(account_size) BETWEEN 1 AND 60),
    phase                             text NOT NULL CHECK (phase IN ('evaluation', 'funded', 'live')),
    rule_version                      text NOT NULL CHECK (length(rule_version) BETWEEN 1 AND 200),
    effective_from                    date,
    platform_notes                    text,
    -- Cost
    price_usd                         numeric(20, 2),
    billing                           text CHECK (billing IN ('one_time', 'monthly')),
    activation_fee_usd                numeric(20, 2),
    reset_fee_usd                     numeric(20, 2),
    -- Pass / target
    profit_target_usd                 numeric(20, 2),
    -- Drawdown
    max_loss_usd                      numeric(20, 2),
    drawdown_type                     text CHECK (drawdown_type IN (
                                          'eod_trailing', 'eod_trailing_realtime_enforced', 'intraday_trailing', 'static')),
    drawdown_lock_level               text,
    drawdown_after_payout             text,
    -- Daily loss
    daily_loss_limit_usd              numeric(20, 2),
    daily_loss_type                   text CHECK (daily_loss_type IN ('none', 'soft', 'optional', 'hard')),
    daily_loss_basis                  text CHECK (daily_loss_basis IN ('realized', 'realized_plus_unrealized')),
    -- Contracts and scaling
    max_contracts_minis               integer,
    max_contracts_micros              integer,
    scaling_plan                      text,
    -- Qualifying days
    min_trading_days                  integer,
    -- Consistency
    consistency_pct                   numeric(6, 2),
    consistency_basis                 text CHECK (consistency_basis IN (
                                          'none', 'none stated', 'best_day_vs_total_profit', 'best_day_vs_cycle_profit',
                                          'best_day_vs_profit_target', 'best_day_vs_profit_goal',
                                          'best_day_vs_positive_days_sum', 'best_day_vs_gross_profit_incl_payouts',
                                          'all_time_best_day_vs_cycle_profit')),
    -- Qualifying days (continued)
    qualifying_day_min_profit_usd     numeric(20, 2),
    qualifying_days_required          integer,
    -- Payout
    payout_buffer_usd                 numeric(20, 2),
    min_balance_to_request_usd        numeric(20, 2),
    min_payout_usd                    numeric(20, 2),
    max_payout_usd                    numeric(20, 2),
    payout_cap_schedule               text,
    payout_pct_of_profit_cap          text,
    payout_frequency                  text,
    payout_days_allowed               text,
    max_payouts_before_live_or_close  text,
    profit_split                      text,
    -- Restrictions
    news_trading                      text,
    overnight_holding                 text,
    flat_by_time                      text,
    other_restrictions                text,
    -- Inactivity
    inactivity_rule                   text,
    -- Restrictions (continued)
    max_accounts                      text,
    -- Source
    data_confidence                   text CHECK (data_confidence IN ('official', 'conflicting', 'third_party', 'unknown')),
    source_urls                       text,
    notes                             text,
    -- What the research could not confirm, cell by cell, and the original text of cells that were not clean values.
    flagged_fields                    text[] NOT NULL DEFAULT '{}',
    flag_reasons                      text[] NOT NULL DEFAULT '{}',
    source_notes                      jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_notes) = 'object'),
    created_at                        timestamptz NOT NULL DEFAULT now(),
    updated_at                        timestamptz NOT NULL DEFAULT now(),
    updated_by                        text NOT NULL DEFAULT 'seed',
    CHECK (cardinality(flagged_fields) = cardinality(flag_reasons)),
    CHECK (consistency_pct IS NULL OR (consistency_pct > 0 AND consistency_pct <= 100)),
    CHECK (min_trading_days IS NULL OR min_trading_days >= 0),
    CHECK (qualifying_days_required IS NULL OR qualifying_days_required >= 0),
    CHECK (max_contracts_minis IS NULL OR max_contracts_minis >= 0),
    CHECK (max_contracts_micros IS NULL OR max_contracts_micros >= 0),
    UNIQUE (firm, program, account_size, phase, rule_version)
);

-- Everyone reads the templates (the picker); nobody but the operator and the owner changes them. The operator's role still
-- has no grant on any person's table: these rows are the firms' rules, not anyone's journal.
GRANT SELECT ON rule_templates TO app_rw;
GRANT SELECT, INSERT, UPDATE ON rule_templates TO app_admin;

-- ---------------------------------------------------------------------------------------------------------------
-- A person's verified rule set for one account

CREATE TABLE account_rules (
    id                                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id                           uuid NOT NULL DEFAULT app_user_id(),
    account_id                        bigint NOT NULL,
    -- The template it was copied from; null for rules typed in by hand, and when a template is ever removed.
    source_template_id                bigint REFERENCES rule_templates (id) ON DELETE SET NULL,
    firm                              text NOT NULL CHECK (length(firm) BETWEEN 1 AND 120),
    program                           text NOT NULL CHECK (length(program) BETWEEN 1 AND 120),
    account_size                      text NOT NULL CHECK (length(account_size) BETWEEN 1 AND 60),
    phase                             text NOT NULL CHECK (phase IN ('evaluation', 'funded', 'live')),
    rule_version                      text NOT NULL CHECK (length(rule_version) BETWEEN 1 AND 200),
    effective_from                    date,
    platform_notes                    text,
    price_usd                         numeric(20, 2),
    billing                           text CHECK (billing IN ('one_time', 'monthly')),
    activation_fee_usd                numeric(20, 2),
    reset_fee_usd                     numeric(20, 2),
    profit_target_usd                 numeric(20, 2),
    max_loss_usd                      numeric(20, 2),
    drawdown_type                     text CHECK (drawdown_type IN (
                                          'eod_trailing', 'eod_trailing_realtime_enforced', 'intraday_trailing', 'static')),
    drawdown_lock_level               text,
    drawdown_after_payout             text,
    daily_loss_limit_usd              numeric(20, 2),
    daily_loss_type                   text CHECK (daily_loss_type IN ('none', 'soft', 'optional', 'hard')),
    daily_loss_basis                  text CHECK (daily_loss_basis IN ('realized', 'realized_plus_unrealized')),
    max_contracts_minis               integer,
    max_contracts_micros              integer,
    scaling_plan                      text,
    min_trading_days                  integer,
    consistency_pct                   numeric(6, 2),
    consistency_basis                 text CHECK (consistency_basis IN (
                                          'none', 'none stated', 'best_day_vs_total_profit', 'best_day_vs_cycle_profit',
                                          'best_day_vs_profit_target', 'best_day_vs_profit_goal',
                                          'best_day_vs_positive_days_sum', 'best_day_vs_gross_profit_incl_payouts',
                                          'all_time_best_day_vs_cycle_profit')),
    qualifying_day_min_profit_usd     numeric(20, 2),
    qualifying_days_required          integer,
    payout_buffer_usd                 numeric(20, 2),
    min_balance_to_request_usd        numeric(20, 2),
    min_payout_usd                    numeric(20, 2),
    max_payout_usd                    numeric(20, 2),
    payout_cap_schedule               text,
    payout_pct_of_profit_cap          text,
    payout_frequency                  text,
    payout_days_allowed               text,
    max_payouts_before_live_or_close  text,
    profit_split                      text,
    news_trading                      text,
    overnight_holding                 text,
    flat_by_time                      text,
    other_restrictions                text,
    inactivity_rule                   text,
    max_accounts                      text,
    data_confidence                   text CHECK (data_confidence IN ('official', 'conflicting', 'third_party', 'unknown')),
    source_urls                       text,
    notes                             text,
    flagged_fields                    text[] NOT NULL DEFAULT '{}',
    flag_reasons                      text[] NOT NULL DEFAULT '{}',
    source_notes                      jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(source_notes) = 'object'),
    verified_at                       timestamptz NOT NULL DEFAULT now(),
    verified_by                       uuid NOT NULL DEFAULT app_user_id(),
    superseded_at                     timestamptz,
    CHECK (cardinality(flagged_fields) = cardinality(flag_reasons)),
    CHECK (consistency_pct IS NULL OR (consistency_pct > 0 AND consistency_pct <= 100)),
    CHECK (min_trading_days IS NULL OR min_trading_days >= 0),
    CHECK (qualifying_days_required IS NULL OR qualifying_days_required >= 0),
    CHECK (max_contracts_minis IS NULL OR max_contracts_minis >= 0),
    CHECK (max_contracts_micros IS NULL OR max_contracts_micros >= 0),
    UNIQUE (user_id, id),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);
-- One active rule set per account; the superseded ones are its history.
CREATE UNIQUE INDEX account_rules_active ON account_rules (user_id, account_id) WHERE superseded_at IS NULL;
CREATE INDEX account_rules_user_account ON account_rules (user_id, account_id, verified_at);

ALTER TABLE account_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_rules FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON account_rules USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON account_rules TO app_rw;

-- ---------------------------------------------------------------------------------------------------------------
-- Accounts: when it was bought, its phase, and the evaluation-passed prompt

-- Nothing is backfilled: the owner running this cannot see anyone's rows (forced row-level security, see 0012), so an
-- UPDATE here would change nothing. An empty phase means "the same as the account type" (evaluation or funded); only a live
-- account needs one stored. An empty purchase date is an account from before this migration; the person adds it in settings.
ALTER TABLE accounts
    ADD COLUMN purchase_date date,
    ADD COLUMN phase text,
    ADD COLUMN pass_prompt_dismissed_at timestamptz,
    ADD CONSTRAINT accounts_phase_known CHECK (phase IS NULL OR phase IN ('evaluation', 'funded', 'live')),
    -- An evaluation is in the evaluation phase; a funded account is funded (simulated) or live.
    ADD CONSTRAINT accounts_phase_matches_type CHECK (phase IS NULL OR (account_type = 'evaluation') = (phase = 'evaluation'));

-- ---------------------------------------------------------------------------------------------------------------
-- Payouts: the gross requested, the split, a fee, and their number in the account's sequence. Payouts recorded before this
-- migration keep empty columns (the gross is not known); their number is set the next time the account's payouts change.

ALTER TABLE account_ledger
    ADD COLUMN payout_number integer,
    ADD COLUMN gross_amount numeric(20, 2),
    ADD COLUMN profit_split_pct numeric(6, 2),
    ADD COLUMN payout_fee numeric(20, 2),
    ADD CONSTRAINT account_ledger_payout_fields CHECK (
        kind = 'payout'
        OR (payout_number IS NULL AND gross_amount IS NULL AND profit_split_pct IS NULL AND payout_fee IS NULL)
    ),
    ADD CONSTRAINT account_ledger_payout_number_positive CHECK (payout_number IS NULL OR payout_number > 0),
    ADD CONSTRAINT account_ledger_payout_fee_not_negative CHECK (payout_fee IS NULL OR payout_fee >= 0),
    ADD CONSTRAINT account_ledger_split_range CHECK (
        profit_split_pct IS NULL OR (profit_split_pct > 0 AND profit_split_pct <= 100)
    ),
    -- Gross and split come together, and the net (amount) is derived from them: rounded to the cent, half up.
    ADD CONSTRAINT account_ledger_net_is_gross_times_split CHECK (
        (gross_amount IS NULL AND profit_split_pct IS NULL)
        OR (gross_amount IS NOT NULL AND profit_split_pct IS NOT NULL
            AND amount = round(gross_amount * profit_split_pct / 100, 2))
    );
