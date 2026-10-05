"""The reports route on a real Postgres (F-P4): the numbers agree with the dashboard, the filters are the dashboard's, the
hour follows the person's own timezone, tags are the person's own, and nobody reads anyone else's journal."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pg_helpers import add_trade, make_account
from pg_helpers import api_client as api

from tradejournal.pg import accounts as pg_accounts
from tradejournal.pg import journal as pg_journal
from tradejournal.pg import settings as pg_settings

BYS = ["symbol", "weekday", "hour", "hold", "tag", "account"]
TOO_BIG = 9_999_999_999_999_999_999  # more than a Postgres bigint holds


def add(conn, account_id, day, net, *, nth=0, root="NQ", hold=60, entry_hour=15) -> int:
    """`add_trade`, with the hour of entry and the hold time chosen, and the new trade's id back."""
    add_trade(conn, account_id, day, net, nth=nth, root=root)
    trade_id = conn.execute("SELECT max(id) AS id FROM trades").fetchone()["id"]
    entry = datetime(day.year, day.month, day.day, entry_hour, nth, tzinfo=UTC)
    conn.execute(
        "UPDATE trades SET entry_at = %s, exit_at = %s, hold_seconds = %s WHERE id = %s",
        (entry, entry + timedelta(seconds=hold), hold, trade_id),
    )
    return trade_id


@pytest.fixture
def journal(db, alice):
    """Alice's two accounts and six trades over three trading days, three of them carrying tags.

    | trade | day        | account | contract | net     | entered (UTC) | held    | tags                   |
    | 1     | Mon 18 Mar | Apex 07 | NQ       |  100.00 | 14:00         | 30 s    | Opening drive          |
    | 2     | Mon 18 Mar | Apex 07 | NQ       |  -40.00 | 14:05         | 600 s   | Opening drive, FOMO    |
    | 3     | Tue 19 Mar | Apex 07 | ES       |   60.00 | 17:00         | 3600 s  |                        |
    | 4     | Tue 19 Mar | Lucid 01| NQ       | -100.00 | 17:05         | 7200 s  | FOMO                   |
    | 5     | Thu 21 Mar | Lucid 01| ES       |   20.00 | 14:00         | 90 s    |                        |
    | 6     | Thu 21 Mar | Lucid 01| CL       |  -10.00 | 19:09         | 45 s    |                        |
    """
    with db.tenant_session(alice) as conn:
        apex = make_account(conn, "Apex 07", firm="Apex")
        lucid = make_account(conn, "Lucid 01", firm="Lucid", account_type="funded")
        t1 = add(conn, apex.id, date(2024, 3, 18), "100.00", nth=0, hold=30, entry_hour=14)
        t2 = add(conn, apex.id, date(2024, 3, 18), "-40.00", nth=5, hold=600, entry_hour=14)
        t3 = add(conn, apex.id, date(2024, 3, 19), "60.00", nth=0, root="ES", hold=3600, entry_hour=17)
        t4 = add(conn, lucid.id, date(2024, 3, 19), "-100.00", nth=5, hold=7200, entry_hour=17)
        t5 = add(conn, lucid.id, date(2024, 3, 21), "20.00", nth=0, root="ES", hold=90, entry_hour=14)
        t6 = add(conn, lucid.id, date(2024, 3, 21), "-10.00", nth=9, root="CL", hold=45, entry_hour=19)
        groups = {g.name: g.id for g in pg_journal.tag_groups(conn)}
        pg_journal.set_trade_tags(conn, t1, groups["Setup"], ["Opening drive"])
        pg_journal.set_trade_tags(conn, t2, groups["Setup"], ["Opening drive"])
        pg_journal.set_trade_tags(conn, t2, groups["Mistake"], ["FOMO"])
        pg_journal.set_trade_tags(conn, t4, groups["Mistake"], ["FOMO"])
    return {"apex": apex.id, "lucid": lucid.id, "trades": [t1, t2, t3, t4, t5, t6]}


def get_report(client, by="symbol", **params):
    response = client.get("/api/v1/reports", params={"by": by, **params})
    assert response.status_code == 200, response.text
    return response.json()


def rows(body, *fields):
    """Each group as a tuple of its label, then its numbers: (label, trades, net) for the default fields."""
    fields = fields or ("trade_count", "net_pnl")
    return [(g["label"], *(g["summary"][f] for f in fields)) for g in body["groups"]]


# ---- the numbers ------------------------------------------------------------------------------------------------------


def test_the_symbol_report_lists_each_contract_best_result_first(db, alice, journal):
    response = api(db, alice).get("/api/v1/reports")  # no choice made: "symbol" is the default
    assert response.status_code == 200
    body = response.json()
    assert body["by"] == "symbol"
    assert rows(body) == [("ES", 2, "80.00"), ("CL", 1, "-10.00"), ("NQ", 3, "-40.00")]
    assert body["summary"]["trade_count"] == 6 and body["summary"]["net_pnl"] == "30.00"


