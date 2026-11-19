"""Importing TradeSea's pages through the API: the `platform` field, several files in one request, and what the preview
adds for orders (docs/design/tradesea-import-plan.md, sections 2 and 3.8).

The golden day is 2026-11-18: two pages of TradeSea's Filled orders that TradeSea totals at -$635.00 realized P&L with
$165.00 of commissions (66 contracts bought or sold, so $5.00 per contract round trip).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from helpers import SAMPLE, fixture_bytes
from pg_helpers import api_client as api
from pg_helpers import api_client_with_services as api_with_services
from pg_helpers import make_account
from test_pg_tradesea import FIXTURES, HEADER, NOV18_PAGES, orders

from tradejournal.pg import accounts as pg_accounts
from tradejournal.pg import settings as pg_settings
from tradejournal.platforms import tradesea

D = Decimal
PAGES = [(name, (FIXTURES / name).read_bytes()) for name in NOV18_PAGES]
TRADOVATE_FILE = ("performance.csv", fixture_bytes(SAMPLE))


@pytest.fixture
def account_id(db, alice):
    with db.tenant_session(alice) as conn:
        return make_account(conn, "Lucid 12", firm="Lucid").id


def send(client, path, account_id, files, platform=None):
    data = {"account_id": str(account_id)}
    if platform is not None:
        data["platform"] = platform
    return client.post(path, data=data, files=[("file", (name, body, "text/csv")) for name, body in files])


def preview(client, account_id, files, platform="tradesea"):
    response = send(client, "/api/v1/imports/preview", account_id, files, platform)
    assert response.status_code == 200, response.text
    return response.json()


def trades(client):
    return client.get("/api/v1/trades", params={"show_archived": "true", "limit": 100}).json()["items"]


# ---- the preview ----------------------------------------------------------------------------------------------------


def test_both_pages_preview_as_one_import_with_the_orders_their_pairs_and_the_files_zone(db, alice, account_id):
    client = api(db, alice)
    body = preview(client, account_id, PAGES)
    assert body["fatal"] is None and body["platform"] == "tradesea" and body["recognised"] is False
    assert body["file_count"] == 2 and body["file_name"] == ", ".join(NOV18_PAGES)
    assert body["file_bytes"] == sum(len(data) for _, data in PAGES)
    assert (body["rows_parsed"], body["rows_added"], body["duplicates"]) == (17, 17, 0)
    assert body["time_zone"] == "PST" and body["open_positions"] == [] and body["one_page"] is False
    assert body["file_commission"] == "165.00" and body["errors"] == []
    # The table shows the fill pairs the orders make, oldest first, with times as the file wrote them (PST).
    rows = body["rows"]
    assert (
        rows and {r["status"] for r in rows} == {"new"} and [r["line"] for r in rows] == list(range(1, len(rows) + 1))
    )
    # The rows' P&L is before commission: less the files' $165.00, it is TradeSea's realized P&L for the day.
    assert sum(D(r["pnl"]) for r in rows) - D(body["file_commission"]) == D("-635.00")
    assert min(r["bought_at"] for r in rows).startswith("11/18/2026 ")
    assert trades(client) == [] and client.get("/api/v1/imports").json() == []  # a preview writes nothing


def test_the_preview_then_the_import_do_the_same_thing(db, alice, account_id):
    client = api(db, alice)
    promised = preview(client, account_id, PAGES)
    done = send(client, "/api/v1/imports", account_id, PAGES, "tradesea")
    assert done.status_code == 201, done.text
    record = done.json()
    assert record["platform"] == "tradesea" and record["file_name"] == ", ".join(NOV18_PAGES)
    assert (record["rows_added"], record["trades_built"]) == (promised["rows_added"], promised["trades_built"])
    assert len(trades(client)) == promised["trades_built"]
    again = preview(client, account_id, PAGES)  # the same pages again: every order and every pair already stored
    assert (again["rows_added"], again["duplicates"], again["trades_built"]) == (0, 17, 0)
    assert {r["status"] for r in again["rows"]} == {"duplicate"} and len(again["rows"]) == len(promised["rows"])


def test_the_history_says_which_platform_each_import_came_from(db, alice, account_id):
    client = api(db, alice)
    send(client, "/api/v1/imports", account_id, [TRADOVATE_FILE])  # no platform field: Tradovate, as before
    send(client, "/api/v1/imports", account_id, PAGES, "tradesea")
    assert [r["platform"] for r in client.get("/api/v1/imports").json()] == ["tradesea", "tradovate"]


def test_a_position_left_open_is_named_with_when_it_was_opened(db, alice, account_id):
    opened = orders(("9:51:28", "1", "Buy", "27000"))
    body = preview(api(db, alice), account_id, [opened])
    assert body["open_positions"] == [{"symbol": "NQ", "direction": "long", "qty": 1, "since": "11/20/2026 09:51:28"}]
    assert body["rows"] == [] and body["rows_added"] == 1 and body["trades_built"] == 0


def test_orders_that_did_not_fill_are_counted_by_status(db, alice, account_id):
    filled = '"11/20/2026, 9:00:00 AM PST","CME-Delayed:NQ","1","Buy","Market","","","27000","2.5","Filled"'
    cancelled = '"11/20/2026, 9:00:05 AM PST","CME-Delayed:NQ","1","Sell","Limit","27100","","","","Cancelled"'
    rejected = '"11/20/2026, 9:00:06 AM PST","CME-Delayed:NQ","1","Sell","Stop","","26900","","","Rejected (margin)"'
    file = (
        "day.csv",
        "\n".join([HEADER, filled, cancelled, cancelled.replace("9:00:05", "9:00:07"), rejected]).encode(),
    )
    body = preview(api(db, alice), account_id, [file])
    assert body["skipped_by_status"] == {"cancelled": 2, "rejected": 1} and body["rows_parsed"] == 1


def test_one_full_page_is_flagged_so_the_other_pages_can_be_added(db, alice, account_id):
    page = orders(*[(n * 10, "Buy" if n % 2 == 0 else "Sell", "1", "27000") for n in range(10)])
    body = preview(api(db, alice), account_id, [page])
    assert body["one_page"] is True
    assert all(w["message"] != tradesea.ONE_PAGE for w in body["warnings"])  # said once, by `one_page`
    assert preview(api(db, alice), account_id, PAGES)["one_page"] is False  # page 2 has 7 orders: the last page


def test_the_commissions_are_compared_with_the_accounts_own_fee_setting(db, alice, account_id):
    client = api(db, alice)
    [check] = preview(client, account_id, PAGES)["commissions"]
    assert check == {
        "root": "NQ",
        "round_trips": "33",
        "file_total": "165.00",
        "file_rate": "5.00",
        "schedule_rate": "0.00",  # a new account charges nothing until its owner sets a rate
        "schedule_total": "0.00",
    }
    with db.tenant_session(alice) as conn:
        pg_accounts.set_fee(conn, account_id, "*", D("4.50"))
    [check] = preview(client, account_id, PAGES)["commissions"]
    assert (check["schedule_rate"], check["schedule_total"]) == ("4.50", "148.50")


def test_the_same_orders_in_another_account_are_a_note_not_a_skip(db, alice, account_id):
    client = api(db, alice)
    with db.tenant_session(alice) as conn:
        copy = make_account(conn, "Lucid 13", firm="Lucid").id
    send(client, "/api/v1/imports", account_id, PAGES, "tradesea")
    body = preview(client, copy, PAGES)
    assert body["matches_other_accounts"] == {"Lucid 12": 17} and body["rows_added"] == 17
    assert body["duplicates_other_accounts"] == {}


def test_a_time_zone_the_file_does_not_name_falls_back_to_the_setting_and_says_so(db, alice, account_id):
    client = api(db, alice)
    with db.tenant_session(alice) as conn:
        pg_settings.set_csv_timezone(conn, "America/Chicago")
    row = '"11/20/2026, 9:00:00 AM","CME-Delayed:NQ","1","Buy","Market","","","27000","","Filled"'
    body = preview(client, account_id, [("day.csv", f"{HEADER}\n{row}\n{row.replace('Buy', 'Sell')}".encode())])
    assert body["time_zone"] == "America/Chicago" and body["warnings"]
    assert body["rows"][0]["bought_at"] == "11/20/2026 09:00:00"  # shown in the zone it was read in


# ---- choosing the platform ------------------------------------------------------------------------------------------


def test_tradovate_is_still_the_default_and_its_preview_says_so(db, alice, account_id):
    body = preview(api(db, alice), account_id, [TRADOVATE_FILE], platform=None)
    assert body["platform"] == "tradovate" and body["fatal"] is None and body["rows_parsed"] == 14
    assert body["time_zone"] and body["commissions"] == [] and body["file_count"] == 1


@pytest.mark.parametrize(
    ("asked", "files", "suggested", "fatal"),
    [
        ("tradesea", [TRADOVATE_FILE], "tradovate", "This looks like a file from Tradovate, not TradeSea."),
        ("tradovate", PAGES, "tradesea", "These look like files from TradeSea, not Tradovate."),
        ("tradovate", PAGES[:1], "tradesea", "This looks like a file from TradeSea, not Tradovate."),
    ],
)
def test_files_from_the_other_platform_are_refused_with_a_way_to_read_them_as_that(
    db, alice, account_id, asked, files, suggested, fatal
):
    client = api(db, alice)
    body = preview(client, account_id, files, platform=asked)
    assert (body["fatal"], body["suggested_platform"], body["platform"]) == (fatal, suggested, asked)
    assert body["rows"] == [] and body["rows_added"] == 0
    refused = send(client, "/api/v1/imports", account_id, files, asked)
    assert refused.status_code == 422 and refused.json()["detail"] == fatal
    assert preview(client, account_id, files, platform=suggested)["fatal"] is None  # the button's second try works
    assert client.get("/api/v1/imports").json() == []


def test_dropped_files_are_read_as_the_platform_their_header_names(db, alice, account_id):
    client = api(db, alice)
    pages = preview(client, account_id, PAGES, platform="auto")
    assert (pages["platform"], pages["recognised"], pages["fatal"]) == ("tradesea", True, None)
    one = preview(client, account_id, [TRADOVATE_FILE], platform="auto")
    assert (one["platform"], one["recognised"], one["rows_parsed"]) == ("tradovate", True, 14)
    done = send(client, "/api/v1/imports", account_id, PAGES, "auto")
    assert done.status_code == 201 and done.json()["platform"] == "tradesea"


@pytest.mark.parametrize("platform", ["auto", "tradesea", "tradovate"])
def test_files_from_two_platforms_together_are_refused(db, alice, account_id, platform):
    body = preview(api(db, alice), account_id, [PAGES[0], TRADOVATE_FILE], platform=platform)
    assert body["fatal"] == "These files come from different platforms. Choose files from one platform at a time."


def test_a_file_from_no_platform_names_what_roundturn_reads(db, alice, account_id):
    body = preview(api(db, alice), account_id, [("notes.csv", b"name,age\nalice,31\n")], platform="auto")
    assert body["fatal"].startswith(
        "Roundturn reads Tradovate's Performance report, TradeSea's Orders export (the Filled view), the Round Turn "
        "Monitor's log from NinjaTrader, and its own import file. This file is none of them. For any other platform, "
        "choose Other platform on the Import page"
    )
    assert body["suggested_platform"] is None


def test_tradovate_takes_one_file_at_a_time(db, alice, account_id):
    client = api(db, alice)
    two = [TRADOVATE_FILE, ("again.csv", TRADOVATE_FILE[1])]
    for platform in ("tradovate", "auto"):
        assert preview(client, account_id, two, platform)["fatal"] == (
            "Tradovate's Performance report is one file. Choose one file at a time."
        )
    assert send(client, "/api/v1/imports", account_id, two, "tradovate").status_code == 422


def test_an_unknown_platform_is_a_422(db, alice, account_id):
    response = send(api(db, alice), "/api/v1/imports/preview", account_id, PAGES, "ninjatrader")
    assert response.status_code == 422


# ---- limits, privacy and other people -------------------------------------------------------------------------------


def test_too_many_files_or_too_many_bytes_together_are_refused(db, alice, account_id, monkeypatch):
    from tradejournal.api.routers import imports as imports_router

    client = api(db, alice)
    monkeypatch.setattr(imports_router, "MAX_FILES", 1)
    assert send(client, "/api/v1/imports/preview", account_id, PAGES, "tradesea").status_code == 422
    monkeypatch.setattr(imports_router, "MAX_FILES", 50)
    monkeypatch.setattr(imports_router, "MAX_IMPORT_BYTES", len(PAGES[0][1]) + 10)  # each fits, the two do not
    refused = send(client, "/api/v1/imports/preview", account_id, PAGES, "tradesea")
    assert refused.status_code == 413 and "together" in refused.json()["detail"]


def test_an_import_of_pages_is_recorded_as_counts_and_never_names_the_files(db, alice, account_id):
    client, services = api_with_services(db, alice)
    send(client, "/api/v1/imports", account_id, PAGES, "tradesea")
    [event] = [e for e in services.audit.events if e["action"] == "import"]
    assert set(event["meta"]) == {"import_id", "rows_added", "trades_built"} and event["meta"]["rows_added"] == 17
    assert "page1" not in str(services.audit.events)


def test_nobody_can_preview_or_import_pages_into_another_persons_account(db, alice, bob, account_id):
    intruder = api(db, bob)
    for platform in ("tradesea", "tradovate"):  # refused before or after the files are read: a 404 either way
        assert send(intruder, "/api/v1/imports/preview", account_id, PAGES, platform).status_code == 404
        assert send(intruder, "/api/v1/imports", account_id, PAGES, platform).status_code == 404
    assert api(db, alice).get("/api/v1/imports").json() == []
