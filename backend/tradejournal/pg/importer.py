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
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from .. import finance
from ..importer import FileImportReport, ImportReport, LogAccount, RootCommission, UndoReport
from ..models import Execution, FillPair
from ..parser import parse_performance_csv
from ..platforms import other, round_turn_monitor, tradesea
from ..platforms.detect import OTHER, PLATFORMS, ROUND_TURN_MONITOR, TRADESEA, TRADOVATE
from ..symbols import parse_symbol
from . import executions, store, usage
from .accounts import get_account
from .settings import effective_csv_timezone

# Platforms whose rows are stored as orders (`executions`), and the source each one's orders and imports are stored under.
ORDER_SOURCES = {TRADESEA: tradesea.SOURCE, ROUND_TURN_MONITOR: round_turn_monitor.SOURCE, OTHER: other.SOURCE}
# How many stored orders one row of a file is: a round turn (the Round Turn Monitor's, or one of Roundturn's own import
# file) is its entry and its exit. Counts shown to the trader (rows read, added, already there, removed by an undo) are
# rows of the file.
ORDERS_PER_ROW = {round_turn_monitor.SOURCE: 2, other.SOURCE: 2}

CHOOSE_ACCOUNT = "This log has round turns from {count} accounts. Choose which one to import into this account."
NOT_IN_LOG = "This log has no round turns from the account {name}. Choose one of the accounts it has."


def import_files(
    conn: psycopg.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    *,
    platform: str = TRADOVATE,
    dry_run: bool = False,
    log_account: str | None = None,
) -> ImportReport:
    """Import one or more exports into an account, in order, as one atomic operation.

    Tradovate's files are one import each. TradeSea's are the pages of one day (its export saves one page of 10 orders),
    so they are read together as **one** import, with one report. So are the Round Turn Monitor's (its log and the
    overflow file), of which `log_account` picks the account to read (None: the one `default_log_account` picks).
    Roundturn's own import file (`OTHER`) is one file, read as round turns like the Round Turn Monitor's.
    """
    if platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    store.lock_user(conn)
    get_account(conn, account_id)
    tz_name = effective_csv_timezone(conn)  # the CSV zone if one is chosen, else the person's own timezone
    with conn.transaction():  # a savepoint inside the tenant session's transaction
        if platform == TRADOVATE:
            reports = [_import_file(conn, account_id, name, data, tz_name) for name, data in files]
        else:
            reports = [_import_orders(conn, account_id, files, tz_name, platform, log_account)]
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
        time_zone=tz_name,
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
    keys = [_key(pair) for pair in parsed.pairs]
    existing = existing_pairs(conn, parsed.pairs)
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


def _import_orders(
    conn: psycopg.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    tz_name: str,
    platform: str,
    log_account: str | None,
) -> FileImportReport:
    """Read the files of a platform whose rows are orders, and import them."""
    if platform == TRADESEA:
        return _import_order_files(
            conn, account_id, files, tz_name, tradesea.read_order_files(files, tz_name), TRADESEA
        )
    if platform == OTHER:
        return _import_other_file(conn, account_id, files, tz_name)
    return _import_log_files(conn, account_id, files, tz_name, log_account)


def _import_other_file(
    conn: psycopg.Connection, account_id: int, files: Sequence[tuple[str, bytes]], tz_name: str
) -> FileImportReport:
    """Roundturn's own import file (Other platform): its round turns as orders, with the file's own totals, which the
    trader checks against their platform's statement."""
    read = other.read_files(files)
    if read.fatal:
        return FileImportReport(filename=_names(files), platform=OTHER, fatal=read.fatal, time_zone=tz_name)
    report = _import_order_files(conn, account_id, files, tz_name, read.as_orders(), OTHER)
    report.file_gross, report.file_fees, report.file_net = read.gross, read.fees, read.net
    report.first_day, report.last_day = read.first_day, read.last_day
    return report


