"""TradeSea imports on a real Postgres (tradejournal/pg/executions.py and the TradeSea half of pg/importer.py).

The golden figures are TradeSea's own realized P&L for 2026-11-16 (+$10,055.00) and 2026-11-18 (-$635.00 with $165.00
of charges), confirmed by the owner; the accounts here charge TradeSea's $5.00 per contract round trip. The small files
are made up, to put a position across two imports and an order inside a stored stretch, the cases that decide whether a
trade keeps its notes, tags and screenshots (docs/design/tradesea-import-plan.md, section 3.5).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pg_helpers import make_account

from tradejournal.pg import accounts, importer

D = Decimal
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
HEADER = '"Time","Symbol","Qty","Side","Order Type","Limit Price","Stop Price","Avg Price","Commission","Status"'
NOV16 = [
    "tradesea_2026-11-16_0937.csv",
    "tradesea_2026-11-16_0952.csv",
    "tradesea_2026-11-16_1002.csv",
    "tradesea_2026-11-16_1013_filled.csv",
]
NOV18_PAGES = ["tradesea_2026-11-18_page1_filled.csv", "tradesea_2026-11-18_page2_filled.csv"]
NOV18_OVERLAP = "tradesea_2026-11-18_0847_filled.csv"
# The three orders of 2026-11-16 that no export held, as TradeSea's Positions screen showed them.
NOV16_MISSING = [
    ("9:49:24", "1", "Buy", "27413.75"),
    ("9:49:43", "1", "Sell", "27407.5"),
    ("9:49:56", "1", "Sell", "27407.5"),
]
NINE = datetime(2026, 11, 20, 17, 0, tzinfo=UTC)  # 9:00 AM PST, for the made-up files


def fixture(name):
    return (name, (FIXTURES / name).read_bytes())


def orders(*specs, day="11/20/2026", name="orders.csv"):
    """A TradeSea file from (seconds after 9:00 AM PST, side, qty, price) or (clock time, qty, side, price) rows."""
    rows = []
    for spec in specs:
        if isinstance(spec[0], int):
            seconds, side, qty, price = spec
            at = datetime(2026, 1, 1, 9) + timedelta(seconds=seconds)
            clock = f"{at.hour}:{at.minute:02d}:{at.second:02d}"
        else:
            clock, qty, side, price = spec
        cells = (f"{day}, {clock} AM PST", "CME-Delayed:NQ", qty, side.title(), "Market", "", "", price, "", "Filled")
        rows.append(",".join(f'"{cell}"' for cell in cells))
    return (name, "\n".join([HEADER, *rows]).encode())


def run(conn, account_id, *files, dry_run=False):
    [report] = importer.import_files(conn, account_id, list(files), platform="tradesea", dry_run=dry_run).files
    return report


@pytest.fixture
def account(conn):
    """A TradeSea evaluation account charging what TradeSea charges: $5.00 per contract round trip."""
    created = make_account(conn, "Lucid 12", firm="Lucid")
    accounts.set_fee(conn, created.id, "*", D("5.00"))
    return created


def trades(conn, account_id=None):
    where, params = ("WHERE account_id = %s", (account_id,)) if account_id else ("", ())
    return conn.execute(f"SELECT * FROM trades {where} ORDER BY entry_at, id", params).fetchall()  # noqa: S608


def nets(conn, account_id=None):
    return [t["net_pnl"] for t in trades(conn, account_id)]


def count(conn, table):
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 (fixed names)


def add_journal(conn, trade_id, note):
    conn.execute("UPDATE trades SET notes = %s WHERE id = %s", (f"<p>{note}</p>", trade_id))
    group = conn.execute("SELECT id FROM tag_groups WHERE name = 'Setup'").fetchone()["id"]
    tag = conn.execute(
        "INSERT INTO tags (group_id, name) VALUES (%s, %s) RETURNING id", (group, f"tag {note}")
    ).fetchone()["id"]
    conn.execute("INSERT INTO trade_tags (trade_id, tag_id) VALUES (%s, %s)", (trade_id, tag))
    conn.execute(
        "INSERT INTO screenshots (trade_id, stored_name, original_name) VALUES (%s, %s, 'chart.png')",
        (trade_id, f"{trade_id}-{note}.png"),
    )


def journal(conn):
    """Each trade's notes, tags and screenshots, by trade id."""
    found = {}
    for t in trades(conn):
        tags = conn.execute(
            "SELECT tags.name FROM trade_tags JOIN tags ON tags.id = trade_tags.tag_id WHERE trade_id = %s ORDER BY 1",
            (t["id"],),
        )
        shots = conn.execute("SELECT stored_name FROM screenshots WHERE trade_id = %s ORDER BY 1", (t["id"],))
        found[t["id"]] = (t["notes"], [r["name"] for r in tags], [r["stored_name"] for r in shots])
    return found


