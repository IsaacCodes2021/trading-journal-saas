"""Emptying one person's journal (pg/clear_journal.py), on a real Postgres: what goes, what stays, and what it refuses."""

from datetime import date
from decimal import Decimal

import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import PNG_1X1, make_account

from tradejournal.filestore import LocalFileStore
from tradejournal.pg import accounts, clear_journal, importer, journal, ledger, migrate_local, usage
from tradejournal.pg import screenshots as shots
from tradejournal.pg.clear_journal import ClearRefused

SAMPLE_FILE = (SAMPLE, fixture_bytes(SAMPLE))
DAY = date(2024, 1, 5)
TABLES = (
    "accounts", "fee_schedules", "imports", "trades", "fill_pairs", "import_fill_pairs", "account_ledger",
    "tags", "trade_tags", "day_notes", "screenshots",
)  # fmt: skip


def seed(db, user_id, *, name="Apex 07") -> int:
    """A journal with everything the command has to take away: an account with a fee, an imported file, a ledger entry,
    a tag on a trade and a day note. Returns the account id."""
    with db.tenant_session(user_id) as conn:
        account = make_account(conn, name)
        accounts.set_fee(conn, account.id, "*", Decimal("4.00"))
        importer.import_files(conn, account.id, [SAMPLE_FILE])
        ledger.add_entry(conn, account.id, sorted(ledger.KINDS)[0], Decimal("100"), DAY)
        trade_id = conn.execute("SELECT id FROM trades ORDER BY id LIMIT 1").fetchone()["id"]
        setup = conn.execute("SELECT id FROM tag_groups WHERE name = 'Setup'").fetchone()["id"]
        journal.set_trade_tags(conn, trade_id, setup, ["scalp"])
        journal.set_day_note(conn, DAY, "<p>Choppy</p>")
        return account.id


def tables(db, user_id) -> dict[str, int]:
    with db.tenant_session(user_id) as conn:
        return {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in TABLES}  # noqa: S608


# ---- the library --------------------------------------------------------------------------------------------------------


def test_a_dry_run_counts_what_is_there_and_changes_nothing(db, alice):
    seed(db, alice)
    before = tables(db, alice)
    found = clear_journal.run(db, alice, apply=False, expect_accounts=None, expect_trades=None)
    assert (found.accounts, found.trades, found.tags, found.day_notes, found.screenshots) == (1, 8, 1, 1, 0)
    assert tables(db, alice) == before


def test_applying_empties_every_table_under_the_journal(db, alice):
    seed(db, alice)
    assert all(tables(db, alice)[t] for t in TABLES if t != "screenshots")  # the seed really filled them
    clear_journal.run(db, alice, apply=True, expect_accounts=1, expect_trades=8)
    assert tables(db, alice) == dict.fromkeys(TABLES, 0)


def test_the_counters_are_recounted(db, alice):
    seed(db, alice)
    clear_journal.run(db, alice, apply=True, expect_accounts=1, expect_trades=8)
    with db.tenant_session(alice) as conn:
        u = usage.get_usage(conn)
    assert (u.accounts_count, u.trades_count, u.screenshots_count, u.storage_bytes) == (0, 0, 0, 0)


def test_the_person_the_settings_and_the_tag_groups_stay(owner, db, alice):
    seed(db, alice)
    clear_journal.run(db, alice, apply=True, expect_accounts=1, expect_trades=8)
    assert owner.execute("SELECT count(*) AS n FROM users WHERE id = %s", (alice,)).fetchone()["n"] == 1
    with db.tenant_session(alice) as conn:
        assert conn.execute("SELECT count(*) AS n FROM user_settings").fetchone()["n"] == 1
        names = [g.name for g in journal.tag_groups(conn)]
    assert names == ["Setup", "Mistake", "Emotion"]


def test_only_that_persons_journal_is_emptied(db, alice, bob):
    seed(db, alice)
    seed(db, bob, name="Bob's")
    bobs = tables(db, bob)
    clear_journal.run(db, alice, apply=True, expect_accounts=1, expect_trades=8)
    assert tables(db, bob) == bobs and bobs["trades"] == 8


@pytest.mark.parametrize(("accounts_", "trades_"), [(2, 8), (1, 7), (0, 0)])
def test_it_refuses_when_the_counts_are_not_the_ones_expected_and_changes_nothing(db, alice, accounts_, trades_):
    seed(db, alice)
    before = tables(db, alice)
    with pytest.raises(ClearRefused, match="not the"):
        clear_journal.run(db, alice, apply=True, expect_accounts=accounts_, expect_trades=trades_)
    assert tables(db, alice) == before


