"""Importing Roundturn's own import file (the Other platform route) on a real Postgres, through the importer and the API.

The golden file (`tests/other_files.py`) is five made-up round turns a chatbot wrote from another platform's export:
gross +$372.50, the file's fees $25.42, net +$347.08. The account charges $4.00 per contract round trip on everything,
so Roundturn's own fees are 8 contracts x $4.00 = $32.00 and the journal nets +$340.50: the file's fees are shown beside
the setting, never used.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from other_files import GOLDEN, HEADER, ROWS, file_of
from pg_helpers import api_client as api
from pg_helpers import make_account

from tradejournal.pg import accounts as pg_accounts
from tradejournal.pg import importer
from tradejournal.platforms import other

D = Decimal
PLATFORM = "other"
FILE = ("roundturn-import.csv", GOLDEN)


@pytest.fixture
def account(conn):
    created = make_account(conn, "Topstep 50K", firm="Other")
    pg_accounts.set_fee(conn, created.id, "*", D("4.00"))
    return created


def run(conn, account_id, *files, dry_run=False):
    [report] = importer.import_files(conn, account_id, list(files), platform=PLATFORM, dry_run=dry_run).files
    return report


def trades(conn, account_id):
    return conn.execute("SELECT * FROM trades WHERE account_id = %s ORDER BY entry_at, id", (account_id,)).fetchall()


def count(conn, table):
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 (fixed names)


# ---- the importer ----------------------------------------------------------------------------------------------------


def test_the_golden_file_imports_its_round_turns_as_trades_with_the_accounts_own_fees(conn, account):
    report = run(conn, account.id, FILE)
    assert report.platform == PLATFORM and report.fatal is None and report.errors == []
    assert (report.rows_parsed, report.rows_added, report.duplicates, report.trades_created) == (5, 5, 0, 5)
    assert (report.file_gross, report.file_fees, report.file_net) == (D("372.50"), D("25.42"), D("347.08"))
    assert (report.first_day, report.last_day) == (date(2026, 11, 18), date(2026, 11, 18))
    assert report.time_zone == "UTC-05:00" and report.open_positions == []
    stored = trades(conn, account.id)
    assert [(t["symbol"], t["direction"], t["total_qty"], t["gross_pnl"]) for t in stored] == [
        ("NQZ6", "long", 2, D("410.00")),
        ("NQZ6", "short", 1, D("-105.00")),
        ("MESZ6", "long", 3, D("17.50")),
        ("CLF7", "short", 1, D("200.00")),
        ("GCG7", "long", 1, D("-150.00")),
    ]
    assert sum(t["fees"] for t in stored) == D("32.00") and sum(t["net_pnl"] for t in stored) == D("340.50")
    assert count(conn, "executions") == 10
    [record] = conn.execute("SELECT source, rows_parsed, rows_added, source_timezone FROM imports").fetchall()
    assert dict(record) == {
        "source": other.SOURCE,
        "rows_parsed": 5,
        "rows_added": 5,
        "source_timezone": "UTC-05:00",
    }


def test_importing_the_same_trades_again_adds_nothing_even_converted_again_and_written_differently(conn, account):
    run(conn, account.id, FILE)
    again = run(conn, account.id, FILE)
    assert (again.rows_added, again.duplicates, again.trades_created) == (0, 5, 0)
    assert len(again.previous_imports) == 1  # the very same file, named in the preview
    # Another chat writes the same trades in UTC, with other decimals and another guess at the fees.
    rewritten = (
        GOLDEN.replace(b"2026-11-18T09:31:05-05:00", b"2026-11-18T14:31:05+00:00")
        .replace(b"2026-11-18T09:33:10-05:00", b"2026-11-18T14:33:10Z")
        .replace(b",21010.5,410.00,9.00,401.00", b",21010.50,410.00,8.00,402.00")
    )
    assert rewritten.count(b"Z,21010.50,410.00,8.00,402.00") == 1
    converted = run(conn, account.id, ("roundturn-import (1).csv", rewritten))
    assert (converted.rows_added, converted.duplicates) == (0, 5)
    assert count(conn, "trades") == 5 and count(conn, "executions") == 10


def test_a_file_with_a_refused_row_imports_the_rest_and_the_corrected_file_adds_only_that_row(conn, account):
    wrong = ROWS[3].replace("200.00,4.00,196.00", "20.00,4.00,16.00")  # crude oil written at $100 a point
    first = run(conn, account.id, ("first.csv", file_of(ROWS[0], ROWS[1], ROWS[2], wrong, ROWS[4])))
    assert [issue.line for issue in first.errors] == [5]
    assert first.errors[0].message.startswith("gross_pnl 20.00 is not what the prices make")
    assert (first.rows_parsed, first.rows_added, first.file_net) == (4, 4, D("151.08"))  # 347.08 - 196.00
    fixed = run(conn, account.id, ("fixed.csv", GOLDEN))
    assert (fixed.rows_added, fixed.duplicates, fixed.errors) == (1, 4, [])
    assert [t["symbol"] for t in trades(conn, account.id)] == ["NQZ6", "NQZ6", "MESZ6", "CLF7", "GCG7"]


def test_a_preview_saves_nothing_and_says_what_the_import_would_do(conn, account):
    report = run(conn, account.id, FILE, dry_run=True)
    assert (report.rows_added, report.trades_built, report.import_id) == (5, 5, None)
    assert count(conn, "executions") == count(conn, "trades") == count(conn, "imports") == 0


def test_undo_removes_the_round_turns_and_their_trades(conn, account):
    done = run(conn, account.id, FILE)
    undone = importer.undo_import(conn, done.import_id)
    assert (undone.rows_removed, undone.rows_kept, undone.trades_removed) == (5, 0, 5)
    assert count(conn, "executions") == count(conn, "trades") == count(conn, "fill_pairs") == 0


def test_a_file_that_is_not_roundturns_is_refused_and_nothing_is_stored(conn, account):
    report = run(conn, account.id, ("NinjaTrader Grid.csv", b"Instrument,Action,Quantity,Price,Time\n"))
    assert report.fatal == other.NOT_OURS and report.import_id is None
    assert count(conn, "imports") == count(conn, "executions") == 0


def test_the_same_trades_in_a_second_account_are_imported_there_and_named(conn, account):
    run(conn, account.id, FILE)
    copy = make_account(conn, "Topstep 50K copy", firm="Other")
    report = run(conn, copy.id, FILE)
    assert (report.rows_added, report.matches_other_accounts) == (5, {"Topstep 50K": 5})


def test_another_persons_round_turns_are_invisible(db, alice, bob):
    with db.tenant_session(alice) as conn:
        run(conn, make_account(conn, "Topstep 50K").id, FILE)
    with db.tenant_session(bob) as conn:
        report = run(conn, make_account(conn, "Topstep 50K").id, FILE)
        assert (report.rows_added, report.duplicates, report.matches_other_accounts) == (5, 0, {})
        assert count(conn, "executions") == 10 and count(conn, "imports") == 1


# ---- the API ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def account_id(db, alice):
    with db.tenant_session(alice) as conn:
        created = make_account(conn, "Topstep 50K", firm="Other")
        pg_accounts.set_fee(conn, created.id, "*", D("4.00"))
        return created.id


def send(client, path, account_id, files, platform=PLATFORM):
    return client.post(
        path,
        data={"account_id": str(account_id), "platform": platform},
        files=[("file", (name, body, "text/csv")) for name, body in files],
    )


def preview(client, account_id, files, **kwargs):
    response = send(client, "/api/v1/imports/preview", account_id, files, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_preview_gives_the_files_own_totals_and_dates_and_shows_times_as_the_file_wrote_them(db, alice, account_id):
    client = api(db, alice)
    body = preview(client, account_id, [FILE])
    assert (body["platform"], body["recognised"], body["fatal"]) == (PLATFORM, False, None)
    assert (body["file_gross"], body["file_fees"], body["file_net"]) == ("372.50", "25.42", "347.08")
    assert (body["first_day"], body["last_day"]) == ("2026-11-18", "2026-11-18")
    assert (body["rows_parsed"], body["rows_added"], body["trades_built"]) == (5, 5, 5)
    assert body["time_zone"] == "UTC-05:00"
    rows = body["rows"]
    assert rows[0]["bought_at"] == "11/18/2026 09:31:05" and rows[0]["sold_at"] == "11/18/2026 09:33:10"
    assert sum(D(r["pnl"]) for r in rows) == D("372.50")
    assert body["file_commission"] == "25.42"
    nq = next(check for check in body["commissions"] if check["root"] == "NQ")
    assert nq == {
        "root": "NQ",
        "round_trips": "3",
        "file_total": "13.50",
        "file_rate": "4.50",
        "schedule_rate": "4.00",
        "schedule_total": "12.00",
    }
    assert client.get("/api/v1/imports").json() == []  # a preview writes nothing


def test_times_across_a_daylight_saving_change_are_shown_in_the_csv_time_zone_setting(db, alice, account_id):
    summer = "1,NQZ6,long,1,2026-10-30T09:31:00-04:00,21000.00,2026-10-30T09:32:00-04:00,21001.00,20.00,0,20.00"
    body = preview(api(db, alice), account_id, [("both.csv", file_of(summer, ROWS[0]))])
    assert body["fatal"] is None and body["time_zone"] == "America/Los_Angeles"  # the setting's default
    assert (body["first_day"], body["last_day"]) == ("2026-10-30", "2026-11-18")


def test_the_import_and_the_history_name_the_other_platform(db, alice, account_id):
    client = api(db, alice)
    done = send(client, "/api/v1/imports", account_id, [FILE])
    assert done.status_code == 201, done.text
    assert (done.json()["platform"], done.json()["rows_added"], done.json()["trades_built"]) == (PLATFORM, 5, 5)
    assert [r["platform"] for r in client.get("/api/v1/imports").json()] == [PLATFORM]
    assert preview(client, account_id, [FILE])["rows_added"] == 0


def test_roundturns_file_dropped_on_the_page_is_recognised(db, alice, account_id):
    body = preview(api(db, alice), account_id, [FILE], platform="auto")
    assert (body["platform"], body["recognised"], body["fatal"]) == (PLATFORM, True, None)


def test_a_file_with_the_wrong_header_is_refused_in_the_preview_and_by_the_import(db, alice, account_id):
    client = api(db, alice)
    wrong = ("roundturn-import.csv", file_of(ROWS[0] + ",x", header=HEADER + ",account"))
    body = preview(client, account_id, [wrong], platform="auto")
    assert body["platform"] == PLATFORM and "it has account, which the format does not" in body["fatal"]
    refused = send(client, "/api/v1/imports", account_id, [wrong])
    assert refused.status_code == 422 and "it has account" in refused.json()["detail"]
    assert client.get("/api/v1/imports").json() == []


def test_another_platforms_export_chosen_as_other_is_refused_with_a_way_to_read_it_as_that_platform(
    db, alice, account_id
):
    from helpers import SAMPLE, fixture_bytes

    body = preview(api(db, alice), account_id, [(SAMPLE, fixture_bytes(SAMPLE))])
    assert body["suggested_platform"] == "tradovate"
    assert body["fatal"] == "This looks like a file from Tradovate, not Roundturn's import file (Other platform)."


def test_roundturns_file_chosen_as_tradesea_is_refused_with_a_way_to_read_it_as_other(db, alice, account_id):
    body = preview(api(db, alice), account_id, [FILE], platform="tradesea")
    assert body["suggested_platform"] == PLATFORM
    assert body["fatal"] == "This looks like a file from Roundturn's import file (Other platform), not TradeSea."


def test_several_files_are_refused(db, alice, account_id):
    body = preview(api(db, alice), account_id, [FILE, ("b.csv", GOLDEN)])
    assert body["fatal"].startswith("Choose one file at a time")
