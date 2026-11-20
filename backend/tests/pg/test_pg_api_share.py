"""The day and week share cards' numbers against a real Postgres: the range, the filters, the number on the card, and
the isolation between people."""

from __future__ import annotations

from datetime import date

import pytest
from pg_helpers import add_trade, make_account
from pg_helpers import api_client as api


@pytest.fixture
def journal(db, alice):
    """Alice's trades in the week of Sunday 29 Nov to Saturday 5 Dec 2026, which crosses a month's end, and one before.

    Mon 23 Nov: +50 (an earlier week). Mon 30 Nov: +100 on 1 contract (10 pts), -60 on 3 contracts (-2 pts, ES, short).
    Tue 1 Dec: -40 on 2 contracts (-1.5 pts, the funded account). Sat 5 Dec: +25 (0.75 pts).
    """
    with db.tenant_session(alice) as conn:
        apex = make_account(conn, "Apex 07", firm="Apex")
        lucid = make_account(conn, "Lucid 01", firm="Lucid", account_type="funded")
        add_trade(conn, apex.id, date(2026, 11, 23), "50.00", points="5")
        add_trade(conn, apex.id, date(2026, 11, 30), "100.00", nth=0, points="10")
        add_trade(conn, apex.id, date(2026, 11, 30), "-60.00", nth=5, qty=3, points="-2", root="ES", direction="short")
        add_trade(conn, lucid.id, date(2026, 12, 1), "-40.00", qty=2, points="-1.5")
        add_trade(conn, apex.id, date(2026, 12, 5), "25.00", points="0.75")
    return {"apex": apex.id, "lucid": lucid.id}


def day_card(client, day: str, **query):
    response = client.get(f"/api/v1/share/day/{day}", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def week_card(client, start: str, **query):
    response = client.get(f"/api/v1/share/week/{start}", params=query)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_day_card_counts_that_day_only(db, alice, journal):
    body = day_card(api(db, alice), "2026-11-30")
    assert (body["start"], body["end"]) == ("2026-11-30", "2026-11-30")
    assert body["summary"]["trade_count"] == 2 and body["summary"]["net_pnl"] == "40.00"
    assert body["contracts"] == 4
    assert body["net_points"] == "8"  # 10 + (-2): points per contract, sizes not counted
    assert body["best"]["net_pnl"] == "100.00" and body["worst"]["symbol"] == "ESH4"
    assert body["worst"]["direction"] == "short" and body["worst"]["total_qty"] == 3
    assert body["accounts"] == ["Apex 07"]


def test_a_week_card_runs_sunday_to_saturday_across_the_months_end(db, alice, journal):
    body = week_card(api(db, alice), "2026-11-29")
    assert (body["start"], body["end"]) == ("2026-11-29", "2026-12-05")
    summary = body["summary"]
    assert summary["trade_count"] == 4 and summary["net_pnl"] == "25.00"
    assert (summary["trading_days"], summary["winning_days"], summary["losing_days"]) == (3, 2, 1)
    assert body["contracts"] == 7 and body["net_points"] == "7.25"
    assert body["best"]["trade_day"] == "2026-11-30" and body["worst"]["net_pnl"] == "-60.00"
    assert body["worst_points"]["net_pnl"] == "-60.00" and body["best_points"]["points"] == "10"
    assert body["accounts"] == ["Apex 07", "Lucid 01"]


def test_the_cards_follow_the_calendars_filters(db, alice, journal):
    client = api(db, alice)
    funded = week_card(client, "2026-11-29", account_type="funded")
    assert funded["summary"]["trade_count"] == 1 and funded["accounts"] == ["Lucid 01"]
    shorts = day_card(client, "2026-11-30", direction="short")
    assert shorts["summary"]["trade_count"] == 1 and shorts["net_points"] == "-2"
    # The day is the date range: a range or a preset sent along does not widen or move it.
    pinned = day_card(client, "2026-11-30", start="2026-11-01", end="2026-12-31", preset="ytd")
    assert pinned["summary"]["trade_count"] == 2 and pinned["start"] == "2026-11-30"


def test_the_number_counts_the_persons_trading_days_and_weeks_up_to_this_one(db, alice, journal):
    client = api(db, alice)
    # Trading days: 23 Nov, 30 Nov, 1 Dec, 5 Dec.
    assert [day_card(client, d)["number"] for d in ("2026-11-23", "2026-11-30", "2026-12-01", "2026-12-05")] == [
        1,
        2,
        3,
        4,
    ]
    # Weeks with trades: the one of 22 Nov, then the one of 29 Nov.
    assert [week_card(client, d)["number"] for d in ("2026-11-22", "2026-11-29")] == [1, 2]
    # The number ignores the filters: the day is still Alice's second trading day.
    assert day_card(client, "2026-11-30", account_type="funded")["number"] == 2


def test_a_day_or_week_with_no_trades_has_nothing_to_name(db, alice, journal):
    body = day_card(api(db, alice), "2026-12-02")
    assert body["summary"]["trade_count"] == 0 and body["contracts"] == 0 and body["net_points"] == "0"
    assert body["best"] is None and body["worst_points"] is None and body["accounts"] == []


def test_a_week_must_start_on_a_sunday_and_a_day_must_be_a_real_one(db, alice, journal):
    client = api(db, alice)
    monday = client.get("/api/v1/share/week/2026-11-30")
    assert monday.status_code == 422 and "Sunday" in monday.json()["detail"]
    assert client.get("/api/v1/share/day/2026-02-30").status_code == 422
    assert client.get("/api/v1/share/day/1999-12-31").status_code == 422


def test_another_persons_card_never_shows_alices_trades(db, alice, bob, journal):
    with db.tenant_session(bob) as conn:
        add_trade(conn, make_account(conn, "Bob's").id, date(2026, 11, 30), "999.00", points="99")
    bobs = day_card(api(db, bob), "2026-11-30")
    assert bobs["summary"]["trade_count"] == 1 and bobs["number"] == 1 and bobs["accounts"] == ["Bob's"]
    alices = day_card(api(db, alice), "2026-11-30")
    assert alices["summary"]["trade_count"] == 2 and alices["number"] == 2 and alices["best"]["net_pnl"] == "100.00"