def _import_log_files(
    conn: psycopg.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    tz_name: str,
    log_account: str | None,
) -> FileImportReport:
    """The Round Turn Monitor: one account's round turns from a log that holds every account's, as orders."""
    log = round_turn_monitor.read_log_files(files, tz_name)
    if log.fatal:
        return FileImportReport(filename=_names(files), platform=ROUND_TURN_MONITOR, fatal=log.fatal, time_zone=tz_name)
    accounts = log_accounts(conn, account_id, log)
    chosen = default_log_account(accounts) if log_account is None else log_account
    if accounts and chosen not in {account.name for account in accounts}:
        fatal = CHOOSE_ACCOUNT.format(count=len(accounts)) if chosen is None else NOT_IN_LOG.format(name=chosen)
        return FileImportReport(
            filename=_names(files),
            platform=ROUND_TURN_MONITOR,
            fatal=fatal,
            time_zone=tz_name,
            log_accounts=accounts,
        )
    read = log.for_account(chosen or "")
    # Which account was read is part of what was imported: the same log for another account is another import.
    digest = hashlib.sha256(f"{_digest(files)}|{chosen or ''}".encode()).hexdigest()
    report = _import_order_files(conn, account_id, files, tz_name, read, ROUND_TURN_MONITOR, digest)
    report.log_accounts = accounts
    report.log_account = chosen
    return report


def log_accounts(conn: psycopg.Connection, account_id: int, log: round_turn_monitor.LogRead) -> list[LogAccount]:
    """The log's accounts, real ones first, each with how many of its round turns this account (or another one)
    already holds. One query for the whole log."""
    owner = {turn.entry.ref: turn.account for turn in log.round_turns}
    here: Counter[str] = Counter()
    elsewhere: dict[str, Counter[str]] = defaultdict(Counter)
    for row in conn.execute(
        """SELECT executions.ref, executions.account_id, accounts.name AS account_name
           FROM executions JOIN accounts ON accounts.id = executions.account_id
           WHERE executions.source = %s AND executions.ref = ANY(%s::text[])""",
        (round_turn_monitor.SOURCE, list(owner)),
    ):
        name = owner[row["ref"]]
        if row["account_id"] == account_id:
            here[name] += 1
        else:
            elsewhere[name][row["account_name"]] += 1
    accounts = [
        LogAccount(
            name=name,
            round_turns=count,
            practice=round_turn_monitor.is_practice(name),
            here=here[name],
            elsewhere=elsewhere[name].most_common(1)[0][0] if elsewhere[name] else None,
        )
        for name, count in log.accounts().items()
    ]
    return sorted(accounts, key=lambda account: account.practice)  # stable: the files' order within each kind


def default_log_account(accounts: Sequence[LogAccount]) -> str | None:
    """The account to read when the trader has not chosen one, or None when they must.

    The one this account already holds round turns of (so the choice is remembered, with nothing stored but the round
    turns); else the only real account no other account holds yet; else the only real account; else, for a log of
    one practice account, that one. A practice account is never picked over a real one."""
    held = [account for account in accounts if account.here]
    if held:
        return max(held, key=lambda account: account.here).name
    real = [account for account in accounts if not account.practice and account.round_turns]
    fresh = [account for account in real if account.elsewhere is None]
    if len(fresh) == 1:
        return fresh[0].name
    if len(real) == 1:
        return real[0].name
    if not real and len(accounts) == 1:
        return accounts[0].name
    return None


