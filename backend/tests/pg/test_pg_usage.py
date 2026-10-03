"""Usage counters (doc 03 section 2): what quotas and the operator back end read instead of scanning tenant tables."""

import psycopg
import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import make_account, make_trades

from tradejournal.pg import importer, journal, store, usage

SAMPLE_FILE = (SAMPLE, fixture_bytes(SAMPLE))
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def recount(conn) -> tuple:
    """What the counters should say, straight from the tables."""
    row = conn.execute(
        """SELECT (SELECT count(*) FROM accounts) AS accounts, (SELECT count(*) FROM trades) AS trades,
                  (SELECT count(*) FROM screenshots) AS shots,
                  (SELECT max(imported_at) FROM imports WHERE undone_at IS NULL) AS last_import"""
    ).fetchone()
    return row["accounts"], row["trades"], row["shots"], row["last_import"]


def stored(conn) -> tuple:
    u = usage.get_usage(conn)
    return u.accounts_count, u.trades_count, u.screenshots_count, u.last_import_at


def test_a_new_user_starts_with_a_row_of_zeroes(conn):
    assert conn.execute("SELECT count(*) AS n FROM user_usage").fetchone()["n"] == 1  # a real row, not a default
    assert stored(conn) == (0, 0, 0, None)
    assert usage.get_usage(conn).storage_bytes == 0


def test_a_user_with_no_row_reads_as_zeroes(owner, db):
    row = owner.execute("INSERT INTO users (idp_subject, email) VALUES ('idp|bare', 'bare@example.test') RETURNING id")
    with db.tenant_session(row.fetchone()["id"]) as conn:  # no provision_defaults
        assert usage.get_usage(conn) == usage.Usage(0, 0, 0, 0, None, None)


def test_creating_an_account_counts_it(conn):
    make_account(conn, "One")
    make_account(conn, "Two")
    assert stored(conn)[0] == 2


def test_an_import_counts_trades_and_records_when(conn, account):
    importer.import_files(conn, account.id, [SAMPLE_FILE])
    assert stored(conn)[:3] == (1, 8, 0)
    assert stored(conn)[3] is not None
    assert stored(conn) == recount(conn)


def test_a_dry_run_changes_nothing(conn, account):
    before = usage.get_usage(conn)
    importer.import_files(conn, account.id, [SAMPLE_FILE], dry_run=True)
    assert usage.get_usage(conn) == before


def test_undo_takes_the_trades_and_the_last_import_away(conn, account):
    [report] = importer.import_files(conn, account.id, [SAMPLE_FILE]).files
    importer.undo_import(conn, report.import_id)
    assert stored(conn) == (1, 0, 0, None)  # the account stays; nothing is left to have been imported
    assert stored(conn) == recount(conn)


def test_the_last_import_is_the_latest_one_not_undone(conn, account):
    [first] = importer.import_files(conn, account.id, [SAMPLE_FILE]).files
    [second] = importer.import_files(conn, account.id, [SAMPLE_FILE]).files  # all duplicates, but a recorded import
    assert (
        stored(conn)[3]
        > conn.execute("SELECT imported_at FROM imports WHERE id = %s", (first.import_id,)).fetchone()["imported_at"]
    )
    importer.undo_import(conn, second.import_id)
    first_at = conn.execute("SELECT imported_at FROM imports WHERE id = %s", (first.import_id,)).fetchone()[
        "imported_at"
    ]
    assert stored(conn)[3] == first_at


def test_screenshots_are_counted_when_added_and_removed(conn, account, tmp_path):
    [trade_id] = make_trades(conn, account.id, 1)
    shot = journal.add_screenshot(conn, tmp_path, trade_id, "a.png", PNG)
    journal.add_screenshot(conn, tmp_path, trade_id, "b.png", PNG)
    assert stored(conn)[2] == 2
    journal.delete_screenshot(conn, shot.id)
    assert stored(conn)[2] == 1
    assert journal.delete_screenshot(conn, shot.id) is None  # already gone: nothing changes
    assert stored(conn) == recount(conn)


def test_a_full_rebuild_that_merges_trades_updates_the_count(conn, account):
    importer.import_files(conn, account.id, [SAMPLE_FILE])
    conn.execute("UPDATE user_usage SET trades_count = 999")  # simulate drift
    store.rebuild_account(conn, account.id)
    assert stored(conn) == recount(conn)


def test_refresh_repairs_drift(conn, account):
    importer.import_files(conn, account.id, [SAMPLE_FILE])
    conn.execute("UPDATE user_usage SET accounts_count = 50, trades_count = 0, screenshots_count = 7")
    usage.refresh(conn)
    assert stored(conn) == recount(conn)


def test_refresh_leaves_storage_alone_until_files_have_a_size(conn):
    conn.execute("UPDATE user_usage SET storage_bytes = 12345")
    usage.refresh(conn)
    assert usage.get_usage(conn).storage_bytes == 12345


@pytest.mark.parametrize("column", ["accounts_count", "trades_count", "screenshots_count", "storage_bytes"])
def test_a_counter_cannot_go_negative(conn, column):
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(f"UPDATE user_usage SET {column} = -1")  # noqa: S608 (fixed column names)


def test_one_users_counters_never_include_another_users_rows(db, alice, bob):
    with db.tenant_session(alice) as a:
        mine = make_account(a)
        make_trades(a, mine.id, 5)
        usage.refresh(a)
    with db.tenant_session(bob) as b:
        usage.refresh(b)
        assert stored(b) == (0, 0, 0, None)
    with db.tenant_session(alice) as a:
        assert stored(a)[:3] == (1, 5, 0)


def test_the_request_role_cannot_write_another_users_usage_row(db, alice, bob):
    with db.tenant_session(bob) as b:
        with pytest.raises(psycopg.errors.InsufficientPrivilege), b.transaction():
            b.execute("INSERT INTO user_usage (user_id, trades_count) VALUES (%s, 1)", (alice,))
        assert b.execute("UPDATE user_usage SET trades_count = 99 WHERE user_id = %s", (alice,)).rowcount == 0