# ---- the real files --------------------------------------------------------------------------------------------------


def test_both_pages_of_18_november_are_one_import_with_tradesea_realized_pnl(conn, account):
    report = run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    assert report.platform == "tradesea" and report.fatal is None
    assert report.filename == ", ".join(NOV18_PAGES)
    assert (report.rows_parsed, report.rows_added, report.duplicates, report.duplicates_in_file) == (17, 17, 0, 0)
    assert report.file_commission == D("165.0")
    assert report.open_positions == []
    assert report.trades_created == 5
    assert nets(conn) == [D("-1030.00"), D("1695.00"), D("-395.00"), D("2615.00"), D("-3520.00")]
    assert sum(nets(conn)) == D("-635.00")
    assert sum(t["fees"] for t in trades(conn)) == D("165.00")  # the fee setting matches what TradeSea charged
    [record] = conn.execute("SELECT * FROM imports").fetchall()
    assert (record["source"], record["source_timezone"], record["rows_added"]) == ("tradesea_orders", "PST", 17)
    assert count(conn, "executions") == 17 and count(conn, "import_executions") == 17
    assert count(conn, "import_fill_pairs") == 0  # TradeSea's fill pairs are worked out, not imported
    assert conn.execute("SELECT count(*) AS n FROM fill_pairs WHERE trade_id IS NULL").fetchone()["n"] == 0


def test_16_november_with_every_order_is_tradesea_realized_pnl_to_the_cent(conn, account):
    missing = orders(*NOV16_MISSING, day="11/16/2026", name="positions-screen.csv")
    report = run(conn, account.id, *(fixture(name) for name in NOV16), missing)
    assert report.duplicates_in_file == 10 and report.open_positions == []
    assert report.skipped_by_status == {"cancelled": 9}
    assert sum(nets(conn)) == D("10055.00")


def test_16_november_without_the_missing_orders_ends_holding_a_contract(conn, account):
    report = run(conn, account.id, *(fixture(name) for name in NOV16))
    [held] = report.open_positions
    assert (held.symbol, held.direction, held.qty) == ("NQ", "long", 1)
    held_orders = conn.execute("SELECT count(*) AS n FROM executions").fetchone()["n"]
    assert held_orders == report.rows_added  # the open order is stored, so the next import can close it


def test_the_same_pages_again_change_nothing_whatever_their_order(conn, account):
    first = run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    before = (conn.execute("SELECT id, trade_id FROM fill_pairs ORDER BY id").fetchall(), journal(conn))
    again = run(conn, account.id, *(fixture(name) for name in reversed(NOV18_PAGES)))
    assert (again.rows_added, again.duplicates, again.trades_built, again.trades_merged) == (0, 17, 0, 0)
    assert again.previous_imports == [first.import_id]  # the same pages, picked in another order
    assert (conn.execute("SELECT id, trade_id FROM fill_pairs ORDER BY id").fetchall(), journal(conn)) == before


def test_an_overlapping_export_imported_first_then_both_pages_gives_the_same_day(conn, account):
    early = run(conn, account.id, fixture(NOV18_OVERLAP))
    assert nets(conn) == [D("-395.00"), D("2615.00")]
    [one, two] = [t["id"] for t in trades(conn)]
    add_journal(conn, one, "early")
    add_journal(conn, two, "late")
    later = run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    assert (later.rows_added, later.duplicates) == (7, 10)
    assert sum(nets(conn)) == D("-635.00")
    kept = journal(conn)
    assert kept[one] == ("<p>early</p>", ["tag early"], [f"{one}-early.png"])  # the 8:47 trades kept their ids
    assert kept[two] == ("<p>late</p>", ["tag late"], [f"{two}-late.png"])
    assert early.import_id != later.import_id


# ---- positions that span imports, and orders inside a stored stretch ---------------------------------------------------


