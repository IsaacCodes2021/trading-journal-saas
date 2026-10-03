"""Baseline timings for the journal's hot paths, run against the current SQLite code.

    cd backend
    .venv\\Scripts\\python.exe benchmarks\\baseline.py [--trades 10000] [--repeat 5]

The data is synthetic (a seeded random walk in the Tradovate Performance format) and goes into a
throwaway database in the system temp folder. Nothing under data/ is read or written.

This exists to give the Postgres port (M2) a "before" number. The same scenarios are meant to be
re-run against the multi-tenant code, with the targets from docs/design/01-requirements.md:
a 5,000-row import in under 10 s, and the dashboard's data load in under 800 ms at 10,000 trades.
"""

from __future__ import annotations

import argparse
import platform
import random
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradejournal import accounts, db, finance, importer, queries, store  # noqa: E402
from tradejournal.parser import parse_performance_csv  # noqa: E402

HEADER = (
    "symbol,_priceFormat,_priceFormatType,_tickSize,buyFillId,sellFillId,qty,buyPrice,sellPrice,pnl,"
    "boughtTimestamp,soldTimestamp,duration"
)
TICK = Decimal("0.25")
# root -> (point value, weight, max contracts)
INSTRUMENTS = {
    "NQ": (Decimal("20"), 4, 5),
    "MNQ": (Decimal("2"), 3, 10),
    "ES": (Decimal("50"), 2, 3),
    "MES": (Decimal("5"), 1, 10),
}
QUARTER_CODES = ((3, "H"), (6, "M"), (9, "U"), (12, "Z"))


def contract(root: str, day: date) -> str:
    month_code = next((code for month, code in QUARTER_CODES if day.month <= month), "H")
    return f"{root}{month_code}{day.year % 10}"


def money(value: Decimal) -> str:
    return f"$({-value:.2f})" if value < 0 else f"${value:.2f}"


def duration_text(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}min {rest}sec" if minutes else f"{seconds}sec"


def synthetic_rows(
    trades: int, *, start: date, rng: random.Random, first_fill_id: int, per_day: int = 20, scale_in_share: float = 0.1
) -> tuple[list[str], int, date]:
    """CSV data rows for about `trades` trades (a scale-in trade is two rows), oldest first.

    Returns the rows, the next unused fill id and the first weekday after the last one generated.
    """
    roots = list(INSTRUMENTS)
    weights = [INSTRUMENTS[r][1] for r in roots]
    rows: list[str] = []
    fill_id = first_fill_id
    day = start
    made = 0
    while made < trades:
        if day.weekday() >= 5:
            day += timedelta(days=1)
            continue
        clock = datetime.combine(day, datetime.min.time()).replace(hour=6, minute=30)
        price = Decimal(rng.randint(15000, 21000)) + Decimal("0.25") * rng.randint(0, 3)
        for _ in range(min(per_day, trades - made)):
            root = rng.choices(roots, weights)[0]
            point_value, _, max_qty = INSTRUMENTS[root]
            symbol = contract(root, day)
            long_side = rng.random() < 0.5
            scale_in = rng.random() < scale_in_share
            legs = [(rng.randint(1, max_qty), 0, 40 + rng.randint(0, 120))]
            if scale_in:  # a second leg entered later and closed earlier, so the position never goes flat
                legs.append((rng.randint(1, max_qty), 10, 30 + rng.randint(0, 20)))
            start_price = price + TICK * rng.randint(-40, 40)
            for qty, offset, length in legs:
                entry_at = clock + timedelta(seconds=offset)
                exit_at = entry_at + timedelta(seconds=length)
                entry = start_price + TICK * rng.randint(-4, 4)
                exit_ = entry + TICK * rng.randint(-24, 28) * (1 if long_side else -1)
                buy_px, sell_px = (entry, exit_) if long_side else (exit_, entry)
                pnl = ((sell_px - buy_px) * qty * point_value).quantize(Decimal("0.01"))
                first_id, second_id = fill_id, fill_id + 1  # lower id = entry, as in real exports
                fill_id += 2
                buy_id, sell_id = (first_id, second_id) if long_side else (second_id, first_id)
                bought, sold = (entry_at, exit_at) if long_side else (exit_at, entry_at)
                rows.append(
                    f"{symbol},-2,0,{TICK},{buy_id},{sell_id},{qty},{buy_px},{sell_px},{money(pnl)},"
                    f"{bought:%m/%d/%Y %H:%M:%S},{sold:%m/%d/%Y %H:%M:%S},{duration_text(length)}"
                )
            clock += timedelta(seconds=legs[0][2] + rng.randint(20, 400))
            made += 1
        day += timedelta(days=1)
    return rows, fill_id, day


def csv_bytes(rows: list[str]) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n").encode()


def timed(fn: Callable[[], object], repeat: int) -> list[float]:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - started)
    return samples


def describe(samples: list[float]) -> str:
    ordered = sorted(samples)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return f"median {statistics.median(samples) * 1000:8.1f} ms   p95 {p95 * 1000:8.1f} ms   n={len(samples)}"


