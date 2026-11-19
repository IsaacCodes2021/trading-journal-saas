"""Importing the Round Turn Monitor's log (NinjaTrader 8) on a real Postgres, through the importer and the API
(docs/design/round-turn-monitor-import-plan.md, sections 2 and 3).

The golden day is one Lucid account's 2026-11-18 in the real log: 18 round turns, +$1,395.00 gross. The add-on's own net
is +$1,367.00 after $28.00 of commission, but its first 11 round turns show no commission (the rate was set in its
Settings part-way through the session). Roundturn charges every trade from the account's fee setting, so with $3.50 per
NQ contract the 19 contracts cost $66.50 and the day nets +$1,328.50, $38.50 below the add-on.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pg_helpers import api_client as api
from pg_helpers import make_account

from tradejournal.pg import accounts as pg_accounts
from tradejournal.pg import importer
from tradejournal.pg import settings as pg_settings
from tradejournal.platforms import round_turn_monitor as rtm

D = Decimal
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LOG = ("RoundTurnLog.csv", (FIXTURES / "rtm_log_2026-11-18.csv").read_bytes())
EXPORT = ("RoundTurnExport_2026-11-18.csv", (FIXTURES / "rtm_export_2026-11-18.csv").read_bytes())
LUCID = "LFE0000000000001"
PLATFORM = "round_turn_monitor"


def log_lines(*accounts: str, last: int | None = None) -> bytes:
    """The real log cut to some accounts' rows (every account when none are named), or to its first `last` rows."""
    header, *rows = LOG[1].decode().splitlines()
    kept = [r for r in rows if not accounts or r.split(",")[1] in accounts][:last]
    return "\r\n".join([header, *kept, ""]).encode()


def renamed(account: str, to: str) -> bytes:
    return LOG[1].replace(account.encode(), to.encode())


@pytest.fixture
def lucid(conn):
    """The Lucid account, charging $3.50 per NQ contract round trip, with times read in Pacific time."""
    pg_settings.set_csv_timezone(conn, "America/Los_Angeles")
    created = make_account(conn, "Lucid 12", firm="Lucid")
    pg_accounts.set_fee(conn, created.id, "NQ", D("3.50"))
    return created


def run(conn, account_id, *files, log_account=None, dry_run=False):
    [report] = importer.import_files(
        conn, account_id, list(files), platform=PLATFORM, log_account=log_account, dry_run=dry_run
    ).files
    return report


def trades(conn, account_id=None):
    where, params = ("WHERE account_id = %s", (account_id,)) if account_id else ("", ())
    return conn.execute(f"SELECT * FROM trades {where} ORDER BY entry_at, id", params).fetchall()  # noqa: S608


def count(conn, table):
    return conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]  # noqa: S608 (fixed names)


# ---- the importer ----------------------------------------------------------------------------------------------------


def test_the_real_log_imports_the_lucid_accounts_day_and_leaves_sim101_out(conn, lucid):
    report = run(conn, lucid.id, LOG)
    assert report.platform == PLATFORM and report.fatal is None and report.log_account == LUCID
    assert [(a.name, a.round_turns, a.practice) for a in report.log_accounts] == [
        (LUCID, 18, False),
        ("Sim101", 5, True),
    ]
    assert (report.rows_parsed, report.rows_added, report.duplicates, report.trades_created) == (18, 18, 0, 18)
    assert report.errors == [] and report.warnings == [] and report.open_positions == []
    assert report.time_zone == "America/Los_Angeles" and report.file_commission == D("28.00")
    assert [(c.root, c.sides, c.commission, c.rate) for c in report.commissions] == [("NQ", 38, D("28.00"), D("3.50"))]
    stored = trades(conn)
    assert len(stored) == 18 and {t["symbol"] for t in stored} == {"NQZ6"}
    assert {t["contract_month"] for t in stored} == {"2026-12"}
    assert sum(t["gross_pnl"] for t in stored) == D("1395.00")  # the add-on's gross, to the cent
    assert sum(t["fees"] for t in stored) == D("66.50")  # 19 contracts at the account's $3.50
    assert sum(t["net_pnl"] for t in stored) == D("1328.50")
    # The add-on's day starts at 15:00 Pacific, as Roundturn's does: the three round turns of the evening before count.
    assert {t["trade_date"] for t in stored} == {date(2026, 11, 18)}
    assert stored[-1]["total_qty"] == 2 and stored[-1]["avg_entry"] == D("27136.375")
    [record] = conn.execute("SELECT * FROM imports").fetchall()
    assert (record["source"], record["source_timezone"]) == ("round_turn_monitor_log", "America/Los_Angeles")
    assert count(conn, "executions") == 36 and count(conn, "import_fill_pairs") == 0


