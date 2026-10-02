"""SQLite connection handling and schema migrations.

Storage conventions:
- Money, prices, fee rates, points and ticks are TEXT holding exact decimal strings
  ("-475.00", "16458.925"). Never REAL: SQLite would store them as binary floats.
  Aggregation happens in Python with Decimal (see finance.py).
- Timestamps are TEXT in UTC, "YYYY-MM-DDTHH:MM:SSZ". Fixed width, so they sort and
  compare correctly as text.
- Trading days are TEXT "YYYY-MM-DD".
"""

from __future__ import annotations

import itertools
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_V1 = """
CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE TABLE accounts (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
    firm                TEXT NOT NULL DEFAULT 'Other' CHECK (firm IN ('Apex', 'Lucid', 'Other')),
    account_size        TEXT,
    starting_balance    TEXT NOT NULL DEFAULT '0',
    -- Prop firm rules. NULL means the rule is not tracked for this account.
    max_drawdown        TEXT,
    trailing_type       TEXT CHECK (trailing_type IN ('intraday', 'eod')),
    trail_lock_enabled  INTEGER NOT NULL DEFAULT 0,
    trail_lock_level    TEXT,   -- balance at which the drawdown threshold stops trailing
    daily_loss_limit    TEXT,
    profit_target       TEXT,
    created_at          TEXT NOT NULL
);

-- $ per contract round trip. symbol_root '*' applies to roots without their own row.
CREATE TABLE fee_schedules (
    account_id           INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    symbol_root          TEXT NOT NULL,
    fee_per_contract_rt  TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol_root)
);

CREATE TABLE imports (
    id               INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    filename         TEXT NOT NULL,
    file_sha256      TEXT NOT NULL,
    source_timezone  TEXT NOT NULL,
    imported_at      TEXT NOT NULL,
    rows_parsed      INTEGER NOT NULL DEFAULT 0,
    rows_added       INTEGER NOT NULL DEFAULT 0,
    rows_duplicate   INTEGER NOT NULL DEFAULT 0,
    rows_error       INTEGER NOT NULL DEFAULT 0,
    trades_affected  INTEGER NOT NULL DEFAULT 0,
    undone_at        TEXT
);

CREATE TABLE trades (
    id               INTEGER PRIMARY KEY,
    account_id       INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    symbol           TEXT NOT NULL,
    symbol_root      TEXT NOT NULL,
    contract_month   TEXT,
    direction        TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    entry_at         TEXT NOT NULL,
    exit_at          TEXT NOT NULL,
    trade_date       TEXT NOT NULL,
    hold_seconds     INTEGER NOT NULL,
    total_qty        INTEGER NOT NULL,
    max_position     INTEGER NOT NULL,
    fill_pair_count  INTEGER NOT NULL,
    tick_size        TEXT NOT NULL,
    avg_entry        TEXT NOT NULL,
    avg_exit         TEXT NOT NULL,
    points           TEXT NOT NULL,
    ticks            TEXT NOT NULL,
    gross_pnl        TEXT NOT NULL,
    fees             TEXT NOT NULL,
    net_pnl          TEXT NOT NULL,
    notes            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_trades_account_date ON trades (account_id, trade_date);
CREATE INDEX idx_trades_account_symbol_time ON trades (account_id, symbol, entry_at, exit_at);

-- Raw rows from the CSV, linked to the trade they were grouped into.
-- Unique on the fill pair so re-importing overlapping exports never double-counts.
CREATE TABLE fill_pairs (
    id                 INTEGER PRIMARY KEY,
    account_id         INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    trade_id           INTEGER REFERENCES trades(id) ON DELETE SET NULL,
    symbol             TEXT NOT NULL,
    tick_size          TEXT NOT NULL,
    price_format       TEXT NOT NULL,
    price_format_type  TEXT NOT NULL,
    buy_fill_id        INTEGER NOT NULL,
    sell_fill_id       INTEGER NOT NULL,
    qty                INTEGER NOT NULL CHECK (qty > 0),
    buy_price          TEXT NOT NULL,
    sell_price         TEXT NOT NULL,
    pnl                TEXT NOT NULL,
    bought_at          TEXT NOT NULL,
    sold_at            TEXT NOT NULL,
    duration_raw       TEXT NOT NULL,
    duration_seconds   INTEGER NOT NULL,
    direction          TEXT NOT NULL CHECK (direction IN ('long', 'short')),
    UNIQUE (buy_fill_id, sell_fill_id)
);
CREATE INDEX idx_fill_pairs_trade ON fill_pairs (trade_id);

-- Every import that contained a fill pair. Undoing an import deletes a pair only
-- when no other import still contains it.
CREATE TABLE import_fill_pairs (
    import_id     INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    fill_pair_id  INTEGER NOT NULL REFERENCES fill_pairs(id) ON DELETE CASCADE,
    PRIMARY KEY (import_id, fill_pair_id)
);
CREATE INDEX idx_import_fill_pairs_pair ON import_fill_pairs (fill_pair_id);

CREATE TABLE tag_groups (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    sort_order  INTEGER NOT NULL DEFAULT 0
);
INSERT INTO tag_groups (name, sort_order) VALUES ('Setup', 1), ('Mistake', 2), ('Emotion', 3);

CREATE TABLE tags (
    id        INTEGER PRIMARY KEY,
    group_id  INTEGER NOT NULL REFERENCES tag_groups(id) ON DELETE CASCADE,
    name      TEXT NOT NULL COLLATE NOCASE,
    UNIQUE (group_id, name)
);

CREATE TABLE trade_tags (
    trade_id  INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (trade_id, tag_id)
);

CREATE TABLE day_notes (
    trade_date  TEXT PRIMARY KEY,
    notes       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);

CREATE TABLE screenshots (
    id             INTEGER PRIMARY KEY,
    trade_id       INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
    stored_name    TEXT NOT NULL UNIQUE,  -- file name inside the data/screenshots folder
    original_name  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""

# v2: whether an account is a prop-firm evaluation or a funded account. Existing
# accounts become evaluations; change them in Settings.
ACCOUNT_TYPE_V2 = """
ALTER TABLE accounts ADD COLUMN account_type TEXT NOT NULL DEFAULT 'evaluation'
    CHECK (account_type IN ('evaluation', 'funded'));
