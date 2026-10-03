-- The ledger rules the Python module enforces (a known kind, an amount above zero) also hold in the database,
-- so a second code path or a hand-written fix-up script cannot store a row the app would have refused.
-- The kinds must match tradejournal.ledger.KINDS; a test compares the two.

ALTER TABLE account_ledger
    ADD CONSTRAINT account_ledger_kind_known CHECK (
        kind IN ('evaluation_fee', 'reset_fee', 'activation_fee', 'monthly_fee', 'other_cost', 'payout')
    ),
    ADD CONSTRAINT account_ledger_amount_positive CHECK (amount > 0);