def test_it_refuses_while_a_screenshot_exists_so_no_file_is_left_behind(db, alice, tmp_path):
    seed(db, alice)
    files = LocalFileStore(tmp_path / "files")
    with db.tenant_session(alice) as conn:
        trade_id = conn.execute("SELECT id FROM trades ORDER BY id LIMIT 1").fetchone()["id"]
        shots.add(conn, files, alice, trade_id, "chart.png", PNG_1X1)
    before = tables(db, alice)
    for apply in (False, True):
        with pytest.raises(ClearRefused, match="screenshots"):
            clear_journal.run(db, alice, apply=apply, expect_accounts=1, expect_trades=8)
    assert tables(db, alice) == before and list(files.list("u"))  # the file is still there too


def test_if_anything_is_still_there_afterwards_the_whole_thing_is_undone(db, alice, monkeypatch):
    seed(db, alice)
    before = tables(db, alice)
    real = clear_journal.count
    calls = []

    def count_with_a_leftover(conn):
        calls.append(1)
        found = real(conn)
        # the second count is the one taken after the deletes: pretend a trade survived them
        return found if len(calls) == 1 else clear_journal.Counts(0, 1, 0, 0, 0)

    monkeypatch.setattr(clear_journal, "count", count_with_a_leftover)
    with pytest.raises(ClearRefused, match="left behind"):
        clear_journal.run(db, alice, apply=True, expect_accounts=1, expect_trades=8)
    monkeypatch.undo()
    assert tables(db, alice) == before  # rolled back: nothing was deleted


def test_after_it_the_migration_will_take_the_journal(db, alice):
    seed(db, alice)
    clear_journal.run(db, alice, apply=True, expect_accounts=1, expect_trades=8)
    with db.tenant_session(alice) as conn:
        migrate_local._refuse_if_not_empty(conn)  # raises MigrationRefused if anything is left


def test_an_empty_journal_is_a_plain_zero_not_an_error(db, alice):
    found = clear_journal.run(db, alice, apply=False, expect_accounts=None, expect_trades=None)
    assert found.empty()


# ---- the command ---------------------------------------------------------------------------------------------------------


def test_the_command_defaults_to_a_dry_run_and_prints_only_counts(db, alice, app_url, capsys):
    seed(db, alice, name="Secret Firm Account")
    assert clear_journal.main(["--user-id", str(alice), "--database-url", app_url]) == 0
    shown = capsys.readouterr()
    assert "would remove 1 accounts, 8 trades" in shown.out
    assert "Secret" not in shown.out + shown.err and str(alice) not in shown.out + shown.err
    assert tables(db, alice)["trades"] == 8


def test_applying_needs_the_expected_numbers(db, alice, app_url, capsys):
    seed(db, alice)
    assert clear_journal.main(["--user-id", str(alice), "--database-url", app_url, "--apply"]) == 2
    assert "--expect-accounts" in capsys.readouterr().err
    assert tables(db, alice)["trades"] == 8


def test_a_wrong_expectation_is_refused_with_exit_3(db, alice, app_url, capsys):
    seed(db, alice)
    args = [
        "--user-id",
        str(alice),
        "--database-url",
        app_url,
        "--apply",
        "--expect-accounts",
        "1",
        "--expect-trades",
        "9",
    ]
    assert clear_journal.main(args) == 3
    assert "refused" in capsys.readouterr().err
    assert tables(db, alice)["trades"] == 8


def test_the_right_expectation_empties_the_journal(db, alice, app_url, capsys):
    seed(db, alice)
    args = [
        "--user-id",
        str(alice),
        "--database-url",
        app_url,
        "--apply",
        "--expect-accounts",
        "1",
        "--expect-trades",
        "8",
    ]
    assert clear_journal.main(args) == 0
    assert "applied; removed 1 accounts, 8 trades" in capsys.readouterr().out
    assert tables(db, alice) == dict.fromkeys(TABLES, 0)


def test_a_person_who_does_not_exist_is_an_empty_journal_not_someone_elses(db, alice, app_url, capsys):
    seed(db, alice)
    stranger = "00000000-0000-4000-8000-000000000001"
    assert clear_journal.main(["--user-id", stranger, "--database-url", app_url]) == 0
    assert "would remove 0 accounts, 0 trades" in capsys.readouterr().out
    assert tables(db, alice)["trades"] == 8