def test_choosing_the_growing_log_again_adds_only_the_new_round_turns_and_keeps_the_journal(conn, lucid):
    first = run(conn, lucid.id, ("RoundTurnLog.csv", log_lines(LUCID, last=10)))
    assert (first.rows_parsed, first.trades_created) == (10, 10)
    kept = trades(conn)[0]["id"]
    conn.execute("UPDATE trades SET notes = '<p>kept</p>' WHERE id = %s", (kept,))
    later = run(conn, lucid.id, LOG)
    assert (later.rows_parsed, later.rows_added, later.duplicates) == (18, 8, 10)
    assert (later.trades_created, later.trades_extended, later.trades_removed) == (8, 0, 0)
    assert conn.execute("SELECT notes FROM trades WHERE id = %s", (kept,)).fetchone()["notes"] == "<p>kept</p>"
    again = run(conn, lucid.id, LOG)
    assert (again.rows_added, again.trades_built) == (0, 0) and again.previous_imports == [later.import_id]
    assert len(trades(conn)) == 18


def test_a_log_of_several_real_accounts_asks_which_one_and_remembers_the_answer(conn, lucid):
    two = (
        "RoundTurnLog.csv",
        LOG[1] + log_lines(LUCID).replace(LUCID.encode(), b"LFE0000000000002").split(b"\r\n", 1)[1],
    )
    asked = run(conn, lucid.id, two)
    assert asked.fatal == "This log has round turns from 3 accounts. Choose which one to import into this account."
    assert [a.name for a in asked.log_accounts] == [LUCID, "LFE0000000000002", "Sim101"]
    assert asked.log_account is None and count(conn, "imports") == 0
    chosen = run(conn, lucid.id, two, log_account="LFE0000000000002")
    assert (chosen.fatal, chosen.log_account, chosen.rows_parsed) == (None, "LFE0000000000002", 18)
    remembered = run(conn, lucid.id, two)  # no choice sent: the account already holding its round turns
    assert remembered.log_account == "LFE0000000000002" and remembered.rows_added == 0
    here = {a.name: a.here for a in remembered.log_accounts}
    assert here == {LUCID: 0, "LFE0000000000002": 18, "Sim101": 0}

    # Another Roundturn account skips the account this one already holds: the other real account is the only fresh one.
    other = make_account(conn, "Lucid 13", firm="Lucid")
    nxt = run(conn, other.id, two)
    assert nxt.log_account == LUCID
    assert {a.name: a.elsewhere for a in nxt.log_accounts}["LFE0000000000002"] == "Lucid 12"


def test_an_account_the_log_does_not_have_is_refused_with_the_list(conn, lucid):
    report = run(conn, lucid.id, LOG, log_account="LFE9")
    assert report.fatal == "This log has no round turns from the account LFE9. Choose one of the accounts it has."
    assert len(report.log_accounts) == 2 and count(conn, "imports") == 0


def test_practice_is_imported_when_asked_or_when_it_is_all_the_log_has(conn, lucid):
    sim = run(conn, lucid.id, LOG, log_account="Sim101")
    assert (sim.log_account, sim.rows_parsed, sim.trades_created) == ("Sim101", 5, 5)
    practice = make_account(conn, "Practice")
    only = run(conn, practice.id, ("RoundTurnLog.csv", log_lines("Sim101")))
    assert only.log_account == "Sim101" and only.matches_other_accounts == {"Lucid 12": 5}


def test_undo_removes_the_round_turns_orders_pairs_and_trades(conn, lucid):
    report = run(conn, lucid.id, LOG)
    undone = importer.undo_import(conn, report.import_id)
    assert (undone.rows_removed, undone.rows_kept, undone.trades_removed) == (18, 0, 18)
    for table in ("executions", "import_executions", "fill_pairs", "trades"):
        assert count(conn, table) == 0, table


def test_a_preview_saves_nothing_and_says_what_the_import_does(conn, lucid):
    preview = run(conn, lucid.id, LOG, dry_run=True)
    assert (preview.rows_added, preview.trades_created, preview.import_id) == (18, 18, None)
    for table in ("executions", "fill_pairs", "trades", "imports"):
        assert count(conn, table) == 0, table


def test_the_export_file_is_refused_and_nothing_is_stored(conn, lucid):
    report = run(conn, lucid.id, EXPORT)
    assert report.fatal == rtm.EXPORT_FILE and report.log_accounts == []
    assert count(conn, "imports") == 0 and count(conn, "executions") == 0


# ---- the API ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def account_id(db, alice):
    with db.tenant_session(alice) as conn:
        pg_settings.set_csv_timezone(conn, "America/Los_Angeles")
        return make_account(conn, "Lucid 12", firm="Lucid").id


def send(client, path, account_id, files, platform=PLATFORM, log_account=None):
    data = {"account_id": str(account_id), "platform": platform}
    if log_account is not None:
        data["log_account"] = log_account
    return client.post(path, data=data, files=[("file", (name, body, "text/csv")) for name, body in files])


