"""Command-line access to the journal database.

    python -m tradejournal accounts add "Apex 07" --firm Apex --size 50000 --balance 50000 --fee 4.12
    python -m tradejournal import path/to/*.csv --account "Apex 07" --dry-run
    python -m tradejournal trades --account "Apex 07" --date 2024-01-05 --fills
"""

from __future__ import annotations

import argparse
import glob
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from . import accounts, config, db, importer, settings, store
from .finance import DEFAULT_FEE_KEY, ZERO, parse_decimal
from .timeutil import from_utc_iso, zone


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    conn = db.connect(args.db or config.db_path())
    try:
        return args.handler(conn, args) or 0
    except (ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tradejournal", description=__doc__.split("\n")[0])
    parser.add_argument("--db", help=f"database file (default: {config.db_path()})")
    commands = parser.add_subparsers(required=True, metavar="command")

    accounts_cmd = commands.add_parser("accounts", help="list or add accounts")
    accounts_cmd.set_defaults(handler=_list_accounts)
    accounts_sub = accounts_cmd.add_subparsers(metavar="action")
    add = accounts_sub.add_parser("add", help="create an account")
    add.add_argument("name")
    add.add_argument("--firm", choices=accounts.FIRMS, default="Other")
    add.add_argument("--type", choices=list(accounts.ACCOUNT_TYPES), default="evaluation", dest="account_type")
    add.add_argument("--size", type=parse_decimal, help="account size, e.g. 50000")
    add.add_argument("--balance", type=parse_decimal, default=ZERO, help="starting balance")
    add.add_argument(
        "--fee", action="append", default=[], metavar="[ROOT=]RATE",
        help="$ per contract round trip; bare RATE is the default for all symbols, repeatable",
    )  # fmt: skip
    add.set_defaults(handler=_add_account)

    fees = commands.add_parser("fees", help="set fee rates for an account and recompute its trades")
    fees.add_argument("account")
    fees.add_argument("rates", nargs="+", metavar="[ROOT=]RATE")
    fees.set_defaults(handler=_set_fees)

    imp = commands.add_parser("import", help="import Tradovate Performance CSV files")
    imp.add_argument("files", nargs="+", help="CSV files (wildcards allowed)")
    imp.add_argument("--account", required=True)
    imp.add_argument("--dry-run", action="store_true", help="preview only; nothing is saved")
    imp.set_defaults(handler=_import)

    history = commands.add_parser("imports", help="import history")
    history.add_argument("--account")
    history.set_defaults(handler=_list_imports)

    undo = commands.add_parser("undo", help="undo an import")
    undo.add_argument("import_id", type=int)
    undo.add_argument("--dry-run", action="store_true")
    undo.set_defaults(handler=_undo)

    trades = commands.add_parser("trades", help="list trades")
    trades.add_argument("--account")
    trades.add_argument("--date", help="trading day, YYYY-MM-DD")
    trades.add_argument("--fills", action="store_true", help="show each trade's fill pairs")
    trades.set_defaults(handler=_list_trades)

    config_cmd = commands.add_parser("settings", help="show or change a setting")
    config_cmd.add_argument("key", nargs="?", choices=sorted(settings.DEFAULTS))
    config_cmd.add_argument("value", nargs="?")
    config_cmd.set_defaults(handler=_settings)
    return parser


def _list_accounts(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    rows = []
    for account in accounts.list_accounts(conn):
        schedule = accounts.fee_schedule(conn, account.id)
        fees = ", ".join(f"{root}={rate}" for root, rate in sorted(schedule.items())) or "none"
        trade_count = conn.execute("SELECT COUNT(*) FROM trades WHERE account_id = ?", (account.id,)).fetchone()[0]
        status = (
            f"{accounts.OUTCOMES[account.outcome]} {account.ended_on or ''}".strip() if account.archived else "active"
        )
        rows.append([
            account.id, account.name, accounts.ACCOUNT_TYPES[account.account_type], account.firm,
            account.account_size or "", account.starting_balance, fees, trade_count, status,
        ])  # fmt: skip
    _print_table(["id", "name", "type", "firm", "size", "start balance", "fees (per RT)", "trades", "status"], rows)


def _add_account(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    with db.transaction(conn):
        account = accounts.create_account(
            conn,
            args.name,
            firm=args.firm,
            account_type=args.account_type,
            account_size=args.size,
            starting_balance=args.balance,
        )
        for root, rate in _parse_rates(args.fee):
            accounts.set_fee(conn, account.id, root, rate)
    print(f"Created account {account.id}: {account.name} ({accounts.ACCOUNT_TYPES[account.account_type]}, {account.firm})")


def _set_fees(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    account = _require_account(conn, args.account)
    with db.transaction(conn):
        for root, rate in _parse_rates(args.rates):
            accounts.set_fee(conn, account.id, root, rate)
        count = store.recalculate_trades(conn, account.id)
    print(f"Fees updated; recomputed {count} trades.")


def _import(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    account = _require_account(conn, args.account)
    paths = [Path(p) for pattern in args.files for p in (sorted(glob.glob(pattern)) or [pattern])]
    files = [(path.name, path.read_bytes()) for path in paths]
    report = importer.import_files(conn, account.id, files, dry_run=args.dry_run)

    heading = "PREVIEW (nothing saved)" if args.dry_run else "IMPORTED"
    print(f"{heading} into {account.name}")
    rows = []
    for f in report.files:
        dupes = f.duplicates + f.duplicates_in_file
        rows.append([
            f.filename, "REJECTED" if f.fatal else (f.import_id or "-"), f.rows_parsed, f.rows_added,
            dupes, sum(f.duplicates_other_accounts.values()), len(f.errors),
            f.trades_created, f.trades_extended,
        ])  # fmt: skip
    _print_table(["file", "import", "rows", "added", "dupes", "in other acct", "errors", "new trades", "extended"], rows)

    for f in report.files:
        if f.fatal:
            print(f"\n{f.filename}: {f.fatal}")
        for issue in f.errors:
            print(f"{f.filename} line {issue.line}: ERROR {issue.message}")
        for issue in f.warnings:
            print(f"{f.filename} line {issue.line}: warning {issue.message}")
        for other, count in f.duplicates_other_accounts.items():
            print(f"{f.filename}: {count} rows already belong to account '{other}' and were skipped")
        if f.previous_imports:
            print(f"{f.filename}: this exact file was already imported (import {', '.join(map(str, f.previous_imports))})")
    return 1 if any(f.fatal for f in report.files) else 0


def _list_imports(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    account_id = _require_account(conn, args.account).id if args.account else None
    tz = zone(settings.get_setting(conn, "timezone"))
    rows = [
        [
            r["id"], r["account_name"], r["filename"],
            from_utc_iso(r["imported_at"]).astimezone(tz).strftime("%Y-%m-%d %H:%M"),
            r["rows_parsed"], r["rows_added"], r["rows_duplicate"], r["rows_error"], r["trades_affected"],
            "undone" if r["undone_at"] else "",
        ]
        for r in importer.list_imports(conn, account_id)
    ]  # fmt: skip
    _print_table(["id", "account", "file", "imported", "rows", "added", "dupes", "errors", "trades", ""], rows)


def _undo(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    report = importer.undo_import(conn, args.import_id, dry_run=args.dry_run)
    prefix = "Would remove" if args.dry_run else "Removed"
    print(
        f"{prefix} {report.rows_removed} fill pairs and {report.trades_removed} trades; "
        f"{report.trades_changed} trades regrouped; {report.rows_kept} pairs kept (also in another import)."
    )
    if report.trades_with_journal_removed:
        print(f"{report.trades_with_journal_removed} of the removed trades had notes, tags or screenshots.")


def _list_trades(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    query = "SELECT trades.*, accounts.name AS account_name FROM trades JOIN accounts ON accounts.id = trades.account_id WHERE 1 = 1"
    params: list = []
    if args.account:
        query += " AND account_id = ?"
        params.append(_require_account(conn, args.account).id)
    if args.date:
        query += " AND trade_date = ?"
        params.append(args.date)
    trades = conn.execute(query + " ORDER BY entry_at, trades.id", params).fetchall()
    tz = zone(settings.get_setting(conn, "timezone"))

    rows = []
    for t in trades:
        rows.append([
            t["id"], t["account_name"], t["trade_date"],
            from_utc_iso(t["entry_at"]).astimezone(tz).strftime("%H:%M:%S"),
            t["symbol"], t["direction"], t["total_qty"], t["max_position"],
            t["avg_entry"], t["avg_exit"], t["points"], t["ticks"], _hold(t["hold_seconds"]),
            t["gross_pnl"], t["fees"], t["net_pnl"], t["fill_pair_count"],
        ])  # fmt: skip
        if args.fills:
            for pair in store.trade_fill_pairs(conn, t["id"]):
                rows.append([
                    "", "", "", pair.entry_at.astimezone(tz).strftime("%H:%M:%S"), "", "  fill pair",
                    pair.qty, "", pair.entry_price, pair.exit_price, "", "", _hold(pair.duration_seconds),
                    pair.pnl, "", "", f"{pair.buy_fill_id}/{pair.sell_fill_id}",
                ])  # fmt: skip
    _print_table(
        ["id", "account", "day", "entry", "symbol", "side", "qty", "max", "avg entry", "avg exit",
         "points", "ticks", "hold", "gross", "fees", "net", "fills"],
        rows,
    )  # fmt: skip
    nets = [Decimal(t["net_pnl"]) for t in trades]
    wins = sum(1 for n in nets if n > 0)
    losses = sum(1 for n in nets if n < 0)
    print(
        f"\n{len(trades)} trades ({wins} won, {losses} lost)   gross "
        f"{sum((Decimal(t['gross_pnl']) for t in trades), ZERO)}   fees "
        f"{sum((Decimal(t['fees']) for t in trades), ZERO)}   net {sum(nets, ZERO)}"
    )


def _settings(conn: sqlite3.Connection, args: argparse.Namespace) -> None:
    if args.key and args.value is not None:
        with db.transaction(conn):
            settings.set_setting(conn, args.key, args.value)
            recomputed = store.recalculate_trades(conn) if args.key.startswith("trading_day") else 0
        print(f"{args.key} = {args.value}" + (f" (recomputed {recomputed} trades)" if recomputed else ""))
        return
    for key in [args.key] if args.key else sorted(settings.DEFAULTS):
        print(f"{key} = {settings.get_setting(conn, key)}")


def _require_account(conn: sqlite3.Connection, name: str) -> accounts.Account:
    account = accounts.find_account(conn, name)
    if account is None:
        raise LookupError(f"no account named {name!r}; create it with: accounts add {name!r}")
    return account


def _parse_rates(specs: Sequence[str]) -> list[tuple[str, Decimal]]:
    rates = []
    for spec in specs:
        root, _, rate = spec.rpartition("=")
        rates.append((root or DEFAULT_FEE_KEY, parse_decimal(rate)))
    return rates


def _hold(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


def _print_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    cells = [[str(c) for c in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    for index, row in enumerate(cells):
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
        if index == 0:
            print("  ".join("-" * width for width in widths))
