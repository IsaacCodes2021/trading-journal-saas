"""The importer and store tests (test_importer.py) run against Postgres, plus what only Postgres can say."""

import threading
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from helpers import SAMPLE, fixture_bytes, fixture_lines, make_csv
from pg_helpers import make_account, record_statements

from tradejournal.pg import accounts, importer, settings, store

D = Decimal
SAMPLE_FILE = (SAMPLE, fixture_bytes(SAMPLE))
MORNING_FILE = ("morning.csv", fixture_lines(SAMPLE, *range(2, 15)))  # all but the last row


def run_import(conn, account, *files, dry_run=False):
    return importer.import_files(conn, account.id, list(files), dry_run=dry_run).files


def count(conn, table, where="TRUE", params=()):
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params).fetchone()["n"]


def trades(conn):
    return conn.execute("SELECT * FROM trades ORDER BY entry_at, id").fetchall()


def net_total(conn):
    return sum((t["net_pnl"] for t in trades(conn)), D("0"))


def add_journal(conn, trade_id, note):
    conn.execute("UPDATE trades SET notes = %s WHERE id = %s", (note, trade_id))
    group_id = conn.execute("SELECT id FROM tag_groups WHERE name = 'Setup'").fetchone()["id"]
    conn.execute("INSERT INTO tags (group_id, name) VALUES (%s, 'Opening drive') ON CONFLICT DO NOTHING", (group_id,))
    tag_id = conn.execute("SELECT id FROM tags WHERE name = 'Opening drive'").fetchone()["id"]
    conn.execute("INSERT INTO trade_tags (trade_id, tag_id) VALUES (%s, %s)", (trade_id, tag_id))
    conn.execute(
        "INSERT INTO screenshots (trade_id, stored_name, original_name) VALUES (%s, %s, 'chart.png')",
        (trade_id, f"{trade_id}-{note}.png"),
    )


# ---- ported from test_importer.py ------------------------------------------------------------------------------


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
    assert first["entry_at"] == datetime(2024, 1, 5, 14, 30, tzinfo=UTC)
    assert first["trade_date"] == date(2024, 1, 5)
    assert (first["gross_pnl"], first["fees"], first["net_pnl"]) == (D("-475.00"), D("20.00"), D("-495.00"))
    assert str(first["gross_pnl"]) == "-475.00"  # the column keeps two decimals, so the API can send it as is
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
    extended = conn.execute("SELECT * FROM trades WHERE id = %s", (last["id"],)).fetchone()
    assert extended["total_qty"] == 5
    assert extended["gross_pnl"] == D("175.00")
    assert extended["notes"] == "held for target"
    assert count(conn, "trade_tags", "trade_id = %s", (last["id"],)) == 1
    assert count(conn, "screenshots", "trade_id = %s", (last["id"],)) == 1


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
    assert (merged["total_qty"], merged["max_position"], merged["gross_pnl"]) == (3, 2, D("80.00"))
    assert merged["notes"] == "first<br>second"
    assert count(conn, "trade_tags") == 1  # same tag on both: kept once
    assert count(conn, "screenshots", "trade_id = %s", (first_id,)) == 2


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
    restored = conn.execute("SELECT * FROM trades WHERE id = %s", (last_id,)).fetchone()
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
    other = accounts.create_account(conn, "Lucid 01", firm="Lucid")
    run_import(conn, account, SAMPLE_FILE)
    [report] = importer.import_files(conn, other.id, [SAMPLE_FILE]).files
    assert report.rows_added == 0
    assert report.duplicates_other_accounts == {"Apex 07": 14}
    assert count(conn, "trades", "account_id = %s", (other.id,)) == 0


def test_rejected_file_creates_no_import(conn, account):
    [report] = run_import(conn, account, ("orders.csv", b'Working Orders\n"Account","Status"\n'))
    assert "order-history" in report.fatal
    assert report.import_id is None
    assert count(conn, "imports") == 0


def test_fee_change_recalculates_net_pnl(conn, account):
    run_import(conn, account, SAMPLE_FILE)
    accounts.set_fee(conn, account.id, "NQ", D("5.00"))
    assert store.recalculate_trades(conn, account.id) == 8
    assert net_total(conn) == D("785.00")