def _import_order_files(
    conn: psycopg.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    tz_name: str,
    read: tradesea.ReadResult,
    platform: str,
    digest: str | None = None,
) -> FileImportReport:
    """Store the orders read from the files, then work the account's fill pairs and trades out again from every stored
    order of the symbols in the files (`executions.py`)."""
    source = ORDER_SOURCES[platform]
    per_row = ORDERS_PER_ROW.get(source, 1)
    report = FileImportReport(
        filename=_names(files),
        platform=platform,
        rows_parsed=len(read.executions) // per_row,
        duplicates_in_file=read.duplicates_across_files,
        errors=read.errors,
        warnings=read.warnings,
        fatal=read.fatal,
        skipped_by_status=dict(read.skipped),
        file_commission=read.commission if any(e.commission is not None for e in read.executions) else None,
        commissions=_commissions(read.executions, read.rates),
    )
    if read.fatal:
        return report

    digest = digest or _digest(files)
    report.previous_imports = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM imports WHERE account_id = %s AND file_sha256 = %s AND undone_at IS NULL ORDER BY id",
            (account_id, digest),
        )
    ]
    # The zone the times were read in: the file's own ("PST"), and the setting's when some had none Roundturn reads.
    zones = sorted(read.zones) + ([tz_name] if read.fell_back or not read.zones else [])
    report.time_zone = ", ".join(zones)
    import_id = conn.execute(
        """INSERT INTO imports (account_id, source, filename, file_sha256, source_timezone, imported_at)
           VALUES (%s, %s, %s, %s, %s, clock_timestamp()) RETURNING id""",
        (account_id, source, report.filename, digest, report.time_zone),
    ).fetchone()["id"]
    report.import_id = import_id

    stored = executions.store_executions(conn, account_id, source, read.executions)
    executions.link_to_import(conn, import_id, stored.linked_ids)
    report.rows_added = len(stored.added_ids) // per_row
    report.duplicates = len(stored.duplicate_ids) // per_row
    report.matches_other_accounts = {name: n // per_row for name, n in stored.other_accounts.items()}

    rederived = executions.rederive(conn, account_id, source, {e.symbol for e in read.executions})
    report.trades_created = len(rederived.trades.created)
    report.trades_extended = len(rederived.trades.updated)
    report.trades_merged = len(rederived.trades.removed | rederived.journal_moved)
    report.trades_removed = len(rederived.deleted)
    report.open_positions = rederived.open_positions

    conn.execute(
        "UPDATE imports SET rows_parsed = %s, rows_added = %s, rows_duplicate = %s, rows_error = %s, "
        "trades_affected = %s WHERE id = %s",
        (
            report.rows_parsed,
            report.rows_added,
            report.duplicates + report.duplicates_in_file,
            len(report.errors),
            report.trades_built,
            import_id,
        ),
    )
    return report


def _commissions(orders: Sequence[Execution], rates: dict[str, Decimal]) -> list[RootCommission]:
    """The files' own commission by contract root, for the orders that state one."""
    sides: Counter[str] = Counter()
    totals: dict[str, Decimal] = defaultdict(lambda: finance.ZERO)
    for order in orders:
        if order.commission is not None:
            root, _ = parse_symbol(order.symbol, order.executed_at.date())  # "NQZ6" -> "NQ"; TradeSea's "NQ" as it is
            sides[root] += order.qty
            totals[root] += order.commission
    return [RootCommission(root, sides[root], totals[root], rates.get(root)) for root in sorted(sides)]


def preview_orders(
    conn: psycopg.Connection,
    account_id: int,
    files: Sequence[tuple[str, bytes]],
    limit: int,
    *,
    platform: str = TRADESEA,
    log_account: str | None = None,
) -> tuple[FileImportReport, list[RowPreview]]:
    """TradeSea or the Round Turn Monitor: what importing these files would do, and the first `limit` fill pairs their
    orders would be part of, each with its status. The import itself runs and is rolled back, as
    `import_files(dry_run=True)` does, so the preview is what the import would do."""
    source = ORDER_SOURCES[platform]
    store.lock_user(conn)
    get_account(conn, account_id)
    tz_name = effective_csv_timezone(conn)
    rows: list[RowPreview] = []
    with conn.transaction():
        # Fill pair ids only grow, so a pair with a higher id than any stored now is one this import would make.
        last = conn.execute("SELECT coalesce(max(id), 0) AS id FROM fill_pairs").fetchone()["id"]
        report = _import_orders(conn, account_id, files, tz_name, platform, log_account)
        if not report.fatal:
            refs = [
                str(row["execution_id"])
                for row in conn.execute(
                    "SELECT execution_id FROM import_executions WHERE import_id = %s", (report.import_id,)
                )
            ]
            for row in conn.execute(
                """SELECT * FROM fill_pairs
                   WHERE account_id = %s AND source = %s AND (buy_ref = ANY(%s::text[]) OR sell_ref = ANY(%s::text[]))
                   ORDER BY least(bought_at, sold_at), id LIMIT %s""",
                (account_id, source, refs, refs, limit),
            ):
                status = PAIR_NEW if row["id"] > last else PAIR_DUPLICATE
                rows.append(RowPreview(store.pair_from_row(row), status, None))
        raise psycopg.Rollback
    report.import_id = None
    return report, rows


def _names(files: Sequence[tuple[str, bytes]]) -> str:
    return ", ".join(name for name, _ in files)[:500]


def _digest(files: Sequence[tuple[str, bytes]]) -> str:
    """One file's SHA-256, as for Tradovate; for several pages, the SHA-256 of their sorted SHA-256s, so the same pages
    picked in any order give the same fingerprint."""
    digests = sorted(hashlib.sha256(data).hexdigest() for _, data in files)
    if len(digests) == 1:
        return digests[0]
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()


def _key(pair: FillPair) -> tuple[str, str]:
    return (str(pair.buy_fill_id), str(pair.sell_fill_id))


def existing_pairs(conn: psycopg.Connection, pairs: Sequence[FillPair]) -> dict[tuple[str, str], dict]:
    """Which of these fill pairs does the user already have, and under which account? One query for any number of pairs,
    keyed by (buy fill, sell fill). Shared by the import itself and the preview, so they can never disagree."""
    keys = [_key(pair) for pair in pairs]
    return {
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


PAIR_NEW = "new"
PAIR_DUPLICATE = "duplicate"  # already stored in this account
PAIR_OTHER_ACCOUNT = "other_account"  # already stored under another account: left alone


@dataclass(frozen=True)
class RowPreview:
    """One fill pair of a file and what importing it into an account would do with it."""

    pair: FillPair
    status: str
    account_name: str | None  # for PAIR_OTHER_ACCOUNT: the account that has it


def preview_rows(conn: psycopg.Connection, account_id: int, data: bytes, limit: int) -> tuple[list[RowPreview], str]:
    """The first `limit` fill pairs of a file with their status, and the timezone the file was read in. Changes nothing."""
    get_account(conn, account_id)
    tz_name = effective_csv_timezone(conn)
    parsed = parse_performance_csv(data, tz_name)
    if parsed.fatal:
        return [], tz_name
    shown = parsed.pairs[:limit]
    existing = existing_pairs(conn, shown)
    rows = []
    for pair in shown:
        found = existing.get(_key(pair))
        if found is None:
            rows.append(RowPreview(pair, PAIR_NEW, None))
        elif found["account_id"] == account_id:
            rows.append(RowPreview(pair, PAIR_DUPLICATE, None))
        else:
            rows.append(RowPreview(pair, PAIR_OTHER_ACCOUNT, found["account_name"]))
    return rows, tz_name


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
        if record["source"] == store.SOURCE:
            _undo_fill_pairs(conn, record, report)
        else:
            _undo_orders(conn, record, report)
        conn.execute("UPDATE imports SET undone_at = clock_timestamp() WHERE id = %s", (import_id,))
        usage.refresh(conn)
        if dry_run:
            raise psycopg.Rollback
    return report


def _undo_orders(conn: psycopg.Connection, record: dict, report: UndoReport) -> None:
    """Remove the orders only this import holds, then work the fill pairs and trades out again (`executions.py`)."""
    linked = [
        row["execution_id"]
        for row in conn.execute(
            "DELETE FROM import_executions WHERE import_id = %s RETURNING execution_id", (record["id"],)
        )
    ]
    kept = {
        row["execution_id"]
        for row in conn.execute(
            "SELECT DISTINCT execution_id FROM import_executions WHERE execution_id = ANY(%s::bigint[])", (linked,)
        )
    }
    per_row = ORDERS_PER_ROW.get(record["source"], 1)
    report.rows_kept = len(kept) // per_row
    orphans = [execution_id for execution_id in linked if execution_id not in kept]
    symbols = {
        row["symbol"]
        for row in conn.execute("DELETE FROM executions WHERE id = ANY(%s::bigint[]) RETURNING symbol", (orphans,))
    }
    report.rows_removed = len(orphans) // per_row
    rederived = executions.rederive(conn, record["account_id"], record["source"], symbols)
    report.trades_removed = len(rederived.removed)
    report.trades_with_journal_removed = rederived.journal_lost
    report.trades_changed = len(rederived.trades.created | rederived.trades.updated)


def _undo_fill_pairs(conn: psycopg.Connection, record: dict, report: UndoReport) -> None:
    """Tradovate: remove the fill pairs only this import holds, and regroup what is left."""
    import_id, account_id = record["id"], record["account_id"]
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


def list_imports(conn: psycopg.Connection, account_id: int | None = None) -> list[dict]:
    """Imports newest first, each with its account's name."""
    select = (
        "SELECT imports.*, accounts.name AS account_name, accounts.firm AS account_firm, "
        "accounts.account_type AS account_type FROM imports JOIN accounts ON accounts.id = imports.account_id"
    )
    if account_id is None:
        return conn.execute(select + " ORDER BY imports.id DESC").fetchall()
    return conn.execute(select + " WHERE imports.account_id = %s ORDER BY imports.id DESC", (account_id,)).fetchall()
