"""Importing Tradovate exports into an account, previewing imports, and undoing them, on Postgres.

The port of ``tradejournal/importer.py`` (milestone M2). The report records are shared with the SQLite module. Two
changes from the original, both from the benchmark and the SQL audit (doc 05 section 7, doc 03 section 9.1):

* **A fixed number of statements per file**, not three per row. The SQLite importer looked each row up, inserted it and
  linked it to the import one at a time; over a network that is thousands of round trips for one file. Here a file is
  looked up with one query, stored with one batch, and linked with one statement.
* **The user's lock** (``store.lock_user``) is taken first, so two imports for the same user queue instead of racing.

Every function takes a connection from ``Database.tenant_session`` (one transaction). ``dry_run`` does the whole
operation inside a savepoint and rolls it back, so a preview always matches what the real thing would do.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Sequence

import psycopg

from ..importer import FileImportReport, ImportReport, UndoReport
from ..parser import parse_performance_csv
from . import store, usage
from .accounts import get_account
from .settings import get_setting


def import_files(
    conn: psycopg.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    *,
    dry_run: bool = False,
) -> ImportReport:
    """Import one or more exports into an account, in order, as one atomic operation."""
    store.lock_user(conn)
    get_account(conn, account_id)
    tz_name = get_setting(conn, "timezone")
    with conn.transaction():  # a savepoint inside the tenant session's transaction
        reports = [_import_file(conn, account_id, name, data, tz_name) for name, data in files]
        usage.refresh(conn)
        if dry_run:
            raise psycopg.Rollback
    if dry_run:
        for report in reports:
            report.import_id = None
    return ImportReport(account_id=account_id, dry_run=dry_run, files=reports)


def _import_file(
    conn: psycopg.Connection, account_id: int, filename: str, data: bytes, tz_name: str
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
            "SELECT id FROM imports WHERE account_id = %s AND file_sha256 = %s AND undone_at IS NULL ORDER BY id",
            (account_id, digest),
        )
    ]
    import_id = conn.execute(
        """INSERT INTO imports (account_id, filename, file_sha256, source_timezone, imported_at)
           VALUES (%s, %s, %s, %s, clock_timestamp()) RETURNING id""",
        (account_id, filename, digest, tz_name),
    ).fetchone()["id"]
    report.import_id = import_id

    # One lookup for the whole file: which of its rows does this user already have, and in which account?
    keys = [(str(pair.buy_fill_id), str(pair.sell_fill_id)) for pair in parsed.pairs]
    existing = {
        (row["buy_ref"], row["sell_ref"]): row
        for row in conn.execute(
            """SELECT fill_pairs.id, fill_pairs.account_id, fill_pairs.buy_ref, fill_pairs.sell_ref,
                      accounts.name AS account_name
               FROM fill_pairs JOIN accounts ON accounts.id = fill_pairs.account_id
               WHERE fill_pairs.source = %s
                 AND (fill_pairs.buy_ref, fill_pairs.sell_ref) IN (SELECT * FROM unnest(%s::text[], %s::text[]))""",
            (store.SOURCE, [buy for buy, _ in keys], [sell for _, sell in keys]),
        )
    }
    new_pairs = []
    linked_ids: list[int] = []  # existing pairs in this account, plus the ones inserted below
    other_accounts: Counter[str] = Counter()
    for pair, key in zip(parsed.pairs, keys, strict=True):
        found = existing.get(key)
        if found is None:
            new_pairs.append(pair)
        elif found["account_id"] == account_id:
            linked_ids.append(found["id"])
            report.duplicates += 1
        else:
            other_accounts[found["account_name"]] += 1
    report.rows_added = len(new_pairs)
    report.duplicates_other_accounts = dict(other_accounts)

    new_ids = store.insert_fill_pairs(conn, account_id, new_pairs)
    new_by_symbol: dict[str, list[int]] = defaultdict(list)
    for pair, pair_id in zip(new_pairs, new_ids, strict=True):
        new_by_symbol[pair.symbol].append(pair_id)
    linked_ids.extend(new_ids)
    conn.execute(
        "INSERT INTO import_fill_pairs (import_id, fill_pair_id) SELECT %s, unnest(%s::bigint[]) ON CONFLICT DO NOTHING",
        (import_id, linked_ids),
    )

    rebuilt = store.RebuildResult()
    for symbol, pair_ids in new_by_symbol.items():
        rebuilt.absorb(store.rebuild_trades(conn, account_id, symbol, pair_ids))
    report.trades_created = len(rebuilt.created)
    report.trades_extended = len(rebuilt.updated)
    report.trades_merged = len(rebuilt.removed)

    conn.execute(
        "UPDATE imports SET rows_parsed = %s, rows_added = %s, rows_duplicate = %s, rows_error = %s, "
        "trades_affected = %s WHERE id = %s",
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


def undo_import(conn: psycopg.Connection, import_id: int, *, dry_run: bool = False) -> UndoReport:
    """Remove the fill pairs an import brought in and regroup what is left.

    A fill pair that another (not undone) import also contained stays. Trades that
    lose all their fill pairs are deleted along with their journal entries; the
    report counts how many had any.
    """
    store.lock_user(conn)
    report = UndoReport(import_id=import_id, dry_run=dry_run)
    with conn.transaction():
        record = conn.execute("SELECT * FROM imports WHERE id = %s", (import_id,)).fetchone()
        if record is None:
            raise LookupError(f"no import with id {import_id}")
        if record["undone_at"] is not None:
            raise ValueError(f"import {import_id} was already undone")
        account_id = record["account_id"]

        linked = [
            row["fill_pair_id"]
            for row in conn.execute(
                "DELETE FROM import_fill_pairs WHERE import_id = %s RETURNING fill_pair_id", (import_id,)
            )
        ]
        kept = {
            row["fill_pair_id"]
            for row in conn.execute(
                "SELECT DISTINCT fill_pair_id FROM import_fill_pairs WHERE fill_pair_id = ANY(%s::bigint[])", (linked,)
            )
        }
        report.rows_kept = len(kept)
        orphans = [pair_id for pair_id in linked if pair_id not in kept]
        affected: dict[int, str] = {  # trade id -> symbol
            row["trade_id"]: row["symbol"]
            for row in conn.execute(
                "DELETE FROM fill_pairs WHERE id = ANY(%s::bigint[]) RETURNING trade_id, symbol", (orphans,)
            )
            if row["trade_id"] is not None
        }
        report.rows_removed = len(orphans)

        remaining: dict[int, list[int]] = defaultdict(list)  # trade id -> its surviving fill pair ids
        for row in conn.execute(
            "SELECT id, trade_id FROM fill_pairs WHERE trade_id = ANY(%s::bigint[])", (list(affected),)
        ):
            remaining[row["trade_id"]].append(row["id"])
        emptied = [trade_id for trade_id in affected if trade_id not in remaining]
        report.trades_with_journal_removed = len(store.journal_trade_ids(conn, emptied))
        conn.execute("DELETE FROM trades WHERE id = ANY(%s::bigint[])", (emptied,))
        report.trades_removed = len(emptied)

        remaining_by_symbol: dict[str, list[int]] = defaultdict(list)
        for trade_id, pair_ids in remaining.items():
            remaining_by_symbol[affected[trade_id]].extend(pair_ids)
        changed: set[int] = set(remaining)  # trades that lost some, but not all, of their fill pairs
        for symbol, pair_ids in remaining_by_symbol.items():
            rebuilt = store.rebuild_trades(conn, account_id, symbol, pair_ids)
            changed |= rebuilt.created | rebuilt.updated
            report.trades_removed += len(rebuilt.removed)
        report.trades_changed = len(changed)

        conn.execute("UPDATE imports SET undone_at = clock_timestamp() WHERE id = %s", (import_id,))
        usage.refresh(conn)
        if dry_run:
            raise psycopg.Rollback
    return report


def list_imports(conn: psycopg.Connection, account_id: int | None = None) -> list[dict]:
    """Imports newest first, each with its account's name."""
    select = (
        "SELECT imports.*, accounts.name AS account_name FROM imports JOIN accounts ON accounts.id = imports.account_id"
    )
    if account_id is None:
        return conn.execute(select + " ORDER BY imports.id DESC").fetchall()
    return conn.execute(select + " WHERE imports.account_id = %s ORDER BY imports.id DESC", (account_id,)).fetchall()
