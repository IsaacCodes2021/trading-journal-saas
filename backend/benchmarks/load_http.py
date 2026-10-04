"""An HTTP load test: about a hundred people using the real app at once, against a real Postgres (doc 05, section 8).

    cd backend
    .venv\\Scripts\\python.exe benchmarks\\load_http.py [--users 100] [--seconds 30] [--think 1.0] [--big-trades 10000]
                                                       [--pool 10] [--imports 2]

What it builds and measures:

- one journal per person in the test database (``TJ_TEST_OWNER_URL`` and ``TJ_TEST_APP_URL``, from backend/.env.test or
  the environment; **it empties that database first**, as the Postgres tests do), a few hundred trades each, and one
  person with ``--big-trades`` (the "10,000 trades" case of the launch gate);
- the real API served by uvicorn on a local port, with the in-memory session fakes (sessions are not what is measured;
  every other layer is real: the middleware stack, CSRF, rate limits, the request role, row-level security, the queries);
- ``--users`` people, each a thread with its own connection, doing what a person does (the dashboard, the trade log,
  a trade's page, the accounts, the tags) with ``--think`` seconds between requests, for ``--seconds`` seconds;
- while they do, ``--imports`` people upload a 5,000-row file (the "I just signed up and uploaded my history" case).

It prints per-request latency percentiles, the status codes that came back (a 503 is the server saying it is busy), and
the import times. **The load generator, the server and the database share this machine**, so the numbers are a floor on
what a real host with its own database would do, not a promise: use them to compare settings and to find what breaks.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import platform
import random
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import uvicorn
from psycopg.rows import dict_row

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "tests"))

from api_fakes import make_services  # noqa: E402
from baseline import csv_bytes, synthetic_rows  # noqa: E402

from tradejournal.api.app import create_app  # noqa: E402
from tradejournal.api.config import ApiConfig  # noqa: E402
from tradejournal.api.deps import SESSION_COOKIE  # noqa: E402
from tradejournal.auth.csrf import csrf_token  # noqa: E402
from tradejournal.pg import accounts, importer, migrate  # noqa: E402
from tradejournal.pg.defaults import provision_defaults  # noqa: E402
from tradejournal.pg.provision import read_env_file  # noqa: E402
from tradejournal.pg.session import Database  # noqa: E402

ENV_FILE = HERE.parent / ".env.test"
SECRET = b"load-test-secret-0123456789abcdef"
PORT = 18765


def connection_urls() -> tuple[str, str]:
    env = {**read_env_file(ENV_FILE), **os.environ}
    owner, app = env.get("TJ_TEST_OWNER_URL"), env.get("TJ_TEST_APP_URL")
    if not owner or not app:
        sys.exit("Set TJ_TEST_OWNER_URL and TJ_TEST_APP_URL, or run: python -m tradejournal.pg.provision (see --help)")
    return owner, app


def percentile(ordered: list[float], share: float) -> float:
    return ordered[min(len(ordered) - 1, int(len(ordered) * share))]


def describe(samples: list[float]) -> str:
    ordered = sorted(samples)
    return (
        f"n={len(ordered):5d}  median {statistics.median(ordered) * 1000:7.1f} ms  p95 {percentile(ordered, 0.95) * 1000:7.1f} ms"
        f"  p99 {percentile(ordered, 0.99) * 1000:7.1f} ms  max {ordered[-1] * 1000:7.1f} ms"
    )


def build_journals(owner_url: str, db: Database, users: int, big_trades: int, per_person: int, rng: random.Random):
    """People with journals: person 0 is the big one. Returns their ids and each one's account id."""
    with psycopg.connect(owner_url, autocommit=True, row_factory=dict_row) as owner:
        owner.execute("DROP SCHEMA public CASCADE")
        owner.execute("CREATE SCHEMA public")
        migrate.migrate(owner_url)
        ids = [
            owner.execute(
                "INSERT INTO users (idp_subject, email) VALUES (%s, %s) RETURNING id",
                (f"load|{n}", f"load{n}@example.test"),
            ).fetchone()["id"]
            for n in range(users)
        ]
    account_ids: list[int] = []
    fill_id, started = 30_000_000_000, time.perf_counter()
    for number, user_id in enumerate(ids):
        wanted = big_trades if number == 0 else per_person
        with db.tenant_session(user_id, statement_timeout_ms=600_000) as conn:
            provision_defaults(conn)
            account = accounts.create_account(
                conn, f"Load {number}", firm="Apex", account_size=Decimal("50000"), starting_balance=Decimal("50000")
            )
            accounts.set_fee(conn, account.id, "*", Decimal("4.00"))
            day = date(2024, 1, 2)
            while wanted > 0:
                chunk = min(wanted, 2500)
                rows, fill_id, day = synthetic_rows(chunk, start=day, rng=rng, first_fill_id=fill_id)
                importer.import_files(conn, account.id, [(f"seed-{number}.csv", csv_bytes(rows))])
                wanted -= chunk
        account_ids.append(account.id)
    with psycopg.connect(owner_url, autocommit=True) as owner:
        owner.execute("ANALYZE")
    print(
        f"built {users} journals ({big_trades:,} trades for the first, {per_person} for the others) in {time.perf_counter() - started:.0f} s"
    )
    return ids, account_ids