def test_a_position_held_from_one_import_to_the_next_is_paired_whichever_comes_first(db, alice, bob):
    opening = orders((0, "buy", "2", "27000"), (60, "sell", "1", "27010"), name="morning.csv")
    closing = orders((600, "sell", "1", "27020"), name="later.csv")
    results = []
    for person, files in ((alice, (opening, closing)), (bob, (closing, opening))):
        with db.tenant_session(person) as conn:
            account = make_account(conn, "Lucid 12")
            accounts.set_fee(conn, account.id, "*", D("5.00"))
            first = run(conn, account.id, files[0])
            second = run(conn, account.id, files[1])
            results.append((first, second, nets(conn)))
    (a_first, a_second, alice_nets), (b_first, b_second, bob_nets) = results
    [held] = a_first.open_positions
    assert (held.direction, held.qty, held.since) == ("long", 1, NINE)
    [held] = b_first.open_positions  # a sell alone reads as a short until the buy before it arrives
    assert (held.direction, held.qty) == ("short", 1)
    assert a_second.open_positions == [] and b_second.open_positions == []
    assert alice_nets == bob_nets == [D("590.00")]  # (10 + 20) points x $20, less 2 contracts at $5


def test_a_missed_order_inside_a_trade_splits_it_and_the_journal_stays_on_the_first_part(conn, account):
    run(conn, account.id, orders((0, "buy", "2", "27000"), (300, "sell", "2", "27010")))
    [trade] = trades(conn)
    add_journal(conn, trade["id"], "whole")
    # The page the first export missed: out at 1:00 and back in at 2:00. Flat in between, so two trades.
    report = run(conn, account.id, orders((60, "sell", "2", "27005"), (120, "buy", "2", "27001")))
    assert (report.trades_created, report.trades_extended, report.trades_merged, report.trades_removed) == (1, 1, 0, 0)
    first, second = trades(conn)
    assert first["id"] == trade["id"]
    assert (first["gross_pnl"], second["gross_pnl"]) == (D("200.00"), D("360.00"))
    assert journal(conn)[trade["id"]] == ("<p>whole</p>", ["tag whole"], [f"{trade['id']}-whole.png"])
    assert journal(conn)[second["id"]] == ("", [], [])


def test_an_order_before_two_trades_joins_them_and_both_journals_move_onto_one(conn, account):
    run(
        conn,
        account.id,
        orders(
            (0, "buy", "1", "27000"), (10, "sell", "1", "27001"), (20, "buy", "1", "27002"), (30, "sell", "1", "27003")
        ),
    )
    one, two = (t["id"] for t in trades(conn))
    add_journal(conn, one, "first")
    add_journal(conn, two, "second")
    # A buy at 8:59:55 that the first export left out: long from then on, never flat, so one trade. The second trade's
    # pair is replaced by one holding the first trade's buy, so the second trade is left with no pair of its own.
    report = run(conn, account.id, orders(("8:59:55", "1", "Buy", "26990")))
    assert (report.trades_created, report.trades_extended, report.trades_merged) == (0, 1, 1)
    [joined] = trades(conn)
    assert joined["id"] == one
    assert (joined["gross_pnl"], joined["net_pnl"]) == (D("280.00"), D("270.00"))  # 11 + 3 points, 2 contracts
    shots = sorted([f"{one}-first.png", f"{two}-second.png"])
    assert journal(conn) == {one: ("<p>first</p><br><p>second</p>", ["tag first", "tag second"], shots)}
    [held] = report.open_positions
    assert (held.direction, held.qty) == ("long", 1)


# ---- undo ------------------------------------------------------------------------------------------------------------


def test_undoing_the_only_import_removes_its_orders_pairs_and_trades(conn, account):
    report = run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    add_journal(conn, trades(conn)[0]["id"], "gone")
    undone = importer.undo_import(conn, report.import_id)
    assert (undone.rows_removed, undone.rows_kept, undone.trades_removed) == (17, 0, 5)
    assert undone.trades_with_journal_removed == 1
    for table in ("executions", "import_executions", "fill_pairs", "trades"):
        assert count(conn, table) == 0, table


def test_undo_keeps_the_orders_another_import_also_holds(conn, account):
    run(conn, account.id, fixture(NOV18_OVERLAP))
    both = run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    undone = importer.undo_import(conn, both.import_id)
    assert (undone.rows_removed, undone.rows_kept) == (7, 10)
    assert nets(conn) == [D("-395.00"), D("2615.00")]  # back to the 8:47 export alone