def preview(client, account_id, files, **kwargs):
    response = send(client, "/api/v1/imports/preview", account_id, files, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_preview_lists_the_logs_accounts_and_reads_the_real_one(db, alice, account_id):
    client = api(db, alice)
    body = preview(client, account_id, [LOG])
    assert body["fatal"] is None and body["platform"] == PLATFORM and body["log_account"] == LUCID
    assert body["log_accounts"] == [
        {"name": LUCID, "round_turns": 18, "practice": False, "here": 0, "elsewhere": None},
        {"name": "Sim101", "round_turns": 5, "practice": True, "here": 0, "elsewhere": None},
    ]
    assert (body["rows_parsed"], body["rows_added"], body["trades_built"]) == (18, 18, 18)
    assert body["time_zone"] == "America/Los_Angeles" and body["file_commission"] == "28.00"
    rows = body["rows"]
    assert len(rows) == 18 and {r["status"] for r in rows} == {"new"} and {r["symbol"] for r in rows} == {"NQZ6"}
    assert sum(D(r["pnl"]) for r in rows) == D("1395.00")
    assert rows[0]["bought_at"] == "11/17/2026 22:29:58"  # as the log wrote it
    [check] = body["commissions"]
    # The log's usual rate, not $28.00 over 19 contracts: its first 11 round turns show no commission.
    assert check == {
        "root": "NQ",
        "round_trips": "19",
        "file_total": "28.00",
        "file_rate": "3.50",
        "schedule_rate": "0.00",
        "schedule_total": "0.00",
    }
    assert client.get("/api/v1/imports").json() == []  # a preview writes nothing


def test_the_import_takes_the_chosen_account_and_the_history_names_the_platform(db, alice, account_id):
    client = api(db, alice)
    done = send(client, "/api/v1/imports", account_id, [LOG], log_account="Sim101")
    assert done.status_code == 201, done.text
    assert (done.json()["platform"], done.json()["rows_added"], done.json()["trades_built"]) == (PLATFORM, 5, 5)
    assert [r["platform"] for r in client.get("/api/v1/imports").json()] == [PLATFORM]
    body = preview(client, account_id, [LOG])  # the account it already holds is now the one picked
    assert body["log_account"] == "Sim101" and body["rows_added"] == 0


def test_a_log_of_several_real_accounts_needs_a_choice_before_it_imports(db, alice, account_id):
    client = api(db, alice)
    two = ("RoundTurnLog.csv", renamed("Sim101", "LFE0000000000002"))
    body = preview(client, account_id, [two])
    assert body["fatal"] == importer.CHOOSE_ACCOUNT.format(count=2) and body["log_account"] is None
    assert [a["name"] for a in body["log_accounts"]] == [
        "LFE0000000000002",
        LUCID,
    ]  # in the log's order and body["rows"] == []
    refused = send(client, "/api/v1/imports", account_id, [two])
    assert refused.status_code == 422 and refused.json()["detail"] == importer.CHOOSE_ACCOUNT.format(count=2)
    chosen = preview(client, account_id, [two], log_account="LFE0000000000002")
    assert chosen["fatal"] is None and chosen["rows_parsed"] == 5
    assert send(client, "/api/v1/imports", account_id, [two], log_account="LFE0000000000002").status_code == 201


def test_the_log_dropped_on_the_page_is_recognised_and_the_export_file_is_refused_in_words(db, alice, account_id):
    client = api(db, alice)
    body = preview(client, account_id, [LOG], platform="auto")
    assert (body["platform"], body["recognised"], body["fatal"]) == (PLATFORM, True, None)
    export = preview(client, account_id, [EXPORT], platform="auto")
    assert (export["platform"], export["fatal"]) == (PLATFORM, rtm.EXPORT_FILE)


def test_the_log_chosen_as_another_platform_is_refused_with_a_way_to_read_it_as_the_log(db, alice, account_id):
    client = api(db, alice)
    body = preview(client, account_id, [LOG], platform="tradesea")
    assert body["fatal"] == "This looks like a file from the Round Turn Monitor (NinjaTrader), not TradeSea."
    assert body["suggested_platform"] == PLATFORM
    from helpers import SAMPLE, fixture_bytes

    tradovate = preview(client, account_id, [("performance.csv", fixture_bytes(SAMPLE))])
    assert tradovate["fatal"] == "This looks like a file from Tradovate, not the Round Turn Monitor (NinjaTrader)."
    assert tradovate["suggested_platform"] == "tradovate"


def test_the_log_and_its_overflow_file_import_together(db, alice, account_id):
    client = api(db, alice)
    main = ("RoundTurnLog.csv", log_lines(LUCID, last=12))
    header = LOG[1].split(b"\r\n", 1)[0]
    overflow = ("RoundTurnLog_overflow.csv", header + b"\r\n" + log_lines(LUCID).split(b"\r\n", 13)[13])
    body = preview(client, account_id, [main, overflow])
    assert (body["file_count"], body["rows_parsed"], body["duplicates_in_file"], body["fatal"]) == (2, 18, 0, None)
    assert sum(D(r["pnl"]) for r in body["rows"]) == D("1395.00")


def test_someone_elses_account_is_not_found(db, alice, bob, account_id):
    response = send(api(db, bob), "/api/v1/imports/preview", account_id, [LOG])
    assert response.status_code == 404
