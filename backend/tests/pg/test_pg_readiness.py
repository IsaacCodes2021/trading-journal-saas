"""The database half of /readyz, against a real Postgres: reachable, and migrated at least as far as this image."""

import re
import time

import psycopg
import pytest

from tradejournal.pg import migrate
from tradejournal.pg.readiness import DatabaseReadiness
from tradejournal.pg.session import Database


def test_ready_when_the_database_is_migrated_to_what_this_image_ships(db):
    assert DatabaseReadiness(db)() == (True, {"database": "ok"})


def test_the_schema_version_is_the_newest_migration_file(db):
    assert db.schema_version() == migrate.latest_version()
    assert migrate.latest_version().startswith("0015_")


def test_not_ready_when_the_image_expects_a_newer_schema_than_the_database_has(db):
    assert DatabaseReadiness(db, expected_version="9999_from_the_future")() == (False, {"database": "schema_behind"})


def test_a_database_that_is_newer_than_the_image_is_still_ready(db):
    # An old container keeps serving while a deploy has already migrated forward.
    assert DatabaseReadiness(db, expected_version="0001_tenancy_core")() == (True, {"database": "ok"})


def test_not_ready_when_the_database_is_unreachable_and_it_answers_fast():
    database = Database("postgresql://app_rw:x@127.0.0.1:9/tj?connect_timeout=1")
    database.open(wait=False)  # nothing listens on port 9; the web process must still be able to boot
    try:
        started = time.monotonic()
        result = DatabaseReadiness(database, timeout=1.0)()
        assert result == (False, {"database": "unreachable"})
        assert time.monotonic() - started < 5
    finally:
        database.close()


def test_not_ready_when_the_pool_was_never_opened():
    database = Database("postgresql://app_rw:x@127.0.0.1:9/tj")
    assert DatabaseReadiness(database, timeout=0.5)() == (False, {"database": "unreachable"})


def test_a_database_without_the_function_reads_as_behind_not_as_down(owner_url, app_url, db):
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute("ALTER FUNCTION app_schema_version() RENAME TO app_schema_version_hidden")
        try:
            assert DatabaseReadiness(db)() == (False, {"database": "schema_behind"})
        finally:
            conn.execute("ALTER FUNCTION app_schema_version_hidden() RENAME TO app_schema_version")
    assert DatabaseReadiness(db)() == (True, {"database": "ok"})


def test_a_failure_logs_only_the_kind_of_error(caplog):
    database = Database("postgresql://app_rw:hunter2@127.0.0.1:9/tj?connect_timeout=1")
    with caplog.at_level("WARNING", logger="tradejournal.readiness"):
        DatabaseReadiness(database, timeout=0.3)()
    ours = [record.getMessage() for record in caplog.records if record.name == "tradejournal.readiness"]
    assert len(ours) == 1
    assert re.fullmatch(r"readiness: the database check failed \(\w+\)", ours[0]), ours[0]  # a type name, nothing else


# ---- what the request role can and cannot see of the migrations --------------------------------------------------


def test_the_request_role_reads_the_version_only_through_the_function(owner, app_url):
    with psycopg.connect(app_url) as raw:
        assert raw.execute("SELECT app_schema_version()").fetchone()[0] == migrate.latest_version()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            raw.execute("SELECT version FROM schema_migrations")


def test_the_function_is_a_fixed_path_security_definer_that_only_the_request_role_may_call(owner):
    row = owner.execute(
        """SELECT p.prosecdef AS definer, p.proconfig AS config, pg_get_userbyid(p.proowner) AS owner
           FROM pg_proc p WHERE p.proname = 'app_schema_version'"""
    ).fetchone()
    assert row["definer"] is True
    assert row["owner"] == "tj_owner"
    assert any(setting.startswith("search_path=") for setting in row["config"]), "a definer function needs a fixed path"
    for role, allowed in [
        ("app_rw", True),
        ("app_login", False),
        ("app_webhook", False),
        ("app_sweep", False),
        ("app_admin", False),
    ]:
        got = owner.execute(
            "SELECT has_function_privilege(%s, 'app_schema_version()', 'EXECUTE') AS ok", (role,)
        ).fetchone()["ok"]
        assert got is allowed, role
    public = owner.execute(
        "SELECT has_function_privilege('public', 'app_schema_version()', 'EXECUTE') AS ok"
    ).fetchone()["ok"]
    assert public is False
