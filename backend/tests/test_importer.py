from decimal import Decimal

import pytest

from helpers import SAMPLE, fixture_bytes, fixture_lines, make_csv
from tradejournal import accounts, db, importer, settings, store

D = Decimal
SAMPLE_FILE = (SAMPLE, fixture_bytes(SAMPLE))
MORNING_FILE = ("morning.csv", fixture_lines(SAMPLE, *range(2, 15)))  # all but the last row


def run_import(conn, account, *files, dry_run=False):
    return importer.import_files(conn, account.id, list(files), dry_run=dry_run).files


def count(conn, table, where="1 = 1", params=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]


def trades(conn):
    return conn.execute("SELECT * FROM trades ORDER BY entry_at").fetchall()


def net_total(conn):
    return sum((D(t["net_pnl"]) for t in trades(conn)), D("0"))


def add_journal(conn, trade_id, note):
    conn.execute("UPDATE trades SET notes = ? WHERE id = ?", (note, trade_id))
    conn.execute("INSERT OR IGNORE INTO tags (group_id, name) VALUES (1, 'Opening drive')")
    tag_id = conn.execute("SELECT id FROM tags WHERE name = 'Opening drive'").fetchone()[0]
    conn.execute("INSERT INTO trade_tags (trade_id, tag_id) VALUES (?, ?)", (trade_id, tag_id))
    conn.execute(
        "INSERT INTO screenshots (trade_id, stored_name, original_name, created_at) VALUES (?, ?, ?, ?)",
        (trade_id, f"{trade_id}-{note}.png", "chart.png", "2024-01-05T00:00:00Z"),
    )


def test_import_sample_builds_eight_trades(conn, account):
    [report] = run_import(conn, account, SAMPLE_FILE)
    assert report.import_id is not None
    assert (report.rows_parsed, report.rows_added, report.duplicates) == (14, 14, 0)
    assert (report.trades_created, report.trades_extended) == (8, 0)
    assert count(conn, "trades") == 8
    assert count(conn, "fill_pairs") == 14
    assert count(conn, "fill_pairs", "trade_id IS NULL") == 0
    assert net_total(conn) == D("835.00")  # 1035.00 gross - 50 contracts x $4

    first = trades(conn)[0]
    assert first["entry_at"] == "2024-01-05T14:30:00Z"
    assert first["trade_date"] == "2024-01-05"
    assert (first["gross_pnl"], first["fees"], first["net_pnl"]) == ("-475.00", "20.00", "-495.00")
    assert first["account_id"] == account.id


def test_reimport_is_deduplicated(conn, account):
    [first] = run_import(conn, account, SAMPLE_FILE)
    [second] = run_import(conn, account, SAMPLE_FILE)
    assert (second.rows_added, second.duplicates, second.trades_built) == (0, 14, 0)
    assert second.previous_imports == [first.import_id]
    assert count(conn, "trades") == 8
    assert count(conn, "fill_pairs") == 14


def test_dry_run_reports_but_saves_nothing(conn, account):
    [preview] = run_import(conn, account, SAMPLE_FILE, dry_run=True)
    assert (preview.rows_added, preview.trades_created, preview.import_id) == (14, 8, None)
    for table in ("trades", "fill_pairs", "imports", "import_fill_pairs"):
        assert count(conn, table) == 0


def test_files_in_one_import_dedupe_against_each_other(conn, account):
    reports = run_import(conn, account, ("part.csv", fixture_lines(SAMPLE, 2, 3, 4)), SAMPLE_FILE)
    assert [r.rows_added for r in reports] == [3, 11]
    assert [r.duplicates for r in reports] == [0, 3]
    assert count(conn, "trades") == 8


def test_overlapping_export_extends_trade_and_keeps_its_journal(conn, account):
    run_import(conn, account, MORNING_FILE)
    last = trades(conn)[-1]
    assert last["total_qty"] == 4  # the 1-lot exit on line 15 isn't in this file yet
    add_journal(conn, last["id"], "held for target")

    [report] = run_import(conn, account, SAMPLE_FILE)
    assert (report.rows_added, report.trades_created, report.trades_extended) == (1, 0, 1)
    assert count(conn, "trades") == 8
    extended = conn.execute("SELECT * FROM trades WHERE id = ?", (last["id"],)).fetchone()
    assert extended["total_qty"] == 5
    assert extended["gross_pnl"] == "175.00"
    assert extended["notes"] == "held for target"
    assert count(conn, "trade_tags", "trade_id = ?", (last["id"],)) == 1
    assert count(conn, "screenshots", "trade_id = ?", (last["id"],)) == 1


# Two separate 1-lot longs (X, Y) and a later-imported 1-lot (Z) that overlaps both,
# so the position never goes flat from X's entry to Y's exit.
ROW_X = "NQH4,-2,0,0.25,10,30,1,17000.00,17001.00,$20.00,01/10/2024 06:30:00,01/10/2024 06:30:10,10sec"
ROW_Z = "NQH4,-2,0,0.25,20,50,1,17000.50,17002.50,$40.00,01/10/2024 06:30:05,01/10/2024 06:30:25,20sec"
ROW_Y = "NQH4,-2,0,0.25,40,60,1,17002.00,17003.00,$20.00,01/10/2024 06:30:20,01/10/2024 06:30:30,10sec"