class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.samples: dict[str, list[float]] = defaultdict(list)
        self.statuses: Counter[tuple[str, int]] = Counter()

    def add(self, name: str, status: int, seconds: float) -> None:
        with self.lock:
            self.samples[name].append(seconds)
            self.statuses[(name, status)] += 1


def person(client: httpx.Client, number: int, trade_ids: list[int], deadline: float, think: float, recorder: Recorder):
    """One person at their screen: look at something, pause, look at something else."""
    rng = random.Random(number)  # noqa: S311  (a load pattern, not a secret)
    big = "big " if number == 0 else ""
    mix = [
        (f"{big}dashboard", 3, lambda: "/api/v1/dashboard"),
        (f"{big}trade log", 4, lambda: f"/api/v1/trades?limit=50&offset={rng.randrange(0, 200, 50)}"),
        ("trade page", 2, lambda: f"/api/v1/trades/{rng.choice(trade_ids)}"),
        ("accounts", 1, lambda: "/api/v1/accounts"),
        ("tags", 1, lambda: "/api/v1/tags"),
    ]
    names, weights, paths = [m[0] for m in mix], [m[1] for m in mix], [m[2] for m in mix]
    while time.monotonic() < deadline:
        pick = rng.choices(range(len(mix)), weights)[0]
        started = time.perf_counter()
        try:
            status = client.get(paths[pick]()).status_code
        except httpx.HTTPError:
            status = 0
        recorder.add(names[pick], status, time.perf_counter() - started)
        time.sleep(think * rng.uniform(0.5, 1.5))