def test_trading_day_setting_recomputes_trade_dates(conn, account):
    run_import(conn, account, ("evening.csv", fixture_bytes("evening_session_2024-03-20.csv")))
    assert {t["trade_date"] for t in trades(conn)} == {date(2024, 3, 20)}
    settings.set_setting(conn, "trading_day_timezone", "America/Los_Angeles")
    settings.set_setting(conn, "trading_day_start", "00:00")
    store.recalculate_trades(conn)
    assert {t["trade_date"] for t in trades(conn)} == {date(2024, 3, 19)}


def test_timezone_setting_applies_to_new_imports(conn, account):
    settings.set_setting(conn, "timezone", "America/New_York")
    run_import(conn, account, SAMPLE_FILE)
    assert trades(conn)[0]["entry_at"] == datetime(2024, 1, 5, 11, 30, tzinfo=UTC)
    assert conn.execute("SELECT source_timezone FROM imports").fetchone()["source_timezone"] == "America/New_York"


def test_full_rebuild_changes_nothing_on_consistent_data(conn, account):
    run_import(conn, account, ("shorts.csv", fixture_bytes("chained_shorts_2024-03-13.csv")))
    before = trades(conn)
    result = store.rebuild_account(conn, account.id)
    assert (result.created, result.updated, result.removed) == (set(), set(), set())
    assert trades(conn) == before


# ---- what only Postgres can say --------------------------------------------------------------------------------


def test_fill_ids_are_stored_as_text_refs_and_read_back_as_integers(conn, account):
    run_import(conn, account, SAMPLE_FILE)
    row = conn.execute("SELECT source, buy_ref, sell_ref FROM fill_pairs ORDER BY id LIMIT 1").fetchone()
    assert row["source"] == "tradovate_performance"
    assert row["buy_ref"].isdigit() and row["sell_ref"].isdigit()
    [pair] = store.trade_fill_pairs(conn, trades(conn)[0]["id"])[:1]
    assert isinstance(pair.buy_fill_id, int) and isinstance(pair.sell_fill_id, int)


def test_prices_are_stored_without_trailing_zeros(conn, account):
    row = "NQH4,-2,0,0.25,10,30,1,17000.500,17001.0,$20.00,01/10/2024 06:30:00,01/10/2024 06:30:10,10sec"
    run_import(conn, account, ("p.csv", make_csv(row)))
    stored = conn.execute("SELECT buy_price::text AS buy, sell_price::text AS sell FROM fill_pairs").fetchone()
    assert (stored["buy"], stored["sell"]) == ("17000.5", "17001")
    trade = trades(conn)[0]
    assert (str(trade["avg_entry"]), str(trade["avg_exit"])) == ("17000.5", "17001")


def test_an_import_that_fails_part_way_changes_nothing_and_the_session_stays_usable(conn, account, monkeypatch):
    real = store.rebuild_trades
    calls = []

    def fail_on_second_file(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "rebuild_trades", fail_on_second_file)
    with pytest.raises(RuntimeError, match="boom"):
        run_import(conn, account, ("xy.csv", make_csv(ROW_X)), ("z.csv", make_csv(ROW_Z)))
    for table in ("imports", "fill_pairs", "trades", "import_fill_pairs"):
        assert count(conn, table) == 0, table  # the first file's work went with it
    monkeypatch.setattr(store, "rebuild_trades", real)
    [report] = run_import(conn, account, SAMPLE_FILE)  # and the same connection carries on
    assert report.rows_added == 14


def _many_one_lot_trades(rows: int, *, day: str = "01/10/2024", first_id: int = 1000) -> bytes:
    """`rows` separate 1-lot trades a minute apart, each with its own fill ids."""
    lines = []
    for i in range(rows):
        hour, minute = 6 + i // 60, i % 60
        lines.append(
            f"NQH4,-2,0,0.25,{first_id + 2 * i},{first_id + 1 + 2 * i},1,17000.00,17001.00,$20.00,"
            f"{day} {hour:02d}:{minute:02d}:00,{day} {hour:02d}:{minute:02d}:10,10sec"
        )
    return make_csv(*lines)


