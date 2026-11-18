-- 0025: orders from platforms that do not pair their own fills (TradeSea first), and which platform each import came from.
--
-- Tradovate's Performance export gives fill pairs (one buy matched to one sell), so `fill_pairs` is filled straight from
-- the file. TradeSea's Orders export gives one row per filled order and no pairs. Its orders are stored here as they are,
-- and the fill pairs of an account and symbol are worked out again, first in first out, from every order stored, after
-- each import and each undo (`tradejournal/pg/executions.py`). Pairing inside one file would lose a position held from
-- one file to the next, or read the next file's closing order as a new trade the other way round (decision D2 of the
-- import plan, decision T1 of `docs/design/tradesea-import-plan.md`).
--
-- `ref` is built from what the order is (its time, symbol, side, quantity, type and prices), because the file has no ids.
-- It is unique per account, not per person: copy traders hold the same orders in several accounts, and nothing in the
-- file tells those apart. `price` is the exact average fill price (27420.8333333333 for 3 contracts whose fills total
-- 82,262.50), so it is plain numeric. `commission` is the file's own figure for the whole order, kept for the comparison
-- the preview shows; a trade's fees still come from the account's fee schedule, as for every import.
--
-- `import_executions` says which imports brought which order in, like `import_fill_pairs`: undoing an import removes an
-- order only when no other import still holds it.
--
-- Additive: two new tables and a column with a default, so the running app works on the new schema before its deploy.

-- A source is a name in code (`tradovate_performance`, `tradesea_orders`); the check is its shape, so the next platform
-- needs no migration for it.
ALTER TABLE imports ADD COLUMN source text NOT NULL DEFAULT 'tradovate_performance'
    CONSTRAINT imports_source_shape CHECK (source ~ '^[a-z][a-z0-9_]{0,39}$');

CREATE TABLE executions (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      uuid NOT NULL DEFAULT app_user_id(),
    account_id   bigint NOT NULL,
    source       text NOT NULL CHECK (source ~ '^[a-z][a-z0-9_]{0,39}$'),
    ref          text NOT NULL CHECK (length(ref) BETWEEN 1 AND 200),
    executed_at  timestamptz NOT NULL,
    symbol       text NOT NULL CHECK (length(symbol) BETWEEN 1 AND 40),
    side         text NOT NULL CHECK (side IN ('buy', 'sell')),
    qty          integer NOT NULL CHECK (qty > 0),
    price        numeric NOT NULL CHECK (price > 0),
    commission   numeric(20, 2),
    order_type   text NOT NULL DEFAULT '' CHECK (length(order_type) <= 40),
    line         integer NOT NULL DEFAULT 0,
    UNIQUE (user_id, id),
    UNIQUE (user_id, account_id, source, ref),
    FOREIGN KEY (user_id, account_id) REFERENCES accounts (user_id, id) ON DELETE CASCADE
);
-- Pairing reads one account and symbol in time order; the id breaks ties within a second.
CREATE INDEX executions_user_account_symbol_time ON executions (user_id, account_id, source, symbol, executed_at, id);

CREATE TABLE import_executions (
    user_id       uuid NOT NULL DEFAULT app_user_id(),
    import_id     bigint NOT NULL,
    execution_id  bigint NOT NULL,
    PRIMARY KEY (user_id, import_id, execution_id),
    FOREIGN KEY (user_id, import_id) REFERENCES imports (user_id, id) ON DELETE CASCADE,
    FOREIGN KEY (user_id, execution_id) REFERENCES executions (user_id, id) ON DELETE CASCADE
);
CREATE INDEX import_executions_execution ON import_executions (user_id, execution_id);

-- Fill pairs worked out from orders are looked up by account and symbol when the orders change.
CREATE INDEX fill_pairs_user_account_source_symbol ON fill_pairs (user_id, account_id, source, symbol);

ALTER TABLE executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON executions USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON executions TO app_rw;

ALTER TABLE import_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE import_executions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant ON import_executions USING (user_id = app_user_id()) WITH CHECK (user_id = app_user_id());
GRANT SELECT, INSERT, UPDATE, DELETE ON import_executions TO app_rw;
