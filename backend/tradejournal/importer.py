"""Importing Tradovate exports into an account, previewing imports, and undoing them."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from . import store
from .accounts import get_account
from .db import transaction
from .parser import RowIssue, parse_performance_csv
from .settings import get_setting
from .timeutil import to_utc_iso


@dataclass
class FileImportReport:
    filename: str
    import_id: int | None = None  # None for previews and rejected files
    rows_parsed: int = 0  # valid fill-pair rows read from the file
    rows_added: int = 0
    duplicates: int = 0  # already stored in this account
    duplicates_in_file: int = 0
    duplicates_other_accounts: dict[str, int] = field(default_factory=dict)  # stored under another account
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)
    fatal: str | None = None
    trades_created: int = 0
    trades_extended: int = 0  # existing trades that gained fill pairs from this file
    trades_merged: int = 0  # existing trades folded into another trade
    previous_imports: list[int] = field(default_factory=list)  # earlier imports of this exact file

    @property
    def trades_built(self) -> int:
        return self.trades_created + self.trades_extended


@dataclass
class ImportReport:
    account_id: int
    dry_run: bool
    files: list[FileImportReport]


@dataclass
class UndoReport:
    import_id: int
    dry_run: bool
    rows_removed: int = 0
    rows_kept: int = 0  # still contained in another import
    trades_removed: int = 0
    trades_with_journal_removed: int = 0  # removed trades that had notes, tags or screenshots
    trades_changed: int = 0


def import_files(
    conn: sqlite3.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    *,
    dry_run: bool = False,
) -> ImportReport:
    """Import one or more exports into an account, in order, as one atomic operation.

    dry_run runs the complete import and then rolls it back, so the preview always
    matches what a real import would do.
    """
    get_account(conn, account_id)
    tz_name = get_setting(conn, "timezone")
    with transaction(conn, rollback=dry_run):
        reports = [_import_file(conn, account_id, name, data, tz_name) for name, data in files]
    if dry_run:
        for report in reports:
            report.import_id = None
    return ImportReport(account_id=account_id, dry_run=dry_run, files=reports)


def _import_file(
    conn: sqlite3.Connection, account_id: int, filename: str, data: bytes, tz_name: str
) -> FileImportReport:
    parsed = parse_performance_csv(data, tz_name)
    report = FileImportReport(
        filename=filename,
        rows_parsed=len(parsed.pairs),
        duplicates_in_file=parsed.duplicates_in_file,
        errors=parsed.errors,
        warnings=parsed.warnings,
        fatal=parsed.fatal,
    )
    if parsed.fatal:
        return report

    digest = hashlib.sha256(data).hexdigest()
    report.previous_imports = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM imports WHERE account_id = ? AND file_sha256 = ? AND undone_at IS NULL",
            (account_id, digest),
        )
    ]
    import_id = conn.execute(
        "INSERT INTO imports (account_id, filename, file_sha256, source_timezone, imported_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (account_id, filename, digest, tz_name, to_utc_iso(datetime.now(timezone.utc))),
    ).lastrowid
    report.import_id = import_id

    new_by_symbol: dict[str, list[int]] = defaultdict(list)
    other_accounts: Counter[str] = Counter()
    for pair in parsed.pairs:
        existing = conn.execute(
            "SELECT fill_pairs.id, account_id, accounts.name AS account_name FROM fill_pairs "
            "JOIN accounts ON accounts.id = fill_pairs.account_id "
            "WHERE buy_fill_id = ? AND sell_fill_id = ?",
            (pair.buy_fill_id, pair.sell_fill_id),
        ).fetchone()
        if existing is None:
            pair_id = store.insert_fill_pair(conn, account_id, pair)
            new_by_symbol[pair.symbol].append(pair_id)
            report.rows_added += 1
        elif existing["account_id"] == account_id:
            pair_id = existing["id"]
            report.duplicates += 1
        else:
            other_accounts[existing["account_name"]] += 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO import_fill_pairs (import_id, fill_pair_id) VALUES (?, ?)",
            (import_id, pair_id),
        )
    report.duplicates_other_accounts = dict(other_accounts)

    rebuilt = store.RebuildResult()
    for symbol, pair_ids in new_by_symbol.items():
        rebuilt.absorb(store.rebuild_trades(conn, account_id, symbol, pair_ids))
    report.trades_created = len(rebuilt.created)
    report.trades_extended = len(rebuilt.updated)
    report.trades_merged = len(rebuilt.removed)

    conn.execute(
        "UPDATE imports SET rows_parsed = ?, rows_added = ?, rows_duplicate = ?, rows_error = ?, "
        "trades_affected = ? WHERE id = ?",
        (
            report.rows_parsed,
            report.rows_added,
            report.duplicates + report.duplicates_in_file + sum(other_accounts.values()),
            len(report.errors),
            report.trades_built,
            import_id,
        ),
    )
    return report


def undo_import(conn: sqlite3.Connection, import_id: int, *, dry_run: bool = False) -> UndoReport:
    """Remove the fill pairs an import brought in and regroup what is left.

    A fill pair that another (not undone) import also contained stays. Trades that
    lose all their fill pairs are deleted along with their journal entries; the
    report counts how many had any.
    """
    report = UndoReport(import_id=import_id, dry_run=dry_run)
    with transaction(conn, rollback=dry_run):
        record = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
        if record is None:
            raise LookupError(f"no import with id {import_id}")
        if record["undone_at"] is not None:
            raise ValueError(f"import {import_id} was already undone")
        account_id = record["account_id"]

        linked = [
            row["fill_pair_id"]
            for row in conn.execute(
                "SELECT fill_pair_id FROM import_fill_pairs WHERE import_id = ?", (import_id,)
            )
        ]
        conn.execute("DELETE FROM import_fill_pairs WHERE import_id = ?", (import_id,))

        affected: dict[int, str] = {}  # trade id -> symbol
        for pair_id in linked:
            still_linked = conn.execute(
                "SELECT 1 FROM import_fill_pairs WHERE fill_pair_id = ? LIMIT 1", (pair_id,)
            ).fetchone()
            if still_linked:
                report.rows_kept += 1
                continue
            row = conn.execute("SELECT trade_id, symbol FROM fill_pairs WHERE id = ?", (pair_id,)).fetchone()
            if row["trade_id"] is not None:
                affected[row["trade_id"]] = row["symbol"]
            conn.execute("DELETE FROM fill_pairs WHERE id = ?", (pair_id,))
            report.rows_removed += 1

        remaining_by_symbol: dict[str, list[int]] = defaultdict(list)
        changed: set[int] = set()  # trades that lost some, but not all, of their fill pairs
        for trade_id, symbol in affected.items():
            remaining = [
                row["id"]
                for row in conn.execute("SELECT id FROM fill_pairs WHERE trade_id = ?", (trade_id,))
            ]
            if remaining:
                remaining_by_symbol[symbol].extend(remaining)
                changed.add(trade_id)
                continue
            if store.has_journal(conn, trade_id):
                report.trades_with_journal_removed += 1
            conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
            report.trades_removed += 1

        for symbol, pair_ids in remaining_by_symbol.items():
            rebuilt = store.rebuild_trades(conn, account_id, symbol, pair_ids)
            changed |= rebuilt.created | rebuilt.updated
            report.trades_removed += len(rebuilt.removed)
        report.trades_changed = len(changed)

        conn.execute(
            "UPDATE imports SET undone_at = ? WHERE id = ?",
            (to_utc_iso(datetime.now(timezone.utc)), import_id),
        )
    return report


def list_imports(conn: sqlite3.Connection, account_id: int | None = None) -> list[sqlite3.Row]:
    query = (
        "SELECT imports.*, accounts.name AS account_name FROM imports "
        "JOIN accounts ON accounts.id = imports.account_id"
    )
    params: tuple = ()
    if account_id is not None:
        query += " WHERE imports.account_id = ?"
        params = (account_id,)
    return conn.execute(query + " ORDER BY imports.id DESC", params).fetchall()
