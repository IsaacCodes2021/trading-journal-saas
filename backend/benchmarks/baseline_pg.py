"""The baseline scenarios, re-run against the Postgres code: the "after" to baseline.py's "before" (doc 05 section 7).

    cd backend
    .venv\\Scripts\\python.exe benchmarks\\baseline_pg.py [--trades 10000] [--repeat 5] [--latency-ms 1.0]

It uses the same seeded synthetic data and the same scenarios as baseline.py, through the ``tradejournal.pg`` modules,
as one user in the test database (``TJ_TEST_OWNER_URL`` and ``TJ_TEST_APP_URL``, from backend/.env.test or the
environment; see ``python -m tradejournal.pg.provision``). **It empties that database first**, as the Postgres tests do.

A local database answers in a fraction of a millisecond, so on its own it flatters code that makes many statements.
``--latency-ms N`` sleeps N milliseconds before every statement (one batched ``executemany`` counts once), which models
a managed database a network hop away; the doc 05 inference was that 0.5 to 1 ms per statement would push the SQLite
importer's per-row statements past the 10 s target. Statement counts are printed so the effect can be read directly.
"""

from __future__ import annotations

import argparse
import os
import platform
import random
import sys
import time
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from baseline import csv_bytes, describe, synthetic_rows, timed  # noqa: E402

from tradejournal import finance  # noqa: E402
from tradejournal.parser import parse_performance_csv  # noqa: E402
from tradejournal.pg import accounts, importer, migrate, queries, store  # noqa: E402
from tradejournal.pg.defaults import provision_defaults  # noqa: E402
from tradejournal.pg.provision import read_env_file  # noqa: E402
from tradejournal.pg.session import Database  # noqa: E402

ENV_FILE = Path(__file__).resolve().parent.parent / ".env.test"


class Meter:
    """Counts statements and adds the modelled network latency to each."""

    def __init__(self, latency_seconds: float) -> None:
        self.latency = latency_seconds
        self.statements = 0

    def cursor_class(self) -> type:
        meter = self

        class Metered(psycopg.Cursor):
            def execute(self, query, params=None, **kwargs):
                meter.charge()
                return super().execute(query, params, **kwargs)

            def executemany(self, query, params_seq, **kwargs):
                meter.charge()
                return super().executemany(query, params_seq, **kwargs)

        return Metered

    def charge(self) -> None:
        self.statements += 1
        if self.latency:
            time.sleep(self.latency)


