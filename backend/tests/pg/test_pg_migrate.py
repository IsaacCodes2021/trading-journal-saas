"""The migration runner: run once, never edit a shipped file, and take turns."""

from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from tradejournal.pg import migrate as pg_migrate


@pytest.fixture
def scratch(tmp_path, owner_url):
    """An extra migration directory; its bookkeeping row and table are removed afterwards."""
    yield tmp_path
    with psycopg.connect(owner_url, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS scratch_table")
        conn.execute("DELETE FROM schema_migrations WHERE version LIKE '9%'")


def test_the_real_migrations_are_recorded_and_a_second_run_does_nothing(owner_url):
    assert pg_migrate.migrate(owner_url) == []
    with psycopg.connect(owner_url) as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions[0] == "0001_tenancy_core"


def test_a_new_migration_is_applied_once(owner_url, scratch):
    (scratch / "9001_scratch.sql").write_text("CREATE TABLE scratch_table (id int);", encoding="utf-8")
    assert pg_migrate.migrate(owner_url, scratch) == ["9001_scratch"]
    assert pg_migrate.migrate(owner_url, scratch) == []


def test_a_migration_that_fails_changes_nothing(owner_url, scratch):
    (scratch / "9002_broken.sql").write_text("CREATE TABLE scratch_table (id int); SELECT 1/0;", encoding="utf-8")
    with pytest.raises(psycopg.errors.DivisionByZero):
        pg_migrate.migrate(owner_url, scratch)
    with psycopg.connect(owner_url) as conn:
        assert conn.execute("SELECT to_regclass('scratch_table')").fetchone()[0] is None  # rolled back whole
        assert conn.execute("SELECT count(*) FROM schema_migrations WHERE version = '9002_broken'").fetchone()[0] == 0


def test_a_shipped_migration_cannot_be_edited(owner_url, scratch):
    path = scratch / "9003_shipped.sql"
    path.write_text("CREATE TABLE scratch_table (id int);", encoding="utf-8")
    pg_migrate.migrate(owner_url, scratch)
    path.write_text("CREATE TABLE scratch_table (id int, extra int);", encoding="utf-8")
    with pytest.raises(pg_migrate.MigrationError, match="changed after it was applied"):
        pg_migrate.migrate(owner_url, scratch)


def test_two_runners_at_once_take_turns(owner_url, scratch):
    (scratch / "9004_slow.sql").write_text("SELECT pg_sleep(1); CREATE TABLE scratch_table (id int);", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(f.result() for f in [pool.submit(pg_migrate.migrate, owner_url, scratch) for _ in range(2)])
    assert results == [[], ["9004_slow"]]  # one applied it; the other waited, then found nothing to do
