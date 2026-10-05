"""Move one person's local SQLite journal into their Postgres tenant, and prove nothing changed on the way (M2).

    python -m tradejournal.pg.migrate_local --source path/to/journal.db --user-id <uuid> --dry-run
    python -m tradejournal.pg.migrate_local --source path/to/journal.db --user-id <uuid>

``TJ_DATABASE_URL`` (the request role, ``app_rw``) names the database, and ``TJ_FILES_DIR`` (or ``--files-dir``) the
folder that holds screenshot files, or the four ``TJ_S3_*`` settings the bucket that does (one or the other). The user must exist already: sign in once, so
the identity provider has created the account, then use that account's id. The original journal is never opened. Its
file (and a ``-wal`` file beside it, if any) is copied to a temporary folder first, and the copy is what is read and
brought up to the current schema.

Everything happens in one transaction as the user, so a failure leaves nothing behind, and ``--dry-run`` rolls back
after printing the report. It refuses a user who already has any accounts or notes, rather than guess how to merge.

The **golden check** recomputes the same facts on both sides (row counts, per-account totals, per-day results, a
fingerprint of every note, tag, screenshot name and ledger entry) and compares them. Totals are exact decimals. Trades
are copied as they were stored, not regrouped, so a difference means the copy is wrong. Two more checks are shown but
do not fail the run: what the *current* grouping and fee code would change about the stored trades, which is how an old
journal reveals that a rule has moved since it was written.

**Screenshots.** With a file store given, each screenshot's file is checked the way an upload is (a real PNG, JPEG, GIF
or WebP of a sane size, within the per-trade and per-person limits), copied into the store under a new key
(``u/<user id>/<random>.<ext>``), and its row records that key, its type, size and dimensions. The golden check then
compares the rows with the old file names mapped to the new keys, and reads **every copied file back from the store** and
compares its SHA-256 with the original's. A file that is missing, not an acceptable image, or over a limit is *not*
copied: its row is still copied under its old name, and the report says which and why (an export lists such a row as
missing). A dry run checks every file and writes none. A failure removes the files already copied. With no store given,
only the rows are copied, and the report says the files were not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import tuple_row

from .. import db as sqlite_db
from .. import images
from .. import settings as sqlite_settings
from ..bucketstore import BucketFileStore, BucketSettings
from ..filestore import FileMissing, FileStore, LocalFileStore
from ..finance import plain, to_money
from ..settings import DEFAULTS
from ..timeutil import from_utc_iso, to_utc_iso
from . import screenshots as pg_screenshots
from . import settings as pg_settings
from . import store, usage
from .defaults import provision_defaults
from .session import HEAVY_STATEMENT_TIMEOUT_MS, Database
from .store import _returned_ids

MONEY_ACCOUNT_COLUMNS = ("account_size", "max_drawdown", "trail_lock_level", "daily_loss_limit", "profit_target")
TENANT_TABLES = (
    "accounts", "fee_schedules", "imports", "trades", "fill_pairs", "import_fill_pairs", "tags", "trade_tags",
    "day_notes", "screenshots", "account_ledger",
)  # fmt: skip


class MigrationRefused(RuntimeError):
    """The target is not in a state this script will write to."""


@dataclass(frozen=True)
class Check:
    name: str
    source: object
    target: object
    informational: bool = False  # shown, but a difference does not fail the run

    @property
    def ok(self) -> bool:
        return self.source == self.target


@dataclass(frozen=True)
class PlacedFile:
    """A screenshot file taken into the store (or, in a dry run, one that would be)."""

    source_name: str  # its name in the old journal's screenshots folder
    key: str  # where it is in the store
    sha256: str  # of the original's bytes
    size: int
    content_type: str
    width: int
    height: int


@dataclass
class MigrationReport:
    dry_run: bool
    copied: dict[str, int] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    missing_screenshot_files: list[str] = field(default_factory=list)
    skipped_settings: list[str] = field(default_factory=list)
    placed: list[PlacedFile] = field(default_factory=list)  # files taken into the store, in the order they were
    files_not_copied: list[tuple[str, str]] = field(default_factory=list)  # (old name, the reason in words)
    store_given: bool = False  # false: only the screenshot rows were copied

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks if not check.informational)


# ---- reading the source ----------------------------------------------------------------------------------------


def _money(text: str | None) -> Decimal | None:
    return None if text is None else to_money(Decimal(text))


def _number(text: str) -> Decimal:
    """A price or quantity as stored in Postgres: no trailing zeros (see store._num)."""
    return Decimal(plain(Decimal(text)))


def _timestamp(text: str | None):
    return None if text is None else from_utc_iso(text)


def _rows(source: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return source.execute(query).fetchall()


# ---- the copy --------------------------------------------------------------------------------------------------


def copy_journal(
    conn: psycopg.Connection,
    source: sqlite3.Connection,
    screenshots_dir: Path | None,
    *,
    files: FileStore | None = None,
    user_id: UUID | None = None,
    write_files: bool = True,
    report: MigrationReport | None = None,
) -> MigrationReport:
    """Copy every table. `conn` is a tenant session; the caller owns the transaction. With `files` (and `user_id`, for the
    keys) the screenshot files are taken into the store too, and `write_files=False` checks them without writing any. The
    caller passes its own `report` so that, if this fails half way, it knows which files to take out again."""
    report = report or MigrationReport(dry_run=False)
    _refuse_if_not_empty(conn)
    store.lock_user(conn)

    for row in _rows(source, "SELECT key, value FROM settings"):
        if row["key"] in DEFAULTS:
            pg_settings.set_setting(conn, row["key"], row["value"])
        else:
            report.skipped_settings.append(row["key"])

    group_ids = _copy_tag_groups(conn, source)
    tag_ids = {}
    for row in _rows(source, "SELECT * FROM tags ORDER BY id"):
        tag_ids[row["id"]] = conn.execute(
            "INSERT INTO tags (group_id, name, color) VALUES (%s, %s, %s) RETURNING id",
            (group_ids[row["group_id"]], row["name"], row["color"]),
        ).fetchone()["id"]

    account_ids = {}
    for row in _rows(source, "SELECT * FROM accounts ORDER BY id"):
        account_ids[row["id"]] = conn.execute(
            """INSERT INTO accounts (name, firm, account_type, account_size, starting_balance, max_drawdown,
                   trailing_type, trail_lock_enabled, trail_lock_level, daily_loss_limit, profit_target, created_at,
                   outcome, ended_on, archive_note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                row["name"], row["firm"], row["account_type"], _money(row["account_size"]),
                _money(row["starting_balance"]), _money(row["max_drawdown"]), row["trailing_type"],
                bool(row["trail_lock_enabled"]), _money(row["trail_lock_level"]), _money(row["daily_loss_limit"]),
                _money(row["profit_target"]), _timestamp(row["created_at"]), row["outcome"],
                None if row["ended_on"] is None else date.fromisoformat(row["ended_on"]), row["archive_note"],
            ),
        ).fetchone()["id"]  # fmt: skip

    _batch(
        conn,
        "INSERT INTO fee_schedules (account_id, symbol_root, fee_per_contract_rt) VALUES (%s, %s, %s)",
        [
            (account_ids[r["account_id"]], r["symbol_root"], Decimal(r["fee_per_contract_rt"]))
            for r in _rows(source, "SELECT * FROM fee_schedules ORDER BY account_id, symbol_root")
        ],
    )

    import_ids = {}
    for row in _rows(source, "SELECT * FROM imports ORDER BY id"):
        import_ids[row["id"]] = conn.execute(
            """INSERT INTO imports (account_id, filename, file_sha256, source_timezone, imported_at, rows_parsed,
                   rows_added, rows_duplicate, rows_error, trades_affected, undone_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                account_ids[row["account_id"]], row["filename"], row["file_sha256"], row["source_timezone"],
                _timestamp(row["imported_at"]), row["rows_parsed"], row["rows_added"], row["rows_duplicate"],
                row["rows_error"], row["trades_affected"], _timestamp(row["undone_at"]),
            ),
        ).fetchone()["id"]  # fmt: skip

    trade_rows = _rows(source, "SELECT * FROM trades ORDER BY id")
    trade_ids = dict(
        zip(
            (r["id"] for r in trade_rows),
            _batch(
                conn,
                """INSERT INTO trades (account_id, symbol, symbol_root, contract_month, direction, entry_at, exit_at,
                       trade_date, hold_seconds, total_qty, max_position, fill_pair_count, tick_size, avg_entry,
                       avg_exit, points, ticks, gross_pnl, fees, net_pnl, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                [
                    (
                        account_ids[r["account_id"]], r["symbol"], r["symbol_root"], r["contract_month"],
                        r["direction"], _timestamp(r["entry_at"]), _timestamp(r["exit_at"]),
                        date.fromisoformat(r["trade_date"]), r["hold_seconds"], r["total_qty"], r["max_position"],
                        r["fill_pair_count"], _number(r["tick_size"]), _number(r["avg_entry"]),
                        _number(r["avg_exit"]), _number(r["points"]), _number(r["ticks"]),
                        _money(r["gross_pnl"]), _money(r["fees"]), _money(r["net_pnl"]), r["notes"],
                    )
                    for r in trade_rows
                ],
                returning=True,
            ),
            strict=True,
        )
    )  # fmt: skip

    pair_rows = _rows(source, "SELECT * FROM fill_pairs ORDER BY id")
    pair_ids = dict(
        zip(
            (r["id"] for r in pair_rows),
            _batch(
                conn,
                """INSERT INTO fill_pairs (account_id, trade_id, source, buy_ref, sell_ref, symbol, tick_size,
                       price_format, price_format_type, qty, buy_price, sell_price, pnl, bought_at, sold_at,
                       duration_raw, duration_seconds, direction)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                [
                    (
                        account_ids[r["account_id"]], None if r["trade_id"] is None else trade_ids[r["trade_id"]],
                        store.SOURCE, str(r["buy_fill_id"]), str(r["sell_fill_id"]), r["symbol"],
                        _number(r["tick_size"]), r["price_format"], r["price_format_type"], r["qty"],
                        _number(r["buy_price"]), _number(r["sell_price"]), _money(r["pnl"]),
                        _timestamp(r["bought_at"]), _timestamp(r["sold_at"]), r["duration_raw"],
                        r["duration_seconds"], r["direction"],
                    )
                    for r in pair_rows
                ],
                returning=True,
            ),
            strict=True,
        )
    )  # fmt: skip

    _batch(
        conn,
        "INSERT INTO import_fill_pairs (import_id, fill_pair_id) VALUES (%s, %s)",
        [
            (import_ids[r["import_id"]], pair_ids[r["fill_pair_id"]])
            for r in _rows(source, "SELECT * FROM import_fill_pairs ORDER BY import_id, fill_pair_id")
        ],
    )
    _batch(
        conn,
        "INSERT INTO trade_tags (trade_id, tag_id) VALUES (%s, %s)",
        [
            (trade_ids[r["trade_id"]], tag_ids[r["tag_id"]])
            for r in _rows(source, "SELECT * FROM trade_tags ORDER BY trade_id, tag_id")
        ],
    )
    _batch(
        conn,
        "INSERT INTO day_notes (trade_date, notes, updated_at) VALUES (%s, %s, %s)",
        [
            (date.fromisoformat(r["trade_date"]), r["notes"], _timestamp(r["updated_at"]))
            for r in _rows(source, "SELECT * FROM day_notes ORDER BY trade_date")
        ],
    )
    screenshot_rows = _rows(source, "SELECT * FROM screenshots ORDER BY id")
    report.store_given = files is not None and user_id is not None and screenshots_dir is not None
    taken = _take_files(screenshot_rows, screenshots_dir, files, user_id, write=write_files, report=report)
    _batch(
        conn,
        "INSERT INTO screenshots (trade_id, stored_name, original_name, created_at, content_type, size_bytes, width, height)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                trade_ids[r["trade_id"]],
                taken[r["id"]].key if r["id"] in taken else r["stored_name"],
                r["original_name"],
                _timestamp(r["created_at"]),
                taken[r["id"]].content_type if r["id"] in taken else None,
                taken[r["id"]].size if r["id"] in taken else 0,
                taken[r["id"]].width if r["id"] in taken else None,
                taken[r["id"]].height if r["id"] in taken else None,
            )
            for r in screenshot_rows
        ],
    )
    if screenshots_dir is not None:
        report.missing_screenshot_files = [
            r["stored_name"] for r in screenshot_rows if not (screenshots_dir / r["stored_name"]).is_file()
        ]
    _batch(
        conn,
        "INSERT INTO account_ledger (account_id, kind, amount, entry_date, note, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        [
            (
                account_ids[r["account_id"]], r["kind"], _money(r["amount"]), date.fromisoformat(r["entry_date"]),
                r["note"], _timestamp(r["created_at"]),
            )
            for r in _rows(source, "SELECT * FROM account_ledger ORDER BY id")
        ],
    )  # fmt: skip

    usage.refresh(conn)
    report.copied = {
        "accounts": len(account_ids), "fee_schedules": _count(source, "fee_schedules"), "imports": len(import_ids),
        "trades": len(trade_ids), "fill_pairs": len(pair_ids), "tags": len(tag_ids),
        "trade_tags": _count(source, "trade_tags"), "day_notes": _count(source, "day_notes"),
        "screenshots": len(screenshot_rows), "account_ledger": _count(source, "account_ledger"),
    }  # fmt: skip
    return report


def _take_files(
    rows: list[sqlite3.Row],
    screenshots_dir: Path | None,
    files: FileStore | None,
    user_id: UUID | None,
    *,
    write: bool,
    report: MigrationReport,
) -> dict[int, PlacedFile]:
    """Check each screenshot's file the way an upload is checked and, with `write`, put it in the store. Returns the
    files taken, by the old row's id. One that is not taken is listed in the report with its reason, never fatal."""
    taken: dict[int, PlacedFile] = {}
    if files is None or user_id is None or screenshots_dir is None:
        return taken
    on_trade: dict[int, int] = {}
    held = 0
    for row in rows:
        name = row["stored_name"]
        path = screenshots_dir / name
        if Path(name).name != name or not path.is_file():
            continue  # a missing file is listed with the others below; a name with a folder in it is never opened
        reason, info, data = None, None, b""
        if path.stat().st_size > pg_screenshots.MAX_FILE_BYTES:
            reason = f"larger than {pg_screenshots.MAX_FILE_BYTES // (1024 * 1024)} MB"
        else:
            data = path.read_bytes()
            try:
                info = images.inspect(data)
            except images.ImageRefused as refusal:
                reason = str(refusal)
        if reason is None and on_trade.get(row["trade_id"], 0) >= pg_screenshots.MAX_PER_TRADE:
            reason = f"its trade already has the {pg_screenshots.MAX_PER_TRADE} screenshots one may have"
        if reason is None and held + len(data) > pg_screenshots.MAX_STORED_BYTES:
            reason = (
                f"over the {pg_screenshots.MAX_STORED_BYTES // (1024 * 1024)} MB of screenshot space one person has"
            )
        if reason is not None or info is None:
            report.files_not_copied.append((name, reason or "not an acceptable image"))
            continue
        key = f"u/{user_id}/{uuid.uuid4().hex}{info.extension}"
        if write:
            files.put(key, data)
        placed = PlacedFile(
            name, key, hashlib.sha256(data).hexdigest(), len(data), info.content_type, info.width, info.height
        )
        report.placed.append(placed)  # at once, so a failure further on can take it out again
        taken[row["id"]] = placed
        on_trade[row["trade_id"]] = on_trade.get(row["trade_id"], 0) + 1
        held += len(data)
    return taken


def _refuse_if_not_empty(conn: psycopg.Connection) -> None:
    row = conn.execute(
        """SELECT (SELECT count(*) FROM accounts) AS accounts, (SELECT count(*) FROM tags) AS tags,
                  (SELECT count(*) FROM day_notes) AS day_notes, (SELECT count(*) FROM trades) AS trades"""
    ).fetchone()
    if any(row.values()):
        raise MigrationRefused(
            "this user already has journal data ("
            + ", ".join(f"{count} {name}" for name, count in row.items() if count)
            + "); the migration only fills an empty journal"
        )


def _copy_tag_groups(conn: psycopg.Connection, source: sqlite3.Connection) -> dict[int, int]:
    """New users start with Setup, Mistake and Emotion. Source groups of the same name (any capitalization) reuse them."""
    existing = {row["name"].lower(): row["id"] for row in conn.execute("SELECT id, name FROM tag_groups")}
    ids = {}
    for row in _rows(source, "SELECT * FROM tag_groups ORDER BY id"):
        found = existing.get(row["name"].lower())
        if found is None:
            found = conn.execute(
                "INSERT INTO tag_groups (name, sort_order) VALUES (%s, %s) RETURNING id",
                (row["name"], row["sort_order"]),
            ).fetchone()["id"]
        else:
            conn.execute(
                "UPDATE tag_groups SET name = %s, sort_order = %s WHERE id = %s",
                (row["name"], row["sort_order"], found),
            )
        ids[row["id"]] = found
    return ids


def _batch(conn: psycopg.Connection, query: str, rows: list[tuple], *, returning: bool = False) -> list[int]:
    if not rows:
        return []
    with conn.cursor() as cursor:
        cursor.executemany(query, rows, returning=returning)
        return _returned_ids(cursor) if returning else []


def _count(source: sqlite3.Connection, table: str) -> int:
    return source.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608 (fixed table names)


# ---- the golden check ------------------------------------------------------------------------------------------

# Each fact is asked of both databases in the same plain SQL (a few differ only in a column's name), the answers are
# normalised so that equal things look equal (a decimal with trailing zeros, a timestamp, a boolean stored as 0 or 1),
# and the two answers are compared. Rows are matched by what identifies them (account name, entry time, symbol), never
# by id, because the copy has new ids.

_DECIMAL = re.compile(r"-?\d+(\.\d+)?")

_TRADE_COLUMNS = (
    "a.name, t.entry_at, t.exit_at, t.trade_date, t.symbol, t.symbol_root, t.contract_month, t.direction, "
    "t.hold_seconds, t.total_qty, t.max_position, t.fill_pair_count, t.tick_size, t.avg_entry, t.avg_exit, "
    "t.points, t.ticks, t.gross_pnl, t.fees, t.net_pnl, t.notes"
)
_FROM_TRADES = "FROM trades t JOIN accounts a ON a.id = t.account_id"
_TRADE_KEY = "a.name, t.entry_at, t.symbol"

QUERIES = {
    "accounts, every column": (
        "SELECT name, firm, account_type, account_size, starting_balance, max_drawdown, trailing_type, "
        "trail_lock_enabled, trail_lock_level, daily_loss_limit, profit_target, created_at, outcome, ended_on, "
        "archive_note FROM accounts"
    ),
    "fee schedules": (
        "SELECT a.name, f.symbol_root, f.fee_per_contract_rt FROM fee_schedules f JOIN accounts a ON a.id = f.account_id"
    ),
    "every trade, every column": f"SELECT {_TRADE_COLUMNS} {_FROM_TRADES}",  # noqa: S608 (fixed column names)
    "which tag sits on which trade": (
        f"SELECT {_TRADE_KEY}, g.name, tg.name "  # noqa: S608
        f"{_FROM_TRADES} JOIN trade_tags x ON x.trade_id = t.id JOIN tags tg ON tg.id = x.tag_id "
        "JOIN tag_groups g ON g.id = tg.group_id"
    ),
    "tags: group, name, color": (
        "SELECT g.name, tg.name, tg.color FROM tags tg JOIN tag_groups g ON g.id = tg.group_id"
    ),
    "screenshots: trade, original name, stored name": (
        f"SELECT {_TRADE_KEY}, s.original_name, s.stored_name, s.created_at "  # noqa: S608
        f"{_FROM_TRADES} JOIN screenshots s ON s.trade_id = t.id"
    ),
    "day notes": "SELECT trade_date, notes, updated_at FROM day_notes",
    "ledger entries": (
        "SELECT a.name, l.kind, l.amount, l.entry_date, l.note, l.created_at "
        "FROM account_ledger l JOIN accounts a ON a.id = l.account_id"
    ),
}

# The same questions where the column is called something else on the Postgres side (a fill id is a text reference).
_PAIR_COLUMNS = (
    "a.name, f.symbol, f.tick_size, f.price_format, f.price_format_type, f.{buy}, f.{sell}, f.qty, f.buy_price, "
    "f.sell_price, f.pnl, f.bought_at, f.sold_at, f.duration_raw, f.duration_seconds, f.direction, "
    "t.entry_at, t.symbol"
)
_PAIR_FROM = "FROM fill_pairs f JOIN accounts a ON a.id = f.account_id LEFT JOIN trades t ON t.id = f.trade_id"
_LINK_COLUMNS = "i.filename, i.file_sha256, i.imported_at, f.{buy}, f.{sell}"
_LINK_FROM = "FROM import_fill_pairs x JOIN imports i ON i.id = x.import_id JOIN fill_pairs f ON f.id = x.fill_pair_id"
SPLIT_QUERIES = {
    "fill pairs, every column, and the trade each belongs to": (
        f"SELECT {_PAIR_COLUMNS.format(buy='buy_fill_id', sell='sell_fill_id')} {_PAIR_FROM}",  # noqa: S608
        f"SELECT {_PAIR_COLUMNS.format(buy='buy_ref', sell='sell_ref')} {_PAIR_FROM}",  # noqa: S608
    ),
    "which import brought in which fill pair": (
        f"SELECT {_LINK_COLUMNS.format(buy='buy_fill_id', sell='sell_fill_id')} {_LINK_FROM}",  # noqa: S608
        f"SELECT {_LINK_COLUMNS.format(buy='buy_ref', sell='sell_ref')} {_LINK_FROM}",  # noqa: S608
    ),
}
_IMPORTS = (
    "SELECT a.name, i.filename, i.file_sha256, i.source_timezone, i.imported_at, i.rows_parsed, i.rows_added, "
    "i.rows_duplicate, i.rows_error, i.trades_affected, i.undone_at FROM imports i JOIN accounts a ON a.id = i.account_id"
)
QUERIES["imports, every column"] = _IMPORTS

COUNTED_TABLES = (
    "accounts", "fee_schedules", "imports", "trades", "fill_pairs", "import_fill_pairs", "tags", "trade_tags",
    "day_notes", "screenshots", "account_ledger",
)  # fmt: skip
COUNT_QUERIES = {table: f"SELECT count(*) FROM {table}" for table in COUNTED_TABLES}  # noqa: S608 (fixed names)


def _decimal_text(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _norm(value: object) -> object:
    """Make equal things look equal: 1.50 and 1.5, 0 and False, a timestamp and its ISO text."""
    if value is None:
        return None
    if isinstance(value, bool):
        return _decimal_text(Decimal(int(value)))  # SQLite keeps 0 and 1, Postgres has booleans
    if isinstance(value, datetime):
        return to_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal | int):
        return _decimal_text(Decimal(value))
    if isinstance(value, str) and _DECIMAL.fullmatch(value):
        return _decimal_text(Decimal(value))
    return value


def _pg_rows(conn: psycopg.Connection, query: str) -> list[tuple]:
    """Postgres rows as tuples. (The pool's dict rows would silently merge two columns that share a name.)"""
    with conn.cursor(row_factory=tuple_row) as cursor:
        return cursor.execute(query).fetchall()


def _table(rows: list) -> list[list]:
    """Rows as lists of normalised values, in a fixed order that does not depend on ids or on the database's collation."""
    cooked = [[_norm(v) for v in tuple(row)] for row in rows]
    return sorted(cooked, key=lambda row: json.dumps(row, default=str))


def _fingerprint(table: list[list]) -> str:
    digest = hashlib.sha256(json.dumps(table, default=str).encode()).hexdigest()[:16]
    return f"{len(table)} rows, fingerprint {digest}"


def _sum_by(rows: list, key_index: int) -> dict:
    """Group (key..., decimals...) rows: key -> (count, sums of every decimal column), exactly."""
    totals: dict = {}
    for row in rows:
        values = tuple(row)
        key = values[key_index] if not isinstance(values[key_index], date) else values[key_index].isoformat()
        count, sums = totals.get(key, (0, None))
        decimals = [Decimal(str(v)) for i, v in enumerate(values) if i != key_index]
        totals[key] = (count + 1, decimals if sums is None else [s + d for s, d in zip(sums, decimals, strict=True)])
    return {key: (count, tuple(_decimal_text(s) for s in sums)) for key, (count, sums) in sorted(totals.items())}


def _normal_setting(key: str, value: str) -> str:
    """A start time such as 9:30 is stored as 09:30 in Postgres, so both sides are compared in the padded form."""
    return sqlite_settings._parse_hhmm(value).strftime("%H:%M") if key == "trading_day_start" else value


SCREENSHOT_CHECK = "screenshots: trade, original name, stored name"
STORED_NAME_COLUMN = 4  # in that query: the trade's three key columns, the original name, then the stored name


def golden_checks(
    conn: psycopg.Connection,
    source: sqlite3.Connection,
    *,
    placed: Sequence[PlacedFile] = (),
    files: FileStore | None = None,
) -> list[Check]:
    """The same facts, computed independently on each side, then the two informational checks. `placed` are the screenshot
    files taken into the store: the source's stored names are mapped to their new keys before the rows are compared. With
    `files` (not in a dry run, which writes none), every one is also read back from the store and its bytes compared."""
    checks: list[Check] = []
    renamed = {p.source_name: p.key for p in placed}

    def ask(query: str, target_query: str | None = None) -> tuple[list[list], list[list]]:
        return _table(source.execute(query).fetchall()), _table(_pg_rows(conn, target_query or query))

    for table, query in COUNT_QUERIES.items():
        checks.append(Check(f"rows in {table}", source.execute(query).fetchone()[0], _pg_rows(conn, query)[0][0]))

    checks.append(
        Check(
            "settings",
            {key: _normal_setting(key, sqlite_settings.get_setting(source, key)) for key in DEFAULTS},
            {key: _normal_setting(key, pg_settings.get_setting(conn, key)) for key in DEFAULTS},
        )
    )
    # The totals a trader would read off a screen, kept as exact decimals.
    gross_query = "SELECT a.name, t.gross_pnl, t.fees, t.net_pnl FROM trades t JOIN accounts a ON a.id = t.account_id"
    checks.append(
        Check(
            "per account: trade count, gross, fees and net",
            _sum_by(source.execute(gross_query).fetchall(), 0),
            _sum_by(_pg_rows(conn, gross_query), 0),
        )
    )
    day_query = "SELECT t.trade_date, t.net_pnl FROM trades t"
    checks.append(
        Check(
            "net result of every trading day",
            _sum_by(source.execute(day_query).fetchall(), 0),
            _sum_by(_pg_rows(conn, day_query), 0),
        )
    )
    ledger_query = "SELECT l.kind, l.amount FROM account_ledger l"
    checks.append(
        Check(
            "ledger: entries and total per kind",
            _sum_by(source.execute(ledger_query).fetchall(), 0),
            _sum_by(_pg_rows(conn, ledger_query), 0),
        )
    )

    for name, query in QUERIES.items():
        if name == SCREENSHOT_CHECK and renamed:
            rows = [list(row) for row in source.execute(query).fetchall()]
            for row in rows:
                row[STORED_NAME_COLUMN] = renamed.get(row[STORED_NAME_COLUMN], row[STORED_NAME_COLUMN])
            left, right = _table(rows), _table(_pg_rows(conn, query))
        else:
            left, right = ask(query)
        checks.append(Check(name, _fingerprint(left), _fingerprint(right)))
    if placed and files is not None:
        checks.append(
            Check(
                "screenshot files: every file taken is in the store with the same SHA-256",
                {p.source_name: p.sha256 for p in placed},
                {p.source_name: _hash_in_store(files, p.key) for p in placed},
            )
        )
    for name, (source_query, target_query) in SPLIT_QUERIES.items():
        left, right = ask(source_query, target_query)
        checks.append(Check(name, _fingerprint(left), _fingerprint(right)))

    checks.extend(_consistency_with_current_code(conn))
    return checks


def _hash_in_store(files: FileStore, key: str) -> str:
    try:
        return hashlib.sha256(files.get(key)).hexdigest()
    except FileMissing:
        return "not in the store"


def _consistency_with_current_code(conn: psycopg.Connection) -> list[Check]:
    """Informational: would today's grouping and fee code change what was stored? Always rolled back."""
    before = _derived(conn)
    with conn.transaction():
        regrouped = {"created": 0, "updated": 0, "removed": 0}
        for account in conn.execute("SELECT id FROM accounts ORDER BY id").fetchall():
            result = store.rebuild_account(conn, account["id"])
            regrouped["created"] += len(result.created)
            regrouped["updated"] += len(result.updated)
            regrouped["removed"] += len(result.removed)
        store.recalculate_trades(conn)
        after = _derived(conn)
        raise psycopg.Rollback
    changed = sum(1 for key in before.keys() | after.keys() if before.get(key) != after.get(key))
    return [
        Check("current grouping code would regroup these trades (created, updated, removed)", {"created": 0, "updated": 0, "removed": 0}, regrouped, informational=True),
        Check("trades whose stored result today's fee and trading-day rules would change", 0, changed, informational=True),
    ]  # fmt: skip


def _derived(conn: psycopg.Connection) -> dict[tuple, tuple]:
    """Each trade's derived columns, keyed by what identifies it rather than by its id."""
    return {
        (r["account_id"], r["entry_at"], r["symbol"]): (r["gross_pnl"], r["fees"], r["net_pnl"], r["trade_date"], r["fill_pair_count"], r["total_qty"])
        for r in conn.execute("SELECT account_id, entry_at, symbol, gross_pnl, fees, net_pnl, trade_date, fill_pair_count, total_qty FROM trades")
    }  # fmt: skip


# ---- running it ------------------------------------------------------------------------------------------------


def run(
    database: Database,
    user_id: UUID,
    source_path: Path,
    *,
    screenshots_dir: Path | None = None,
    files: FileStore | None = None,
    dry_run: bool = False,
) -> MigrationReport:
    """Copy `source_path` (a journal.db) into `user_id`'s journal. The original is only ever copied, never opened. With
    `files`, the screenshot files go into that store too (a dry run checks them and writes none)."""
    if not source_path.is_file():
        raise FileNotFoundError(f"no journal at {source_path}")
    report = MigrationReport(dry_run=dry_run)
    with tempfile.TemporaryDirectory(prefix="tj-migrate-") as workdir:
        copy = Path(workdir) / "journal.db"
        shutil.copy2(source_path, copy)
        for suffix in ("-wal", "-shm"):  # unfinished writes live here when the database is in WAL mode
            sibling = source_path.with_name(source_path.name + suffix)
            if sibling.is_file():
                shutil.copy2(sibling, Path(workdir) / ("journal.db" + suffix))
        source = sqlite_db.connect(copy)  # brings an older journal up to the current schema; only the copy changes
        try:
            with database.tenant_session(user_id, statement_timeout_ms=HEAVY_STATEMENT_TIMEOUT_MS) as conn:
                provision_defaults(conn)
                with conn.transaction():
                    copy_journal(
                        conn,
                        source,
                        screenshots_dir,
                        files=files,
                        user_id=user_id,
                        write_files=not dry_run,
                        report=report,
                    )
                    report.checks = golden_checks(conn, source, placed=report.placed, files=None if dry_run else files)
                    if dry_run:
                        raise psycopg.Rollback
        except BaseException:
            if files is not None and not dry_run:
                for placed in report.placed:  # the rows are rolled back, so their files must not stay behind
                    files.delete(placed.key)
            raise
        finally:
            source.close()
    return report


def format_report(report: MigrationReport) -> str:
    lines = [("DRY RUN: nothing was written." if report.dry_run else "Copied."), ""]
    lines += [f"  {name:<16}{count:>8}" for name, count in report.copied.items()]
    lines += ["", "Golden check (source on the left, copy on the right):"]
    for check in report.checks:
        mark = "ok  " if check.ok else ("note" if check.informational else "FAIL")
        lines.append(f"  [{mark}] {check.name}")
        if not check.ok:
            lines += [f"           source: {_short(check.source)}", f"           copy:   {_short(check.target)}"]
    if report.missing_screenshot_files:
        lines += [
            "",
            f"{len(report.missing_screenshot_files)} screenshot file(s) named in the journal are missing from the screenshots folder.",
        ]
    if report.store_given:
        verb = "would be taken into" if report.dry_run else "taken into"
        total = sum(p.size for p in report.placed)
        lines += ["", f"{len(report.placed)} screenshot file(s) {verb} the file store ({total:,} bytes)."]
        lines += [f"  not copied: {name}: {reason}" for name, reason in report.files_not_copied]
        if report.files_not_copied:
            lines += ["  (their rows were copied under the old names; an export lists them as missing)"]
    elif report.copied.get("screenshots"):
        lines += [
            "",
            "No file store was given (--files-dir, TJ_FILES_DIR or TJ_S3_*), so only the screenshot rows were copied, not the files.",
        ]
    if report.skipped_settings:
        lines += ["", "Settings with no place in the new schema (not copied): " + ", ".join(report.skipped_settings)]
    lines += ["", "RESULT: " + ("every check passed." if report.ok else "A CHECK FAILED. Do not use this copy.")]
    return "\n".join(lines)


def _short(value: object, limit: int = 300) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="the local journal.db (it is copied, never opened)")
    parser.add_argument(
        "--screenshots", type=Path, help="the screenshots folder (default: a screenshots folder beside the journal)"
    )
    parser.add_argument(
        "--files-dir",
        type=Path,
        default=Path(os.environ["TJ_FILES_DIR"]) if os.environ.get("TJ_FILES_DIR", "").strip() else None,
        help="the folder that holds screenshot files (default: $TJ_FILES_DIR); without it only the rows are copied",
    )
    parser.add_argument("--user-id", type=UUID, required=True, help="the id of the signed-in user to fill")
    parser.add_argument(
        "--database-url", default=os.environ.get("TJ_DATABASE_URL"), help="default: $TJ_DATABASE_URL (the app_rw URL)"
    )
    parser.add_argument("--dry-run", action="store_true", help="do everything, print the report, write nothing")
    args = parser.parse_args(argv)
    if not args.database_url:
        print("set TJ_DATABASE_URL or pass --database-url", file=sys.stderr)
        return 2
    try:
        bucket = BucketSettings.from_env(os.environ)
    except ValueError as problem:  # names the settings, never their values
        print(problem, file=sys.stderr)
        return 2
    if bucket is not None and args.files_dir:
        print("TJ_S3_* and --files-dir (or TJ_FILES_DIR) are two places to keep files: choose one", file=sys.stderr)
        return 2
    store: FileStore | None = (
        LocalFileStore(args.files_dir) if args.files_dir else BucketFileStore(bucket) if bucket is not None else None
    )
    screenshots = args.screenshots or args.source.parent / "screenshots"
    database = Database(args.database_url, max_size=2)
    database.open()
    try:
        report = run(
            database,
            args.user_id,
            args.source,
            screenshots_dir=screenshots if screenshots.is_dir() else None,
            files=store,
            dry_run=args.dry_run,
        )
    except MigrationRefused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 3
    finally:
        database.close()
    print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
