"""How long a request may wait and run (doc 04, T5: request timeouts), against a real Postgres.

Three limits, all settings of one transaction like the tenant itself, so none can leak to the next user of a pooled
connection: a wait for a free connection (`PoolTimeout`), a statement that runs too long (`QueryCanceled`) and a wait for
a lock (`LockNotAvailable`). Each becomes a 503 that says what happened and when to come back.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import psycopg
import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient
from helpers import SAMPLE, fixture_bytes
from pg_helpers import (
    NOON_UTC,
    ORIGIN,
    SECRET,
    api_client,
    api_client_with_real_accounts,
    make_account,
    write_headers,
)
from psycopg_pool import PoolTimeout

from tradejournal.api import errors as api_errors
from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import HEAVY, SESSION_COOKIE, TenantDep
from tradejournal.pg import session as pg_session
from tradejournal.pg import system as pg_system
from tradejournal.pg.session import (
    HEAVY_STATEMENT_TIMEOUT_MS,
    LOCK_TIMEOUT_MS,
    POOL_WAIT_SECONDS,
    STATEMENT_TIMEOUT_MS,
    Database,
)


def setting(conn, name: str) -> str:
    return conn.execute("SELECT current_setting(%s) AS value", (name,)).fetchone()["value"]


# ---- the settings of a session ----------------------------------------------------------------------------------------


def test_the_limits_are_the_ones_the_design_names():
    assert (POOL_WAIT_SECONDS, STATEMENT_TIMEOUT_MS, HEAVY_STATEMENT_TIMEOUT_MS, LOCK_TIMEOUT_MS) == (
        5.0,
        15_000,
        120_000,
        5_000,
    )
    assert HEAVY == HEAVY_STATEMENT_TIMEOUT_MS


def test_an_ordinary_session_runs_statements_for_fifteen_seconds_and_waits_for_a_lock_for_five(db, alice):
    with db.tenant_session(alice) as conn:
        assert setting(conn, "statement_timeout") == "15s"
        assert setting(conn, "lock_timeout") == "5s"
        assert setting(conn, "app.user_id") == str(alice)  # the tenant is still set, in the same statement


def test_a_heavy_session_is_given_two_minutes(db, alice):
    with db.tenant_session(alice, statement_timeout_ms=HEAVY_STATEMENT_TIMEOUT_MS) as conn:
        assert setting(conn, "statement_timeout") == "2min"
        assert (
            setting(conn, "lock_timeout") == "5s"
        )  # a lock is never worth waiting for longer, however heavy the route


def test_a_snapshot_session_is_still_read_only_and_repeatable_read_with_the_limits_on(db, alice):
    with db.tenant_session(alice, snapshot=True, statement_timeout_ms=1234) as conn:
        assert setting(conn, "transaction_read_only") == "on"
        assert setting(conn, "transaction_isolation") == "repeatable read"
        assert setting(conn, "statement_timeout") == "1234ms"


def test_a_statement_that_runs_too_long_is_cancelled_when_the_time_is_up_and_not_before(db, alice):
    started = time.monotonic()
    with pytest.raises(psycopg.errors.QueryCanceled):
        with db.tenant_session(alice, statement_timeout_ms=300) as conn:
            conn.execute("SELECT pg_sleep(5)")
    assert 0.25 < time.monotonic() - started < 3
    with db.tenant_session(alice, statement_timeout_ms=3000) as conn:  # well inside its limit: untouched
        conn.execute("SELECT pg_sleep(0.2)")


def test_the_limits_belong_to_one_transaction_and_do_not_follow_the_connection(app_url, alice):
    one = Database(app_url, max_size=1)  # the one connection is reused by every session
    one.open()
    try:
        with one.tenant_session(alice, statement_timeout_ms=250) as conn:
            assert setting(conn, "statement_timeout") == "250ms"
        with one.tenant_session(alice) as conn:
            assert setting(conn, "statement_timeout") == "15s"
        with one._pool.connection() as conn:  # outside any tenant session: the server's own settings again
            assert setting(conn, "statement_timeout") == "0"
            assert setting(conn, "lock_timeout") == "0"
            assert conn.execute("SELECT current_setting('app.user_id', true) AS v").fetchone()["v"] in (None, "")
    finally:
        one.close()


def test_a_wait_for_a_lock_ends_after_the_lock_timeout(db, alice, owner, monkeypatch):
    monkeypatch.setattr(pg_session, "LOCK_TIMEOUT_MS", 200)
    holder_ready, release = threading.Event(), threading.Event()
    key = 424242

    def hold():
        with db.tenant_session(alice) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
            holder_ready.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert holder_ready.wait(5)
        started = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with db.tenant_session(alice) as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        assert 0.15 < time.monotonic() - started < 3
    finally:
        release.set()
        thread.join(10)


# ---- the pool ------------------------------------------------------------------------------------------------------


def test_the_pool_is_told_how_long_to_wait(app_url):
    for wait in (0.5, 7.0):
        pool_db = Database(app_url, pool_wait=wait)
        assert pool_db._pool.timeout == wait
    assert Database(app_url)._pool.timeout == POOL_WAIT_SECONDS


def test_a_request_that_cannot_get_a_connection_gives_up_after_the_wait_and_not_after_thirty_seconds(app_url, alice):
    small = Database(app_url, max_size=1, pool_wait=0.3)
    small.open()
    try:
        started = time.monotonic()
        with small.tenant_session(alice):  # takes the only connection
            with pytest.raises(PoolTimeout):
                with small.tenant_session(alice):
                    pass
        assert 0.25 < time.monotonic() - started < 3
        with small.tenant_session(alice) as conn:  # and the pool is usable again afterwards
            assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
    finally:
        small.close()


# ---- the system roles -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("login", "10s"), ("webhook", "10s"), ("admin", "15s"), ("sweep", "5min")],
)
def test_each_system_role_has_its_own_statement_limit(system, profile, expected):
    try:
        with system.system_session(profile) as conn:
            assert setting(conn, "statement_timeout") == expected
    except LookupError:
        pytest.skip(f"the {profile} role is not configured here")


def test_the_system_limits_are_exactly_the_documented_ones():
    assert pg_system.STATEMENT_TIMEOUT_MS == {"login": 10_000, "webhook": 10_000, "admin": 15_000, "sweep": 300_000}
    assert set(pg_system.STATEMENT_TIMEOUT_MS) == set(pg_system.PROFILES)
    assert pg_system.POOL_WAIT_SECONDS == 5.0


def test_the_system_pools_wait_the_same_few_seconds_for_a_connection_as_the_request_pool(system):
    assert system._pools and all(pool.timeout == pg_system.POOL_WAIT_SECONDS for pool in system._pools.values())


def test_a_system_session_that_runs_too_long_is_cancelled(system):
    with pytest.raises(psycopg.errors.QueryCanceled):
        with system.system_session("login") as conn:
            conn.execute("SELECT set_config('statement_timeout', '200', true)")  # a test shortens its own limit
            conn.execute("SELECT pg_sleep(3)")


# ---- what a person is told -------------------------------------------------------------------------------------------


def make_client(db, user_id, slow_route) -> TestClient:
    """The real app with one extra route, signed in as `user_id`."""
    services, sessions, _ = make_services()
    token, session = sessions.sign_in(user_id)
    config = ApiConfig(app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=SECRET)
    app = create_app(config, services, database=db, clock=lambda: NOON_UTC)
    app.post("/api/v1/_slow")(slow_route)
    return TestClient(
        app,
        base_url=ORIGIN,
        raise_server_exceptions=False,
        headers={"cookie": f"{SESSION_COOKIE}={token}", **write_headers(session)},
    )


def test_a_query_that_runs_too_long_is_a_503_that_says_so_and_when_to_come_back(db, alice, caplog):
    def slow(tenant: TenantDep) -> dict:
        with tenant.session(statement_timeout_ms=200) as conn:
            conn.execute("SELECT pg_sleep(3)")
        return {}

    with caplog.at_level("WARNING", logger="tj.db"):
        response = make_client(db, alice, slow).post("/api/v1/_slow")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "10"
    assert response.json()["detail"] == (
        "That took too long and was stopped. Try again in a moment, or ask for less at once (a shorter period, fewer filters)."
    )
    assert "sleep" not in response.text  # nothing of the statement reaches the person
    [record] = [r for r in caplog.records if r.name == "tj.db"]
    assert isinstance(record.exc_info[1], psycopg.errors.QueryCanceled)  # the log keeps what it was


def test_a_pool_with_no_free_connection_is_a_503_that_says_the_server_is_busy(app_url, alice):
    small = Database(app_url, max_size=1, pool_wait=0.2)
    small.open()
    try:
        started, release = threading.Event(), threading.Event()

        def hold():
            with small.tenant_session(alice):
                started.set()
                release.wait(10)

        def waits(tenant: TenantDep) -> dict:
            with tenant.session():
                return {}

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert started.wait(5)
            response = make_client(small, alice, waits).post("/api/v1/_slow")
        finally:
            release.set()
            thread.join(10)
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert response.json()["detail"] == "The server is busy right now. Try again in a few seconds."
    finally:
        small.close()


def test_a_wait_for_a_lock_is_a_503_that_says_something_else_is_changing_the_journal(db, alice, monkeypatch):
    monkeypatch.setattr(pg_session, "LOCK_TIMEOUT_MS", 150)
    key, ready, release = 515151, threading.Event(), threading.Event()

    def hold():
        with db.tenant_session(alice) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
            ready.set()
            release.wait(10)

    def waits(tenant: TenantDep) -> dict:
        with tenant.session() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
        return {}

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert ready.wait(5)
        response = make_client(db, alice, waits).post("/api/v1/_slow")
    finally:
        release.set()
        thread.join(10)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.json()["detail"] == "Your journal is being changed by something else. Try again in a few seconds."


def test_the_three_answers_are_distinct_and_the_handlers_are_registered_for_exactly_those_errors():
    assert set(api_errors._BUSY) == {PoolTimeout, psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable}
    assert len({sentence for sentence, _ in api_errors._BUSY.values()}) == 3
    assert {retry for _, retry in api_errors._BUSY.values()} == {5, 10}


def test_other_database_errors_are_still_a_plain_500_that_leaks_nothing(db, alice):
    def breaks(tenant: TenantDep) -> dict:
        with tenant.session() as conn:
            conn.execute("SELECT 1/0")
        return {}

    response = make_client(db, alice, breaks).post("/api/v1/_slow")
    assert response.status_code == 500
    assert "division" not in response.text and "retry-after" not in response.headers


# ---- which routes get the long limit -----------------------------------------------------------------------------------


@pytest.fixture
def seen(db, monkeypatch):
    """Every tenant session the app opens, as (statement timeout, snapshot), in order."""
    opened: list[tuple[int, bool]] = []
    real = Database.tenant_session

    def spy(self, user_id, *, snapshot=False, statement_timeout_ms=STATEMENT_TIMEOUT_MS):
        opened.append((statement_timeout_ms, snapshot))
        return real(self, user_id, snapshot=snapshot, statement_timeout_ms=statement_timeout_ms)

    monkeypatch.setattr(Database, "tenant_session", spy)
    return opened


@pytest.fixture
def journal(db, alice):
    with db.tenant_session(alice) as conn:
        account = make_account(conn, "Apex 07")
    return account.id


def sessions_of(seen, call) -> list[tuple[int, bool]]:
    seen.clear()
    call()
    return list(seen)


def test_ordinary_routes_get_the_ordinary_limit(db, alice, journal, seen):
    client = api_client(db, alice)
    for path in ("/api/v1/dashboard", "/api/v1/trades", "/api/v1/accounts", "/api/v1/tags", "/api/v1/imports"):
        used = sessions_of(seen, lambda path=path: client.get(path))
        assert used and {t for t, _ in used} == {STATEMENT_TIMEOUT_MS}, path


def test_the_routes_that_touch_every_row_get_the_long_limit(db, system, alice, journal, seen):
    client = api_client_with_real_accounts(db, system, alice)
    upload = {"data": {"account_id": str(journal)}, "files": {"file": ("x.csv", fixture_bytes(SAMPLE), "text/csv")}}
    assert {t for t, _ in sessions_of(seen, lambda: client.post("/api/v1/imports/preview", **upload))} == {HEAVY}
    commit = client.post("/api/v1/imports", **upload).json()
    assert {t for t, _ in sessions_of(seen, lambda: client.post("/api/v1/imports", **upload))} == {HEAVY}
    base = f"/api/v1/imports/{commit['id']}"
    assert {t for t, _ in sessions_of(seen, lambda: client.post(f"{base}/undo-preview"))} == {HEAVY}
    assert {t for t, _ in sessions_of(seen, lambda: client.post(f"{base}/undo"))} == {HEAVY}
    fee = f"/api/v1/accounts/{journal}/fees/NQ"
    assert {t for t, _ in sessions_of(seen, lambda: client.put(fee, json={"rate": "1.50"}))} == {HEAVY}
    assert {t for t, _ in sessions_of(seen, lambda: client.delete(fee))} == {HEAVY}
    assert {t for t, _ in sessions_of(seen, lambda: client.patch("/api/v1/settings", json={"theme": "light"}))} == {
        HEAVY
    }
    export = sessions_of(seen, lambda: client.get("/api/v1/export"))
    assert [t for t, snapshot in export if snapshot] == [HEAVY]  # the one snapshot that builds the zip
    assert (STATEMENT_TIMEOUT_MS, False) in export  # reading who the person is stays an ordinary session