def test_the_weekday_report_has_all_seven_days_monday_first(db, alice, journal):
    body = get_report(api(db, alice), "weekday")
    assert rows(body) == [
        ("Monday", 2, "60.00"),
        ("Tuesday", 2, "-40.00"),
        ("Wednesday", 0, "0.00"),
        ("Thursday", 2, "10.00"),
        ("Friday", 0, "0.00"),
        ("Saturday", 0, "0.00"),
        ("Sunday", 0, "0.00"),
    ]


def test_the_hold_report_has_every_step_and_each_trade_is_in_exactly_one(db, alice, journal):
    body = get_report(api(db, alice), "hold")
    assert rows(body) == [
        ("Under 1 minute", 2, "90.00"),  # 30 s and 45 s
        ("1 to 5 minutes", 1, "20.00"),  # 90 s
        ("5 to 15 minutes", 1, "-40.00"),  # 600 s
        ("15 to 30 minutes", 0, "0.00"),
        ("30 to 60 minutes", 0, "0.00"),
        ("1 to 2 hours", 1, "60.00"),  # exactly 3600 s: the lower edge belongs to the step
        ("Over 2 hours", 1, "-100.00"),  # exactly 7200 s
    ]


def test_the_account_report_names_the_firm_and_kind_of_each_account(db, alice, journal):
    body = get_report(api(db, alice), "account")
    assert rows(body) == [("Apex 07", 3, "120.00"), ("Lucid 01", 3, "-90.00")]
    assert [g["detail"] for g in body["groups"]] == ["Apex · Evaluation", "Lucid · Funded"]
    assert [g["key"] for g in body["groups"]] == [str(journal["apex"]), str(journal["lucid"])]


def test_the_tag_report_counts_a_trade_for_every_tag_and_shows_the_category(db, alice, journal):
    body = get_report(api(db, alice), "tag")
    assert rows(body) == [("Untagged", 3, "70.00"), ("Opening drive", 2, "60.00"), ("FOMO", 2, "-140.00")]
    assert [g["detail"] for g in body["groups"]] == [None, "Setup", "Mistake"]
    assert body["summary"]["trade_count"] == 6  # not 7: trade 2 is in two groups but is one trade


def test_the_hour_is_the_hour_of_entry_in_the_persons_own_timezone(db, alice, journal):
    client = api(db, alice)
    # Alice has not chosen a timezone, so it is the default (Los Angeles, UTC-7 in March): 14:00 UTC is 07:00.
    assert rows(get_report(client, "hour")) == [("07:00", 3, "80.00"), ("10:00", 2, "-40.00"), ("12:00", 1, "-10.00")]
    with db.tenant_session(alice) as conn:
        pg_settings.set_setting(conn, "timezone", "America/New_York")
    assert rows(get_report(client, "hour")) == [("10:00", 3, "80.00"), ("13:00", 2, "-40.00"), ("15:00", 1, "-10.00")]
    with db.tenant_session(alice) as conn:
        pg_settings.set_setting(conn, "timezone", "UTC")
    assert [r[0] for r in rows(get_report(client, "hour"))] == ["14:00", "17:00", "19:00"]


def test_the_statistics_of_a_group_are_the_full_set_the_dashboard_shows(db, alice, journal):
    nq = get_report(api(db, alice))["groups"][2]["summary"]
    assert nq["trade_count"] == 3 and (nq["win_count"], nq["loss_count"]) == (1, 2)
    assert nq["win_rate"] == pytest.approx(1 / 3) and nq["profit_factor"] == pytest.approx(100 / 140)
    assert (nq["avg_win"], nq["avg_loss"], nq["expectancy"]) == ("100.00", "-70.00", "-13.33")
    assert (nq["gross_pnl"], nq["fees"], nq["net_pnl"]) == ("-28.00", "12.00", "-40.00")
    assert nq["account_count"] == 2 and nq["trading_days"] == 2


# ---- agreeing with the dashboard ----------------------------------------------------------------------------------------


def test_every_group_is_what_the_dashboard_says_when_it_is_filtered_to_that_group(db, alice, journal):
    client = api(db, alice)
    symbols = get_report(client)
    assert symbols["summary"] == client.get("/api/v1/dashboard").json()["summary"]
    for group in symbols["groups"]:
        dashboard = client.get("/api/v1/dashboard", params={"symbol_roots": [group["key"]]}).json()
        assert group["summary"] == dashboard["summary"], group["label"]
    for group in get_report(client, "account")["groups"]:
        dashboard = client.get("/api/v1/dashboard", params={"account_ids": [int(group["key"])]}).json()
        assert group["summary"] == dashboard["summary"], group["label"]