def test_the_statements_for_a_file_do_not_grow_with_its_rows(conn, account):
    """The SQLite importer ran about three statements per row, which over a network misses the 10 s target."""
    log = record_statements(conn)
    run_import(conn, account, ("small.csv", _many_one_lot_trades(20)))
    small = list(log)
    log.clear()
    [report] = run_import(conn, account, ("big.csv", _many_one_lot_trades(300)))
    big = list(log)
    assert report.rows_added == 300 - 20  # the first 20 rows are the same fills
    assert count(conn, "trades") == 300
    assert len(big) == len(small), (len(small), len(big))
    assert len(big) < 25, big


def test_a_second_file_for_the_same_symbol_does_not_scale_either(conn, account):
    run_import(conn, account, ("first.csv", _many_one_lot_trades(100)))
    log = record_statements(conn)
    run_import(conn, account, ("second.csv", _many_one_lot_trades(200, day="01/11/2024", first_id=9000)))
    assert len(log) < 25, log
    assert count(conn, "trades") == 300


# ---- the per-user lock -----------------------------------------------------------------------------------------


def _in_thread(errors, target):
    def run():
        try:
            target()
        except BaseException as error:  # noqa: BLE001  (reported to the test, which fails on it)
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


# Everything that regroups a user's trades must wait for that user's lock, and only for that user's.
REGROUPING_OPERATIONS = {
    "import_files": lambda session, ctx: importer.import_files(session, ctx["account"].id, [SAMPLE_FILE]),
    "undo_import": lambda session, ctx: importer.undo_import(session, ctx["import_id"]),
    "rebuild_account": lambda session, ctx: store.rebuild_account(session, ctx["account"].id),
    # Nothing to regroup, so only the lock taken before reading the pair list can make this wait.
    "rebuild_empty_account": lambda session, ctx: store.rebuild_account(session, ctx["empty"].id),
    "recalculate_trades": lambda session, ctx: store.recalculate_trades(session, ctx["account"].id),
    "rebuild_trades": lambda session, ctx: store.rebuild_trades(session, ctx["account"].id, "NQH4", ctx["pair_ids"]),
}


@pytest.mark.parametrize("operation", REGROUPING_OPERATIONS)
def test_regrouping_waits_for_the_users_lock_and_other_users_do_not(db, alice, bob, operation):
    with db.tenant_session(alice) as setup:
        alices = make_account(setup)
        [report] = run_import(setup, alices, SAMPLE_FILE)
        pair_ids = [row["id"] for row in setup.execute("SELECT id FROM fill_pairs WHERE symbol = 'NQH4'")]
        empty = make_account(setup, "Empty 01")
    context = {"account": alices, "import_id": report.import_id, "pair_ids": pair_ids, "empty": empty}
    with db.tenant_session(bob) as setup:
        bobs = make_account(setup)

    errors: list = []
    holding, release, finished, bob_done = (threading.Event() for _ in range(4))

    def hold_alices_lock():
        with db.tenant_session(alice) as holder:
            store.lock_user(holder)
            holding.set()
            release.wait(30)

    def regroup_for_alice():
        with db.tenant_session(alice) as session:
            REGROUPING_OPERATIONS[operation](session, context)
        finished.set()

    def import_for_bob():
        with db.tenant_session(bob) as session:
            assert importer.import_files(session, bobs.id, [SAMPLE_FILE]).files[0].rows_added == 14
        bob_done.set()

    holder = _in_thread(errors, hold_alices_lock)
    assert holding.wait(10)
    waiting = _in_thread(errors, regroup_for_alice)
    bobs_thread = _in_thread(errors, import_for_bob)
    assert bob_done.wait(10), "Bob was held up by Alice's lock"
    assert not finished.wait(1.0), f"Alice's {operation} ran while her lock was held"

    release.set()
    holder.join(10)
    waiting.join(30)
    bobs_thread.join(10)
    assert finished.is_set()
    assert not errors, errors


def test_overlapping_imports_at_once_end_the_same_as_one_after_another(db, alice):
    files = [MORNING_FILE, SAMPLE_FILE, ("part.csv", fixture_lines(SAMPLE, 2, 3, 4))]
    with db.tenant_session(alice) as setup:
        target = make_account(setup)

    errors: list = []
    start = threading.Barrier(len(files))

    def import_one(file):
        def run():
            start.wait(10)
            with db.tenant_session(alice) as session:
                importer.import_files(session, target.id, [file])

        return run

    threads = [_in_thread(errors, import_one(file)) for file in files]
    for thread in threads:
        thread.join(60)
    assert not errors, errors

    with db.tenant_session(alice) as session:
        got = [(t["entry_at"], t["total_qty"], t["gross_pnl"], t["fill_pair_count"]) for t in trades(session)]
        assert count(session, "fill_pairs") == 14
        assert count(session, "fill_pairs", "trade_id IS NULL") == 0
        assert count(session, "imports") == 3
    assert len(got) == 8
    assert sum(pairs for *_, pairs in got) == 14  # no pair counted in two trades, none dropped