def connection_urls() -> tuple[str, str]:
    env = {**read_env_file(ENV_FILE), **os.environ}
    owner, app = env.get("TJ_TEST_OWNER_URL"), env.get("TJ_TEST_APP_URL")
    if not owner or not app:
        sys.exit("Set TJ_TEST_OWNER_URL and TJ_TEST_APP_URL, or run: python -m tradejournal.pg.provision (see --help)")
    return owner, app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--trades", type=int, default=10_000, help="size of the large journal (default 10000)")
    parser.add_argument("--repeat", type=int, default=5, help="repetitions of each read scenario (default 5)")
    parser.add_argument("--seed", type=int, default=20240102)
    parser.add_argument("--latency-ms", type=float, default=0.0, help="modelled round trip per statement (default 0)")
    parser.add_argument(
        "--no-analyze",
        action="store_true",
        help="skip the ANALYZE after the large journal is built, to see the cost of stale statistics",
    )
    args = parser.parse_args()

    owner_url, app_url = connection_urls()
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as owner:
        owner.execute("DROP SCHEMA public CASCADE")
        owner.execute("CREATE SCHEMA public")
        migrate.migrate(owner_url)
        version = owner.execute("SHOW server_version").fetchone()["server_version"]
        user_id = owner.execute(
            "INSERT INTO users (idp_subject, email) VALUES ('bench', 'bench@example.test') RETURNING id"
        ).fetchone()["id"]

    meter = Meter(args.latency_ms / 1000)
    db = Database(app_url, max_size=2)
    db.open()

    @contextmanager
    def session():
        """One request's worth of work: its own tenant session, with the meter attached."""
        with db.tenant_session(user_id) as conn:
            conn.cursor_factory = meter.cursor_class()
            yield conn

    with session() as conn:
        provision_defaults(conn)
        account = accounts.create_account(
            conn, "Bench", firm="Apex", account_size=Decimal("50000"), starting_balance=Decimal("50000")
        )
        accounts.set_fee(conn, account.id, "*", Decimal("4.00"))

    rng = random.Random(args.seed)  # noqa: S311  (synthetic data, not security)
    print(f"Python {platform.python_version()} on {platform.system()} {platform.release()}, PostgreSQL {version}")
    print(f"seed={args.seed}  trades={args.trades}  repeat={args.repeat}  modelled latency={args.latency_ms} ms\n")

    def run(action):
        """Run `action(conn)` in its own session; return (seconds, statements, result)."""
        before = meter.statements
        started = time.perf_counter()
        with session() as conn:
            result = action(conn)
        return time.perf_counter() - started, meter.statements - before, result

    def import_file(name: str, body: bytes):
        return lambda conn: importer.import_files(conn, account.id, [(name, body)])

    # 1. One big file into an empty journal: the "I just signed up and uploaded my history" case.
    rows, next_id, next_day = synthetic_rows(5000, start=date(2024, 1, 2), rng=rng, first_fill_id=10_000_000_000)
    data = csv_bytes(rows)
    elapsed, statements, report = run(lambda c: importer.import_files(c, account.id, [("bulk.csv", data)]).files[0])
    print(f"IMPORT: one file of {len(rows):,} rows ({len(data) // 1024:,} KB) into an empty journal")
    print(
        f"  {elapsed:7.2f} s   {statements} statements   rows added {report.rows_added}, "
        f"trades created {report.trades_created}   (target < 10 s)"
    )
    elapsed, statements, preview = run(
        lambda c: importer.import_files(c, account.id, [("bulk.csv", data)], dry_run=True).files[0]
    )
    print(
        f"  {elapsed:7.2f} s   {statements} statements   dry-run re-import of the same file (duplicates {preview.duplicates})"
    )
    started = time.perf_counter()
    parsed_only = parse_performance_csv(data, "America/Los_Angeles")
    print(f"  {time.perf_counter() - started:7.2f} s   parse only ({len(parsed_only.pairs):,} rows, pandas)")

    # 2. A large journal, then the everyday operations on it.
    with session() as conn:
        conn.execute("DELETE FROM imports")  # cascades to the link rows
        conn.execute("DELETE FROM trades")
        conn.execute("DELETE FROM fill_pairs")
    remaining = args.trades
    fill_id, day = 20_000_000_000, date(2024, 1, 2)
    started = time.perf_counter()
    before = meter.statements
    while remaining > 0:
        chunk = min(remaining, 2500)
        rows, fill_id, day = synthetic_rows(chunk, start=day, rng=rng, first_fill_id=fill_id)
        run(import_file(f"chunk-{remaining}.csv", csv_bytes(rows)))
        remaining -= chunk
    _, _, counts = run(
        lambda c: (
            c.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"],
            c.execute("SELECT COUNT(*) AS n FROM fill_pairs").fetchone()["n"],
        )
    )
    print(
        f"\nLARGE JOURNAL: {counts[0]:,} trades from {counts[1]:,} fill pairs, built in "
        f"{time.perf_counter() - started:.1f} s ({meter.statements - before} statements)"
    )
    if not args.no_analyze:
        # Autovacuum gathers the planner's statistics within about a minute of a bulk load; a benchmark that starts
        # reading at once would otherwise measure the gap before it does.
        with psycopg.connect(owner_url, autocommit=True) as owner:
            owner.execute("ANALYZE")

    rows, fill_id, day = synthetic_rows(20, start=day, rng=rng, first_fill_id=fill_id)
    daily = csv_bytes(rows)
    samples = []
    for number in range(args.repeat):
        rows, fill_id, day = synthetic_rows(20, start=day, rng=rng, first_fill_id=fill_id)
        seconds, _, _ = run(import_file(f"day-{number}.csv", csv_bytes(rows)))
        samples.append(seconds)
    print(f"  import of one day (20 trades)    {describe(samples)}")

    def per_request(action) -> list[float]:
        return timed(lambda: run(action), args.repeat)

    print(
        f"  dry-run preview of one day       "
        f"{describe(per_request(lambda c: importer.import_files(c, account.id, [('day.csv', daily)], dry_run=True)))}"
    )
    everything = queries.TradeFilter()
    print(f"  load_trades (all trades)         {describe(per_request(lambda c: queries.load_trades(c, everything)))}")
    _, _, trades = run(lambda c: queries.load_trades(c, everything))

    def dashboard_compute() -> None:
        summary = finance.summarize(trades)
        days = finance.daily_results(trades)
        finance.performance_score(summary, days)
        finance.equity_curve([t.net_pnl for t in trades])

    print(f"  compute only (summary, days...)  {describe(timed(dashboard_compute, args.repeat))}")

    def dashboard_total(conn) -> None:
        accounts.list_accounts(conn)
        loaded = queries.load_trades(conn, everything)
        summary = finance.summarize(loaded)
        days = finance.daily_results(loaded)
        finance.performance_score(summary, days)
        finance.equity_curve([t.net_pnl for t in loaded])

    print(f"  DASHBOARD DATA (load + compute)  {describe(per_request(dashboard_total))}   (target p95 < 800 ms)")
    month = queries.TradeFilter(start=date(2024, 6, 1), end=date(2024, 6, 30))
    print(f"  load_trades, one month           {describe(per_request(lambda c: queries.load_trades(c, month)))}")

    elapsed, statements, _ = run(lambda c: store.rebuild_account(c, account.id))
    print(f"  rebuild_account (repair tool)    {elapsed:7.2f} s   {statements} statements")

    def undo_first(conn):
        first_import = conn.execute("SELECT MIN(id) AS id FROM imports").fetchone()["id"]
        return importer.undo_import(conn, first_import)

    elapsed, statements, undo = run(undo_first)
    print(
        f"  undo_import (first chunk)        {elapsed:7.2f} s   {statements} statements   "
        f"rows removed {undo.rows_removed}, trades removed {undo.trades_removed}"
    )
    db.close()


if __name__ == "__main__":
    main()