"""

# v3: drop the database-level list of firms (Apex/Lucid/Other) so the list can grow in
# code (accounts.FIRMS) without a migration each time. SQLite can't alter a CHECK
# constraint, so the table is rebuilt with the same ids and data.
FREE_FIRM_V3 = """
CREATE TABLE accounts_v3 (
    id                  INTEGER PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE COLLATE NOCASE,
    firm                TEXT NOT NULL DEFAULT 'Other',
    account_type        TEXT NOT NULL DEFAULT 'evaluation' CHECK (account_type IN ('evaluation', 'funded')),
    account_size        TEXT,
    starting_balance    TEXT NOT NULL DEFAULT '0',
    max_drawdown        TEXT,
    trailing_type       TEXT CHECK (trailing_type IN ('intraday', 'eod')),
    trail_lock_enabled  INTEGER NOT NULL DEFAULT 0,
    trail_lock_level    TEXT,
    daily_loss_limit    TEXT,
    profit_target       TEXT,
    created_at          TEXT NOT NULL
);
INSERT INTO accounts_v3 (id, name, firm, account_type, account_size, starting_balance, max_drawdown,
                         trailing_type, trail_lock_enabled, trail_lock_level, daily_loss_limit,
                         profit_target, created_at)
    SELECT id, name, firm, account_type, account_size, starting_balance, max_drawdown,
           trailing_type, trail_lock_enabled, trail_lock_level, daily_loss_limit,
           profit_target, created_at
    FROM accounts;
DROP TABLE accounts;
ALTER TABLE accounts_v3 RENAME TO accounts;
"""

# v4: archiving. An account with an outcome is archived: hidden from the dashboard and
# trade log unless asked for, but never deleted. ended_on is the date it finished.
ARCHIVE_V4 = """
ALTER TABLE accounts ADD COLUMN outcome TEXT CHECK (outcome IN ('passed', 'failed', 'closed'));
ALTER TABLE accounts ADD COLUMN ended_on TEXT;
ALTER TABLE accounts ADD COLUMN archive_note TEXT NOT NULL DEFAULT '';
"""

# v5: money paid for and received from prop accounts, separate from trading P&L.
# amount is always positive; kind says whether it's a cost or a payout (see ledger.py).
LEDGER_V5 = """
CREATE TABLE account_ledger (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    amount      TEXT NOT NULL,
    entry_date  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_account_ledger_account ON account_ledger (account_id, entry_date);
"""

# v6: editable tag colors. Until now a tag's color followed its id through eight preset
# colors; existing tags keep exactly that color (journal.TAG_COLOR_PRESETS, same order).
TAG_COLOR_V6 = """
ALTER TABLE tags ADD COLUMN color TEXT NOT NULL DEFAULT '#2a78d6'
    CHECK (color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]');
UPDATE tags SET color = CASE (id - 1) % 8
    WHEN 0 THEN '#2a78d6' WHEN 1 THEN '#6e5adc' WHEN 2 THEN '#0ca30c' WHEN 3 THEN '#eb6834'
    WHEN 4 THEN '#e87ba4' WHEN 5 THEN '#1baf7a' WHEN 6 THEN '#d03b3b' ELSE '#eda100' END;
"""

# Append new migrations; never edit one that has shipped. PRAGMA user_version
# records how many have been applied.
MIGRATIONS: list[str] = [SCHEMA_V1, ACCOUNT_TYPE_V2, FREE_FIRM_V3, ARCHIVE_V4, LEDGER_V5, TAG_COLOR_V6]

_savepoint_ids = itertools.count()


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the journal database and bring its schema up to date."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # transactions are explicit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations, each in its own transaction.

    Foreign-key enforcement is paused while migrating (table rebuilds need that, and
    SQLite only changes it outside a transaction). Each migration is checked for
    broken references before it commits.
    """
    applied = conn.execute("PRAGMA user_version").fetchone()[0]
    if applied >= len(MIGRATIONS):
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for version, script in enumerate(MIGRATIONS[applied:], start=applied + 1):
            try:
                conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};")
                broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise sqlite3.IntegrityError(f"migration {version} left broken references: {broken[:5]}")
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


@contextmanager
def transaction(conn: sqlite3.Connection, *, rollback: bool = False) -> Iterator[sqlite3.Connection]:
    """Run a block atomically; nests safely. rollback=True always discards the changes,
    which is how import previews run the real import without keeping it."""
    name = f"sp_{next(_savepoint_ids)}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield conn
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    if rollback:
        conn.execute(f"ROLLBACK TO {name}")
    conn.execute(f"RELEASE {name}")
