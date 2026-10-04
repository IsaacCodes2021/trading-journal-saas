"""Importing through the API against a real Postgres: previews that match the real thing, history and undo."""

from __future__ import annotations

import csv
import io

import pytest
from helpers import HEADER, SAMPLE, fixture_bytes, fixture_lines
from pg_helpers import api_client as api
from pg_helpers import api_client_with_services as api_with_services
from pg_helpers import make_account

from tradejournal.api.routers import imports as imports_router
from tradejournal.pg import journal as pg_journal
from tradejournal.pg import settings as pg_settings

SAMPLE_FILE = fixture_bytes(SAMPLE)  # 14 fill pairs that make 8 trades
MORNING_FILE = fixture_lines(SAMPLE, *range(2, 15))  # all but the last row
TOO_BIG = 9_999_999_999_999_999_999


@pytest.fixture
def two_accounts(db, alice):
    with db.tenant_session(alice) as conn:
        return make_account(conn, "Apex 07").id, make_account(conn, "Apex 08").id


def upload(client, path, account_id, data=SAMPLE_FILE, name="export.csv"):
    return client.post(path, data={"account_id": str(account_id)}, files={"file": (name, data, "text/csv")})


def trade_count(client) -> int:
    return client.get("/api/v1/trades", params={"show_archived": "true"}).json()["total"]


# ---- the preview ----------------------------------------------------------------------------------------------------