def test_bridging_row_merges_two_trades_and_their_journals(conn, account):
    run_import(conn, account, ("xy.csv", make_csv(ROW_X, ROW_Y)))
    first_id, second_id = (t["id"] for t in trades(conn))
    add_journal(conn, first_id, "first")
    add_journal(conn, second_id, "second")

    [report] = run_import(conn, account, ("z.csv", make_csv(ROW_Z)))
    assert (report.trades_created, report.trades_extended, report.trades_merged) == (0, 1, 1)
    [merged] = trades(conn)
    assert merged["id"] == first_id
    assert (merged["total_qty"], merged["max_position"], merged["gross_pnl"]) == (3, 2, "80.00")
    assert merged["notes"] == "first<br>second"
    assert count(conn, "trade_tags") == 1  # same tag on both: kept once
    assert count(conn, "screenshots", "trade_id = ?", (first_id,)) == 2


def test_undo_removes_what_the_import_added(conn, account):
    [report] = run_import(conn, account, SAMPLE_FILE)
    undo = importer.undo_import(conn, report.import_id)
    assert (undo.rows_removed, undo.rows_kept, undo.trades_removed) == (14, 0, 8)
    assert count(conn, "trades") == 0
    assert count(conn, "fill_pairs") == 0
    assert count(conn, "imports", "undone_at IS NOT NULL") == 1

    with pytest.raises(ValueError, match="already undone"):
        importer.undo_import(conn, report.import_id)
    [again] = run_import(conn, account, SAMPLE_FILE)
    assert again.rows_added == 14
    assert again.previous_imports == []  # the undone import no longer counts


def test_undo_keeps_rows_another_import_still_contains(conn, account):
    [morning] = run_import(conn, account, MORNING_FILE)
    [full] = run_import(conn, account, SAMPLE_FILE)
    undo = importer.undo_import(conn, morning.import_id)
    assert (undo.rows_removed, undo.rows_kept, undo.trades_removed) == (0, 13, 0)
    assert count(conn, "trades") == 8
    importer.undo_import(conn, full.import_id)
    assert count(conn, "trades") == 0
    assert count(conn, "fill_pairs") == 0


def test_undo_shrinks_an_extended_trade_back_and_keeps_its_journal(conn, account):
    run_import(conn, account, MORNING_FILE)
    last_id = trades(conn)[-1]["id"]
    add_journal(conn, last_id, "held for target")
    [full] = run_import(conn, account, SAMPLE_FILE)

    undo = importer.undo_import(conn, full.import_id)
    assert (undo.rows_removed, undo.trades_removed, undo.trades_changed) == (1, 0, 1)
    restored = conn.execute("SELECT * FROM trades WHERE id = ?", (last_id,)).fetchone()
    assert restored["total_qty"] == 4
    assert restored["notes"] == "held for target"


def test_undo_preview_warns_about_journal_entries(conn, account):
    [report] = run_import(conn, account, SAMPLE_FILE)
    add_journal(conn, trades(conn)[0]["id"], "revenge trade")
    preview = importer.undo_import(conn, report.import_id, dry_run=True)
    assert (preview.trades_removed, preview.trades_with_journal_removed) == (8, 1)
    assert count(conn, "trades") == 8
    assert count(conn, "imports", "undone_at IS NULL") == 1


def test_rows_already_in_another_account_are_skipped(conn, account):
    with db.transaction(conn):
        other = accounts.create_account(conn, "Lucid 01", firm="Lucid")
    run_import(conn, account, SAMPLE_FILE)
    [report] = importer.import_files(conn, other.id, [SAMPLE_FILE]).files
    assert report.rows_added == 0
    assert report.duplicates_other_accounts == {"Apex 07": 14}
    assert count(conn, "trades", "account_id = ?", (other.id,)) == 0


def test_rejected_file_creates_no_import(conn, account):
    [report] = run_import(conn, account, ("orders.csv", b'Working Orders\n"Account","Status"\n'))
    assert "order-history" in report.fatal
    assert report.import_id is None
    assert count(conn, "imports") == 0


def test_fee_change_recalculates_net_pnl(conn, account):
    run_import(conn, account, SAMPLE_FILE)
    with db.transaction(conn):
        accounts.set_fee(conn, account.id, "NQ", D("5.00"))
        assert store.recalculate_trades(conn, account.id) == 8
    assert net_total(conn) == D("785.00")


def test_trading_day_setting_recomputes_trade_dates(conn, account):
    run_import(conn, account, ("evening.csv", fixture_bytes("evening_session_2024-03-20.csv")))
    assert {t["trade_date"] for t in trades(conn)} == {"2024-03-20"}
    with db.transaction(conn):
        settings.set_setting(conn, "trading_day_timezone", "America/Los_Angeles")
        settings.set_setting(conn, "trading_day_start", "00:00")
        store.recalculate_trades(conn)
    assert {t["trade_date"] for t in trades(conn)} == {"2024-03-19"}


def test_timezone_setting_applies_to_new_imports(conn, account):
    with db.transaction(conn):
        settings.set_setting(conn, "timezone", "America/New_York")
    run_import(conn, account, SAMPLE_FILE)
    assert trades(conn)[0]["entry_at"] == "2024-01-05T11:30:00Z"
    assert conn.execute("SELECT source_timezone FROM imports").fetchone()[0] == "America/New_York"


def test_full_rebuild_changes_nothing_on_consistent_data(conn, account):
    run_import(conn, account, ("shorts.csv", fixture_bytes("chained_shorts_2024-03-13.csv")))
    before = [tuple(t) for t in trades(conn)]
    with db.transaction(conn):
        result = store.rebuild_account(conn, account.id)
    assert (result.created, result.updated, result.removed) == (set(), set(), set())
    assert [tuple(t) for t in trades(conn)] == before


def test_database_file_reopens_without_rerunning_migrations(tmp_path):
    path = tmp_path / "journal.db"
    db.connect(path).close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)
    assert count(conn, "tag_groups") == 3
    conn.close()