def test_undoing_the_missed_order_gives_back_the_two_trades_with_the_journal(conn, account):
    run(
        conn,
        account.id,
        orders(
            (0, "buy", "1", "27000"), (10, "sell", "1", "27001"), (20, "buy", "1", "27002"), (30, "sell", "1", "27003")
        ),
    )
    before = nets(conn)
    one, two = (t["id"] for t in trades(conn))
    add_journal(conn, one, "first")
    add_journal(conn, two, "second")
    missed = run(conn, account.id, orders(("8:59:55", "1", "Buy", "26990")))
    undone = importer.undo_import(conn, missed.import_id)
    assert (undone.rows_removed, undone.trades_with_journal_removed) == (1, 0)
    assert nets(conn) == before
    assert trades(conn)[0]["id"] == one  # the merged journal stays on the first trade; nothing is lost
    notes = [notes for notes, _, _ in journal(conn).values()]
    assert notes[0] == "<p>first</p><br><p>second</p>" and notes[1] == ""


def test_a_dry_run_undo_changes_nothing(conn, account):
    report = run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    preview = importer.undo_import(conn, report.import_id, dry_run=True)
    assert (preview.rows_removed, preview.trades_removed) == (17, 5)
    assert count(conn, "executions") == 17 and count(conn, "trades") == 5
    assert conn.execute("SELECT undone_at FROM imports").fetchone()["undone_at"] is None


# ---- previews, accounts, people ----------------------------------------------------------------------------------------


def test_a_preview_reports_what_the_import_would_do_and_saves_nothing(conn, account):
    files = [fixture(name) for name in NOV18_PAGES]
    preview = run(conn, account.id, *files, dry_run=True)
    assert (preview.rows_added, preview.trades_created, preview.import_id) == (17, 5, None)
    for table in ("executions", "import_executions", "fill_pairs", "trades", "imports"):
        assert count(conn, table) == 0, table
    real = run(conn, account.id, *files)
    assert (real.rows_added, real.trades_created, real.open_positions) == (17, 5, preview.open_positions)


def test_the_same_orders_in_a_second_account_are_kept_for_copy_traders(conn, account):
    files = [fixture(name) for name in NOV18_PAGES]
    run(conn, account.id, *files)
    copy = make_account(conn, "Lucid 13", firm="Lucid")
    accounts.set_fee(conn, copy.id, "*", D("5.00"))
    report = run(conn, copy.id, *files)
    assert (report.rows_added, report.duplicates, report.matches_other_accounts) == (17, 0, {"Lucid 12": 17})
    assert report.duplicates_other_accounts == {}  # nothing skipped, unlike Tradovate
    assert report.previous_imports == []  # the fingerprint check is per account
    assert sum(nets(conn, account.id)) == sum(nets(conn, copy.id)) == D("-635.00")


def test_another_persons_orders_are_invisible(db, alice, bob):
    files = [fixture(name) for name in NOV18_PAGES]
    with db.tenant_session(alice) as conn:
        run(conn, make_account(conn, "Lucid 12").id, *files)
    with db.tenant_session(bob) as conn:
        report = run(conn, make_account(conn, "Lucid 12").id, *files)
        assert (report.rows_added, report.matches_other_accounts) == (17, {})
        assert count(conn, "executions") == 17


def test_tradovate_and_tradesea_imports_live_side_by_side_in_one_account(conn, account):
    from helpers import SAMPLE, fixture_bytes

    run(conn, account.id, *(fixture(name) for name in NOV18_PAGES))
    [tradovate] = importer.import_files(conn, account.id, [(SAMPLE, fixture_bytes(SAMPLE))]).files
    assert (tradovate.rows_added, tradovate.trades_created) == (14, 8)
    assert count(conn, "trades") == 13
    assert {r["source"] for r in conn.execute("SELECT source FROM imports")} == {
        "tradovate_performance",
        "tradesea_orders",
    }


def test_a_file_that_is_not_tradesea_is_refused_and_nothing_is_stored(conn, account):
    from helpers import SAMPLE, fixture_bytes

    report = run(conn, account.id, (SAMPLE, fixture_bytes(SAMPLE)))
    assert report.fatal == "This is a Tradovate Performance export, not a TradeSea one."
    assert count(conn, "imports") == 0 and count(conn, "executions") == 0


def test_an_unknown_platform_is_a_programming_error(conn, account):
    with pytest.raises(ValueError, match="unknown platform"):
        importer.import_files(conn, account.id, [fixture(NOV18_OVERLAP)], platform="ninjatrader")