@pytest.mark.parametrize("by", BYS)
def test_each_report_covers_the_same_trades_as_the_dashboard_under_the_same_filters(db, alice, journal, by):
    client = api(db, alice)
    for params in (
        {},
        {"account_type": "funded"},
        {"firms": ["Apex"]},
        {"symbol_roots": ["ES", "CL"]},
        {"direction": "short"},
        {"start": "2024-03-19", "end": "2024-03-19"},
        {"start": "2024-03-20"},
    ):
        report = get_report(client, by, **params)
        dashboard = client.get("/api/v1/dashboard", params=params).json()
        assert report["summary"] == dashboard["summary"], params
        if by in ("symbol", "account", "weekday", "hour", "hold"):  # each trade is in exactly one group
            assert sum(g["summary"]["trade_count"] for g in report["groups"]) == dashboard["summary"]["trade_count"]


def test_a_filter_that_matches_nothing_gives_an_empty_report_not_an_error(db, alice, journal):
    for by in BYS:
        body = get_report(api(db, alice), by, direction="short")
        assert body["summary"]["trade_count"] == 0 and sum(g["summary"]["trade_count"] for g in body["groups"]) == 0


def test_archived_accounts_are_left_out_unless_asked_for(db, alice, journal):
    with db.tenant_session(alice) as conn:
        pg_accounts.archive_account(conn, journal["lucid"], outcome="failed", ended_on=date(2024, 3, 22))
    client = api(db, alice)
    assert rows(get_report(client, "account")) == [("Apex 07", 3, "120.00")]
    assert rows(get_report(client, "account", show_archived="true")) == [
        ("Apex 07", 3, "120.00"),
        ("Lucid 01", 3, "-90.00"),
    ]


def test_a_preset_means_the_persons_trading_day(db, alice, journal):
    """At 12:00 UTC on 20 March it is 08:00 in New York, before the 18:00 session open: today is the 20th, and "7d" is the
    14th to the 20th, which holds the four trades of the 18th and 19th."""
    client = api(db, alice, now=datetime(2024, 3, 20, 12, 0, tzinfo=UTC))
    assert get_report(client, preset="7d")["summary"]["trade_count"] == 4
    assert get_report(client, preset="all")["summary"]["trade_count"] == 6


# ---- an empty journal and bad requests ------------------------------------------------------------------------------------


def test_a_journal_with_no_trades_still_answers_every_report(db, alice):
    client = api(db, alice)
    for by in BYS:
        body = get_report(client, by)
        assert body["summary"]["trade_count"] == 0 and body["summary"]["net_pnl"] == "0.00"
        assert len(body["groups"]) == {"weekday": 7, "hold": 7}.get(by, 0)


@pytest.mark.parametrize(
    "params",
    [
        {"by": "colour"},
        {"by": ""},
        {"by": "symbol", "nonsense": "1"},
        {"by": "symbol", "start": "2024-03-20", "end": "2024-03-19"},
        {"by": "symbol", "preset": "last_decade"},
        {"by": "symbol", "account_ids": TOO_BIG},
    ],
)
def test_a_bad_request_is_a_422_problem_not_a_wider_answer(db, alice, journal, params):
    response = api(db, alice).get("/api/v1/reports", params=params)
    assert response.status_code == 422, params
    assert response.headers["content-type"] == "application/problem+json"


def test_reading_a_report_changes_nothing(db, alice, journal):
    def counts():
        with db.tenant_session(alice) as conn:
            return [
                conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
                for table in ("trades", "tags", "trade_tags", "user_settings")
            ]

    before = counts()
    client = api(db, alice)
    for by in BYS:
        get_report(client, by)
    assert counts() == before


# ---- two people --------------------------------------------------------------------------------------------------------


@pytest.fixture
def bobs_journal(db, bob):
    with db.tenant_session(bob) as conn:
        account = make_account(conn, "BOB-account", firm="Tradeify")
        trade = add(conn, account.id, date(2024, 3, 18), "7.00", root="BOB", hold=75)
        groups = {g.name: g.id for g in pg_journal.tag_groups(conn)}
        pg_journal.set_trade_tags(conn, trade, groups["Setup"], ["BOB-tag"])
    return {"account": account.id}


@pytest.mark.parametrize("by", BYS)
def test_bob_never_reads_alices_names_or_numbers_even_when_he_names_her_account(
    db, alice, bob, journal, bobs_journal, by
):
    named = get_report(api(db, bob), by, account_ids=[journal["apex"], journal["lucid"]])
    assert named["summary"]["trade_count"] == 0  # her accounts are not his, so there is nothing to match
    plain = get_report(api(db, bob), by)
    text = json.dumps(plain)
    for word in ("Apex", "Lucid", "Opening drive", "FOMO", "80.00", "120.00", "NQ", "ES", "CL"):
        assert word not in text, (by, word)
    assert plain["summary"]["trade_count"] == 1 and plain["summary"]["net_pnl"] == "7.00"  # the control: his own


def test_each_person_sees_their_own_names_in_the_reports_that_carry_names(db, alice, bob, journal, bobs_journal):
    assert "Opening drive" in json.dumps(get_report(api(db, alice), "tag"))
    assert "BOB-tag" in json.dumps(get_report(api(db, bob), "tag"))
    assert "BOB-account" in json.dumps(get_report(api(db, bob), "account"))
    assert "BOB-" not in json.dumps([get_report(api(db, alice), by) for by in BYS])