# ---- tenancy ---------------------------------------------------------------------------------------------------


def test_two_users_can_import_the_same_export(db, alice, bob):
    for user in (alice, bob):
        with db.tenant_session(user) as session:
            mine = make_account(session)
            [report] = run_import(session, mine, SAMPLE_FILE)
            assert (report.rows_added, report.duplicates, report.trades_created) == (14, 0, 8)
    with db.tenant_session(alice) as session:
        assert (count(session, "fill_pairs"), count(session, "trades")) == (14, 8)  # Bob's rows are invisible


def test_bob_cannot_import_into_or_undo_alices_things(db, alice, bob):
    with db.tenant_session(alice) as session:
        alices = make_account(session)
        [report] = run_import(session, alices, SAMPLE_FILE)
    with db.tenant_session(bob) as session:
        with pytest.raises(LookupError):
            importer.import_files(session, alices.id, [SAMPLE_FILE])
        with pytest.raises(LookupError):
            importer.undo_import(session, report.import_id)
        with pytest.raises(LookupError):
            store.rebuild_trades(session, alices.id, "NQH4", [1])
        assert importer.list_imports(session) == []
    with db.tenant_session(alice) as session:
        assert count(session, "trades") == 8


def test_list_imports_is_newest_first_and_can_filter_by_account(conn, account):
    other = accounts.create_account(conn, "Lucid 01", firm="Lucid")
    [first] = run_import(conn, account, MORNING_FILE)
    [second] = importer.import_files(conn, other.id, [("r.csv", fixture_bytes("reversal_2023-11-20.csv"))]).files
    everything = importer.list_imports(conn)
    assert [row["id"] for row in everything] == [second.import_id, first.import_id]
    assert [row["account_name"] for row in everything] == ["Lucid 01", "Apex 07"]
    assert [row["id"] for row in importer.list_imports(conn, account.id)] == [first.import_id]
    assert everything[1]["rows_added"] == 13 and everything[1]["trades_affected"] == first.trades_built


# ---- store helpers ---------------------------------------------------------------------------------------------


def test_rebuild_with_an_unknown_pair_id_is_an_error(conn, account):
    with pytest.raises(LookupError, match="999999"):
        store.rebuild_trades(conn, account.id, "NQH4", [999_999])
    assert store.rebuild_trades(conn, account.id, "NQH4", []).created == set()


def test_the_lock_can_be_taken_twice_in_one_transaction(conn):
    store.lock_user(conn)
    store.lock_user(conn)  # re-entrant: a function that locks may call another that locks


def test_journal_trade_ids_picks_out_the_trades_with_journal_entries(conn, account):
    run_import(conn, account, SAMPLE_FILE)
    ids = [t["id"] for t in trades(conn)]
    assert store.journal_trade_ids(conn, ids) == set()
    add_journal(conn, ids[2], "note")
    assert store.journal_trade_ids(conn, ids) == {ids[2]}
    assert store.has_journal(conn, ids[2]) and not store.has_journal(conn, ids[3])
    assert store.journal_trade_ids(conn, []) == set()


def test_the_database_refuses_a_fill_pair_stored_twice(conn, account):
    run_import(conn, account, SAMPLE_FILE)
    with pytest.raises(psycopg.errors.UniqueViolation), conn.transaction():
        conn.execute(
            """INSERT INTO fill_pairs (account_id, buy_ref, sell_ref, symbol, tick_size, price_format, price_format_type,
                   qty, buy_price, sell_price, pnl, bought_at, sold_at, duration_raw, duration_seconds, direction)
               SELECT account_id, buy_ref, sell_ref, symbol, tick_size, price_format, price_format_type,
                   qty, buy_price, sell_price, pnl, bought_at, sold_at, duration_raw, duration_seconds, direction
               FROM fill_pairs LIMIT 1"""
        )