def serve(app_url: str, pool: int, user_ids: list, tokens) -> None:
    """The server process: the real app on a local port, with every person already signed in (their tokens are sent back
    through `tokens`)."""
    db = Database(app_url, max_size=pool)
    db.open()
    services, sessions, _ = make_services()
    for user_id in user_ids:
        token, session = sessions.sign_in(user_id)
        tokens.put((str(user_id), token, session.id))
    origin = f"http://127.0.0.1:{PORT}"
    app = create_app(
        ApiConfig(app_origin=origin, web_dist=Path("/nonexistent"), session_secret=SECRET), services, database=db
    )
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--users", type=int, default=100)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--think", type=float, default=1.0, help="mean seconds between one person's requests")
    parser.add_argument("--big-trades", type=int, default=10_000)
    parser.add_argument("--per-person", type=int, default=150)
    parser.add_argument("--pool", type=int, default=10, help="the request database pool's size")
    parser.add_argument("--imports", type=int, default=2, help="people uploading a 5,000-row file during the run")
    parser.add_argument("--seed", type=int, default=20240102)
    args = parser.parse_args()

    owner_url, app_url = connection_urls()
    rng = random.Random(args.seed)  # noqa: S311  (synthetic data)
    builder = Database(app_url, max_size=2)
    builder.open()
    user_ids, account_ids = build_journals(owner_url, builder, args.users, args.big_trades, args.per_person, rng)
    builder.close()

    # The server is its own process, as it is in production: in one process the load generator's hundred threads would
    # take turns with the server's under one interpreter lock, and the test would measure that instead.
    origin = f"http://127.0.0.1:{PORT}"
    context = multiprocessing.get_context("spawn")
    tokens = context.Queue()
    server = context.Process(target=serve, args=(app_url, args.pool, user_ids, tokens), daemon=True)
    server.start()
    sign_ins = {}
    while len(sign_ins) < len(user_ids):
        user_id, token, session_id = tokens.get(timeout=120)
        sign_ins[user_id] = (token, session_id)
    while True:
        try:
            if httpx.get(f"{origin}/healthz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)

    clients, trade_ids = [], []
    for user_id in user_ids:
        token, session_id = sign_ins[str(user_id)]
        headers = {
            "cookie": f"{SESSION_COOKIE}={token}",
            "origin": origin,
            "sec-fetch-site": "same-origin",
            "x-csrf-token": csrf_token(SECRET, session_id),
        }
        client = httpx.Client(base_url=origin, headers=headers, timeout=60)
        clients.append(client)
        listing = client.get("/api/v1/trades", params={"limit": 20}).json()
        trade_ids.append([t["id"] for t in listing["items"]])
        for path in ("/api/v1/dashboard", "/api/v1/accounts", "/api/v1/tags"):
            client.get(path)  # a warm-up, not measured: the pool's first connections and each person's first request

    print(
        f"Python {platform.python_version()} on {platform.system()} {platform.release()}, {os.cpu_count()} processors; "
        f"pool {args.pool}, {args.users} people, think {args.think} s, {args.seconds:.0f} s, {args.imports} imports\n"
    )
    recorder = Recorder()
    deadline = time.monotonic() + args.seconds
    threads = [
        threading.Thread(target=person, args=(clients[n], n, trade_ids[n], deadline, args.think, recorder))
        for n in range(args.users)
    ]
    imports: list[tuple[int, float, int]] = []

    def upload(number: int) -> None:
        time.sleep(args.seconds * 0.3)
        rows, _, _ = synthetic_rows(
            5000,
            start=date(2025, 1, 2),
            rng=random.Random(number),  # noqa: S311  (synthetic data)
            first_fill_id=90_000_000_000 + number * 10_000_000,
        )
        data = csv_bytes(rows)
        started = time.perf_counter()
        response = clients[number].post(
            "/api/v1/imports",
            data={"account_id": str(account_ids[number])},
            files={"file": ("history.csv", data, "text/csv")},
        )
        imports.append((number, time.perf_counter() - started, response.status_code))

    uploaders = [threading.Thread(target=upload, args=(n + 1,)) for n in range(args.imports)]
    started = time.perf_counter()
    for thread in threads + uploaders:
        thread.start()
    for thread in threads + uploaders:
        thread.join()
    elapsed = time.perf_counter() - started

    total = sum(len(v) for v in recorder.samples.values())
    print(f"{total:,} requests in {elapsed:.0f} s = {total / elapsed:.0f} per second\n")
    for name in sorted(recorder.samples):
        print(f"  {name:14s} {describe(recorder.samples[name])}")
    everything = [s for v in recorder.samples.values() for s in v]
    print(f"\n  {'all':14s} {describe(everything)}")
    codes = Counter()
    for (_, status), count in recorder.statuses.items():
        codes[status] += count
    print(f"\n  status codes: {dict(sorted(codes.items()))}   (0 = no answer, 503 = the server said it was busy)")
    for number, seconds, status in sorted(imports):
        print(f"  import of 5,000 rows by person {number}: {seconds:.1f} s, status {status}")
    server.terminate()
    server.join(10)


if __name__ == "__main__":
    main()