def test_the_preview_says_what_the_import_will_do_and_saves_nothing(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    preview = upload(client, "/api/v1/imports/preview", account_id)
    assert preview.status_code == 200
    body = preview.json()
    assert body["file_name"] == "export.csv" and body["file_bytes"] == len(SAMPLE_FILE) and body["fatal"] is None
    assert (body["rows_parsed"], body["rows_added"], body["duplicates"], body["duplicates_in_file"]) == (14, 14, 0, 0)
    assert body["trades_built"] == 8 and body["errors"] == [] and body["duplicates_other_accounts"] == {}
    assert trade_count(client) == 0 and client.get("/api/v1/imports").json() == []  # a preview writes nothing
    done = upload(client, "/api/v1/imports", account_id)
    record = done.json()
    assert done.status_code == 201
    assert (record["rows_added"], record["trades_built"]) == (body["rows_added"], body["trades_built"])  # as promised
    assert trade_count(client) == 8


def test_every_row_has_a_status_and_the_counts_follow_it(db, alice, two_accounts):
    client, mine, other = api(db, alice), two_accounts[0], two_accounts[1]
    upload(client, "/api/v1/imports", mine, MORNING_FILE)  # 13 of the 14 rows are in the first account
    same = upload(client, "/api/v1/imports/preview", mine).json()
    statuses = [r["status"] for r in same["rows"]]
    assert statuses.count("duplicate") == 13 and statuses.count("new") == 1
    assert (same["rows_added"], same["duplicates"], same["trades_built"] > 0) == (1, 13, True)
    elsewhere = upload(client, "/api/v1/imports/preview", other).json()
    assert [r["status"] for r in elsewhere["rows"]].count("other_account") == 13
    assert {r["account"] for r in elsewhere["rows"] if r["status"] == "other_account"} == {"Apex 07"}
    assert elsewhere["duplicates_other_accounts"] == {"Apex 07": 13} and elsewhere["rows_added"] == 1
    assert all(r["account"] is None for r in elsewhere["rows"] if r["status"] == "new")


def test_a_row_shows_the_file_its_times_exactly_as_the_file_wrote_them(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    first = next(csv.DictReader(io.StringIO(SAMPLE_FILE.decode())))
    for zone_name in ("America/Los_Angeles", "America/Chicago", "Europe/London"):
        with db.tenant_session(alice) as conn:
            pg_settings.set_csv_timezone(conn, zone_name)  # the file is read in this zone ...
        row = upload(client, "/api/v1/imports/preview", account_id).json()["rows"][0]
        # ... and shown in it again, so the wall clock the person sees is the one in their own file.
        assert (row["bought_at"], row["sold_at"]) == (first["boughtTimestamp"], first["soldTimestamp"]), zone_name
    assert row["line"] == 2 and row["symbol"] == first["symbol"] and row["qty"] == int(first["qty"])
    assert isinstance(row["buy_price"], str) and isinstance(row["pnl"], str)


def test_the_table_shows_only_the_first_rows_but_the_counts_are_for_the_whole_file(
    db, alice, two_accounts, monkeypatch
):
    monkeypatch.setattr(imports_router, "PREVIEW_ROWS_SHOWN", 5)
    body = upload(api(db, alice), "/api/v1/imports/preview", two_accounts[0]).json()
    assert len(body["rows"]) == 5 and body["rows_shown_limit"] == 5 and body["rows_parsed"] == 14


def test_a_file_that_is_not_a_performance_export_is_refused_in_words(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    junk = b"name,age\nalice,31\n"
    preview = upload(client, "/api/v1/imports/preview", account_id, junk)
    assert preview.status_code == 200 and preview.json()["fatal"] and preview.json()["rows_parsed"] == 0
    refused = upload(client, "/api/v1/imports", account_id, junk)
    assert refused.status_code == 422 and refused.json()["detail"] == preview.json()["fatal"].rstrip(".") + "."
    assert upload(client, "/api/v1/imports", account_id, b"").status_code == 422
    assert client.get("/api/v1/imports").json() == [] and trade_count(client) == 0


def test_rows_that_cannot_be_used_are_listed_with_their_line_numbers(db, alice, two_accounts):
    broken = SAMPLE_FILE + b"NQH4,garbage\n"
    body = upload(api(db, alice), "/api/v1/imports/preview", two_accounts[0], broken).json()
    assert body["rows_parsed"] == 14 and len(body["errors"]) == 1
    assert body["errors"][0]["line"] == 16 and body["errors"][0]["message"]  # the header is line 1
    assert HEADER.startswith("symbol")


# ---- import, history and undo -----------------------------------------------------------------------------------------


def test_an_import_is_recorded_and_importing_the_same_file_again_adds_nothing(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    first = upload(client, "/api/v1/imports", account_id, name="C:\\charts\\week 1.csv").json()
    assert first["file_name"] == "week 1.csv"  # a folder path is dropped, whichever machine wrote it
    assert first["account"] == {"id": account_id, "name": "Apex 07", "firm": "Apex", "account_type": "evaluation"}
    assert (first["rows_added"], first["trades_built"], first["undone_at"]) == (14, 8, None)
    again = upload(client, "/api/v1/imports", account_id).json()
    assert (again["rows_added"], again["trades_built"]) == (0, 0) and again["id"] != first["id"]
    assert [i["id"] for i in client.get("/api/v1/imports").json()] == [again["id"], first["id"]]  # newest first
    assert trade_count(client) == 8


def test_undoing_an_import_shows_what_it_will_remove_then_removes_it_and_cannot_be_repeated(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    record = upload(client, "/api/v1/imports", account_id).json()
    first_trade = client.get("/api/v1/trades", params={"limit": 1}).json()["items"][0]["id"]
    with db.tenant_session(alice) as conn:
        pg_journal.set_notes(conn, first_trade, "<p>keep me?</p>")
    base = f"/api/v1/imports/{record['id']}"
    preview = client.post(f"{base}/undo-preview")
    assert preview.status_code == 200
    assert preview.json() == {
        "import_id": record["id"],
        "rows_removed": 14,
        "rows_kept": 0,
        "trades_removed": 8,
        "trades_with_journal_removed": 1,  # the person is told the note would go with its trade
        "trades_changed": 0,
    }
    assert trade_count(client) == 8 and client.get("/api/v1/imports").json()[0]["undone_at"] is None  # only a preview
    done = client.post(f"{base}/undo")
    assert done.status_code == 200 and done.json() == preview.json()
    assert trade_count(client) == 0
    assert client.get("/api/v1/imports").json()[0]["undone_at"] is not None  # kept in the history, marked
    for path in ("undo", "undo-preview"):
        again = client.post(f"{base}/{path}")
        assert again.status_code == 409 and "already undone" in again.json()["detail"]


def test_rows_another_import_also_holds_stay_when_one_is_undone(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    morning = upload(client, "/api/v1/imports", account_id, MORNING_FILE).json()
    full = upload(client, "/api/v1/imports", account_id).json()
    undone = client.post(f"/api/v1/imports/{full['id']}/undo").json()
    assert undone["rows_kept"] == 13 and undone["rows_removed"] == 1  # the morning import still holds 13 of the 14
    assert trade_count(client) > 0
    assert client.post(f"/api/v1/imports/{morning['id']}/undo").json()["rows_removed"] == 13
    assert trade_count(client) == 0


# ---- what the audit log is told -----------------------------------------------------------------------------------------


def test_an_import_and_an_undo_are_recorded_as_counts_and_never_name_the_file(db, alice, two_accounts):
    client, services = api_with_services(db, alice)
    record = upload(client, "/api/v1/imports", two_accounts[0], name="my secret strategy week.csv").json()
    assert services.audit.events == [
        {
            "user_id": alice,
            "action": "import",
            "ip_hash": services.audit.events[0]["ip_hash"],
            "meta": {"import_id": record["id"], "rows_added": 14, "trades_built": 8},
        }
    ]
    assert services.audit.events[0]["ip_hash"]  # the address is kept only as a keyed hash
    assert "secret strategy" not in str(services.audit.events)
    client.post(f"/api/v1/imports/{record['id']}/undo")
    assert [(e["action"], e["meta"]) for e in services.audit.events[1:]] == [
        ("import_undone", {"import_id": record["id"], "rows_removed": 14, "trades_removed": 8})
    ]


def test_a_second_import_of_the_same_file_is_recorded_too_with_nothing_added(db, alice, two_accounts):
    client, services = api_with_services(db, alice)
    upload(client, "/api/v1/imports", two_accounts[0])
    upload(client, "/api/v1/imports", two_accounts[0])
    assert [(e["action"], e["meta"]["rows_added"]) for e in services.audit.events] == [("import", 14), ("import", 0)]


def test_a_preview_changes_nothing_so_it_is_not_recorded(db, alice, two_accounts):
    client, services = api_with_services(db, alice)
    record = upload(client, "/api/v1/imports", two_accounts[0]).json()
    services.audit.events.clear()
    assert upload(client, "/api/v1/imports/preview", two_accounts[0]).status_code == 200
    assert client.post(f"/api/v1/imports/{record['id']}/undo-preview").status_code == 200
    assert services.audit.events == []


def test_a_refused_import_or_undo_is_not_recorded_as_one_that_happened(db, alice, bob, two_accounts):
    client, services = api_with_services(db, alice)
    assert (
        upload(client, "/api/v1/imports", two_accounts[0], data=b"not,a,performance,export\n1,2,3,4\n").status_code
        == 422
    )
    assert client.post("/api/v1/imports/999999/undo").status_code == 404
    record = upload(client, "/api/v1/imports", two_accounts[0]).json()
    services.audit.events.clear()
    as_bob, bobs = api_with_services(db, bob)
    assert as_bob.post(f"/api/v1/imports/{record['id']}/undo").status_code == 404  # someone else's
    assert upload(as_bob, "/api/v1/imports", two_accounts[0]).status_code == 404
    assert bobs.audit.events == [] and services.audit.events == []


# ---- whose files, and what is refused ---------------------------------------------------------------------------------


def test_nobody_can_import_into_preview_or_undo_another_persons_things(db, alice, bob, two_accounts):
    mine = two_accounts[0]
    record = upload(api(db, alice), "/api/v1/imports", mine).json()
    as_bob = api(db, bob)
    for path in ("/api/v1/imports/preview", "/api/v1/imports"):
        refused = upload(as_bob, path, mine)
        assert refused.status_code == 404 and refused.json()["detail"] == "That account does not exist."
    for path in ("undo", "undo-preview"):
        assert as_bob.post(f"/api/v1/imports/{record['id']}/{path}").status_code == 404
    assert as_bob.get("/api/v1/imports").json() == []
    assert trade_count(api(db, alice)) == 8 and api(db, alice).get("/api/v1/imports").json()[0]["undone_at"] is None


@pytest.mark.parametrize("account_id", ["abc", "0", "-1", str(TOO_BIG), ""])
def test_a_bad_account_id_is_a_422_and_a_missing_file_too(db, alice, two_accounts, account_id):
    client = api(db, alice)
    for path in ("/api/v1/imports/preview", "/api/v1/imports"):
        response = client.post(
            path, data={"account_id": account_id}, files={"file": ("x.csv", SAMPLE_FILE, "text/csv")}
        )
        assert response.status_code == 422, (path, account_id)
    no_file = client.post(
        "/api/v1/imports", data={"account_id": str(two_accounts[0])}, files={"other": ("o.txt", b"x", "text/plain")}
    )
    assert no_file.status_code == 422  # a multipart request with no `file` part
    form = client.post("/api/v1/imports", data={"account_id": str(two_accounts[0])})
    assert form.status_code == 415  # a plain form is neither JSON nor multipart: refused by the CSRF layer


def test_a_file_over_the_limit_is_refused_before_anything_is_read_into_the_database(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    huge = b"x" * (imports_router.MAX_IMPORT_BYTES + 1)
    for path in ("/api/v1/imports/preview", "/api/v1/imports"):
        response = upload(client, path, account_id, huge)
        assert response.status_code == 413, path
    assert client.get("/api/v1/imports").json() == []


def test_an_import_without_the_csrf_token_changes_nothing(db, alice, two_accounts):
    client, account_id = api(db, alice), two_accounts[0]
    response = client.post(
        "/api/v1/imports",
        data={"account_id": str(account_id)},
        files={"file": ("x.csv", SAMPLE_FILE, "text/csv")},
        headers={"x-csrf-token": "wrong"},
    )
    assert response.status_code == 403 and trade_count(client) == 0


@pytest.mark.parametrize("path", ["/api/v1/imports/%d/undo", "/api/v1/imports/%d/undo-preview"])
def test_an_id_too_big_for_the_database_is_a_bad_request(db, alice, path):
    assert api(db, alice).post(path % TOO_BIG).status_code == 422