def fresh_database(directory: Path, name: str):
    conn = db.connect(directory / name)
    with db.transaction(conn):
        account = accounts.create_account(
            conn, "Bench", firm="Apex", account_size=Decimal("50000"), starting_balance=Decimal("50000")
        )
        accounts.set_fee(conn, account.id, "*", Decimal("4.00"))
    return conn, account


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trades", type=int, default=10_000, help="size of the large journal (default 10000)")
    parser.add_argument("--repeat", type=int, default=5, help="repetitions of each read scenario (default 5)")
    parser.add_argument("--seed", type=int, default=20240102)
    args = parser.parse_args()

    rng = random.Random(args.seed)  # noqa: S311  (synthetic data, not security)
    print(f"Python {platform.python_version()} on {platform.system()} {platform.release()}, SQLite via sqlite3")
    print(f"seed={args.seed}  trades={args.trades}  repeat={args.repeat}\n")

    with tempfile.TemporaryDirectory(prefix="tj-bench-") as tmp:
        directory = Path(tmp)

        # 1. One big file into an empty journal: the "I just signed up and uploaded my history" case.
        rows, next_id, next_day = synthetic_rows(5000, start=date(2024, 1, 2), rng=rng, first_fill_id=10_000_000_000)
        data = csv_bytes(rows)
        conn, account = fresh_database(directory, "bulk.db")
        started = time.perf_counter()
        report = importer.import_files(conn, account.id, [("bulk.csv", data)]).files[0]
        elapsed = time.perf_counter() - started
        print(f"IMPORT: one file of {len(rows):,} rows ({len(data) // 1024:,} KB) into an empty journal")
        print(
            f"  {elapsed:7.2f} s   rows added {report.rows_added}, trades created {report.trades_created}"
            f"   (target < 10 s)"
        )

        started = time.perf_counter()
        preview = importer.import_files(conn, account.id, [("bulk.csv", data)], dry_run=True).files[0]
        print(
            f"  {time.perf_counter() - started:7.2f} s   dry-run re-import of the same file "
            f"(duplicates {preview.duplicates})"
        )

        started = time.perf_counter()
        parsed_only = parse_performance_csv(data, "America/Los_Angeles")
        print(f"  {time.perf_counter() - started:7.2f} s   parse only ({len(parsed_only.pairs):,} rows, pandas)")
        conn.close()

        # 2. A large journal, then the everyday operations on it.
        conn, account = fresh_database(directory, "large.db")
        remaining = args.trades
        fill_id, day = 20_000_000_000, date(2024, 1, 2)
        started = time.perf_counter()
        while remaining > 0:
            chunk = min(remaining, 2500)
            rows, fill_id, day = synthetic_rows(chunk, start=day, rng=rng, first_fill_id=fill_id)
            importer.import_files(conn, account.id, [(f"chunk-{remaining}.csv", csv_bytes(rows))])
            remaining -= chunk
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        pairs = conn.execute("SELECT COUNT(*) FROM fill_pairs").fetchone()[0]
        print(
            f"\nLARGE JOURNAL: {total:,} trades from {pairs:,} fill pairs, built in {time.perf_counter() - started:.1f} s"
        )

        rows, fill_id, day = synthetic_rows(20, start=day, rng=rng, first_fill_id=fill_id)
        daily = csv_bytes(rows)
        samples = []
        for number in range(args.repeat):
            rows, fill_id, day = synthetic_rows(20, start=day, rng=rng, first_fill_id=fill_id)
            started = time.perf_counter()
            importer.import_files(conn, account.id, [(f"day-{number}.csv", csv_bytes(rows))])
            samples.append(time.perf_counter() - started)
        print(f"  import of one day (20 trades)    {describe(samples)}")
        print(
            f"  dry-run preview of one day       "
            f"{describe(timed(lambda: importer.import_files(conn, account.id, [('day.csv', daily)], dry_run=True), args.repeat))}"
        )

        everything = queries.TradeFilter()
        print(
            f"  load_trades (all trades)         {describe(timed(lambda: queries.load_trades(conn, everything), args.repeat))}"
        )
        trades = queries.load_trades(conn, everything)

        def dashboard_compute() -> None:
            summary = finance.summarize(trades)
            days = finance.daily_results(trades)
            finance.performance_score(summary, days)
            finance.equity_curve([t.net_pnl for t in trades])

        print(f"  compute only (summary, days...)  {describe(timed(dashboard_compute, args.repeat))}")

        def dashboard_total() -> None:
            accounts.list_accounts(conn)
            loaded = queries.load_trades(conn, everything)
            summary = finance.summarize(loaded)
            days = finance.daily_results(loaded)
            finance.performance_score(summary, days)
            finance.equity_curve([t.net_pnl for t in loaded])

        print(
            f"  DASHBOARD DATA (load + compute)  {describe(timed(dashboard_total, args.repeat))}   (target p95 < 800 ms)"
        )

        year = queries.TradeFilter(start=date(2024, 6, 1), end=date(2024, 6, 30))
        print(
            f"  load_trades, one month           {describe(timed(lambda: queries.load_trades(conn, year), args.repeat))}"
        )

        started = time.perf_counter()
        store.rebuild_account(conn, account.id)
        print(f"  rebuild_account (repair tool)    {time.perf_counter() - started:7.2f} s")

        first_import = conn.execute("SELECT MIN(id) FROM imports").fetchone()[0]
        started = time.perf_counter()
        undo = importer.undo_import(conn, first_import)
        print(
            f"  undo_import (first chunk)        {time.perf_counter() - started:7.2f} s   "
            f"rows removed {undo.rows_removed}, trades removed {undo.trades_removed}"
        )
        conn.close()


if __name__ == "__main__":
    main()
