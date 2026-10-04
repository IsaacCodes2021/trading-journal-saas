"""The read routes of the tenant API against a real Postgres: the numbers, the filters, and the isolation between users.

Sessions are the in-memory fakes (the real ones arrive with sign-in, M5); everything below the route is real: the
request role (`app_rw`), row-level security, the ported queries and `finance.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from api_fakes import make_services
from fastapi.testclient import TestClient
from pg_helpers import make_account

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.pg import accounts as pg_accounts
from tradejournal.pg import settings as pg_settings

ORIGIN = "https://app.example.com"
NOON_UTC = datetime(2024, 3, 22, 12, 0, tzinfo=UTC)


def api(db, user_id, *, now: datetime = NOON_UTC) -> TestClient:
    """A browser signed in as `user_id`, on a server whose clock says `now`."""
    services, sessions, _ = make_services()
    token, _ = sessions.sign_in(user_id)
    config = ApiConfig(app_origin=ORIGIN, web_dist=Path("/nonexistent"), session_secret=b"k" * 32)
    app = create_app(config, services, database=db, clock=lambda: now)
    return TestClient(app, base_url=ORIGIN, headers={"cookie": f"{SESSION_COOKIE}={token}"})


def add_trade(conn, account_id: int, day: date, net: str, *, nth: int = 0, root: str = "NQ", direction: str = "long"):
    """One closed trade on trading day `day`: $4.00 of fees, so gross is net + 4."""
    exit_at = datetime(day.year, day.month, day.day, 15, nth, tzinfo=UTC)
    conn.execute(
        """INSERT INTO trades (account_id, symbol, symbol_root, direction, entry_at, exit_at, trade_date,
               hold_seconds, total_qty, max_position, fill_pair_count, tick_size, avg_entry, avg_exit,
               points, ticks, gross_pnl, fees, net_pnl)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 60, 1, 1, 1, 0.25, 100, 101, 1, 4, %s, 4, %s)""",
        (
            account_id,
            f"{root}H4",
            root,
            direction,
            exit_at - timedelta(seconds=60),
            exit_at,
            day,
            Decimal(net) + 4,
            Decimal(net),
        ),
    )


@pytest.fixture
def journal(db, alice):
    """Alice's two accounts and five trades: nets +100, -40, +60, -100, +20 over three days."""
    with db.tenant_session(alice) as conn:
        apex = make_account(conn, "Apex 07", firm="Apex")
        lucid = make_account(conn, "Lucid 01", firm="Lucid", account_type="funded")
        add_trade(conn, apex.id, date(2024, 3, 18), "100.00", nth=0)
        add_trade(conn, apex.id, date(2024, 3, 18), "-40.00", nth=5)
        add_trade(conn, apex.id, date(2024, 3, 19), "60.00", nth=0, root="ES", direction="short")
        add_trade(conn, lucid.id, date(2024, 3, 19), "-100.00", nth=5)
        add_trade(conn, lucid.id, date(2024, 3, 21), "20.00", nth=0)
    return {"apex": apex.id, "lucid": lucid.id}


# ---- the dashboard's numbers -------------------------------------------------------------------------------------


def test_the_dashboard_numbers_are_the_finance_definitions_applied_to_the_trades(db, alice, journal):
    body = api(db, alice).get("/api/v1/dashboard").json()
    summary = body["summary"]
    assert summary["trade_count"] == 5 and summary["account_count"] == 2
    assert (summary["win_count"], summary["loss_count"], summary["breakeven_count"]) == (3, 2, 0)
    assert summary["net_pnl"] == "40.00" and summary["fees"] == "20.00" and summary["gross_pnl"] == "60.00"
    assert summary["total_won"] == "180.00" and summary["total_lost"] == "-140.00"
    assert summary["win_rate"] == pytest.approx(0.6)
    assert summary["profit_factor"] == pytest.approx(180 / 140)
    assert summary["avg_win"] == "60.00" and summary["avg_loss"] == "-70.00"
    assert summary["win_loss_ratio"] == pytest.approx(60 / 70)
    assert summary["expectancy"] == "8.00"
    assert summary["largest_win"] == "100.00" and summary["largest_loss"] == "-100.00"
    assert (summary["trading_days"], summary["winning_days"], summary["losing_days"]) == (3, 2, 1)
    # Cumulative: 100, 60, 120, 20, 40. The peak is 120, the trough after it 20: a drawdown of 100.
    assert summary["max_drawdown"] == "100.00"
    assert [p["equity"] for p in body["equity_by_trade"]] == ["100.00", "60.00", "120.00", "20.00", "40.00"]
    assert [p["drawdown"] for p in body["equity_by_trade"]] == ["0.00", "-40.00", "0.00", "-100.00", "-80.00"]
    assert body["equity_by_trade"][0]["at"] == "2024-03-18T15:00:00Z"  # the trade's exit, UTC, with a Z
    assert body["daily"] == [
        {"day": "2024-03-18", "net_pnl": "60.00", "trade_count": 2},
        {"day": "2024-03-19", "net_pnl": "-40.00", "trade_count": 2},
        {"day": "2024-03-21", "net_pnl": "20.00", "trade_count": 1},
    ]
    assert [p["at"] for p in body["equity_by_day"]] == ["2024-03-18", "2024-03-19", "2024-03-21"]
    assert body["range"] == {"start": "2024-03-18", "end": "2024-03-21"}
    assert len(body["score"]["axes"]) == 6 and 0 <= body["score"]["overall"] <= 100
    assert [(a["account"]["name"], a["trade_count"], a["net_pnl"]) for a in body["accounts"]] == [
        ("Apex 07", 3, "120.00"),
        ("Lucid 01", 2, "-80.00"),
    ]


def test_an_empty_journal_gives_an_empty_dashboard_not_an_error(db, alice):
    body = api(db, alice).get("/api/v1/dashboard").json()
    assert body["range"] is None and body["score"] is None
    assert body["equity_by_trade"] == [] and body["daily"] == [] and body["accounts"] == []
    summary = body["summary"]
    assert summary["trade_count"] == 0 and summary["net_pnl"] == "0.00" and summary["max_drawdown"] == "0.00"
    assert summary["win_rate"] is None and summary["profit_factor"] is None and summary["expectancy"] is None


# ---- the filters --------------------------------------------------------------------------------------------------


def test_each_filter_narrows_the_trades(db, alice, journal):
    client = api(db, alice)

    def count(**params) -> int:
        response = client.get("/api/v1/dashboard", params=params)
        assert response.status_code == 200, response.text
        return response.json()["summary"]["trade_count"]

    assert count(account_type="funded") == 2
    assert count(account_type="evaluation") == 3
    assert count(firms=["Lucid"]) == 2
    assert count(firms=["Lucid", "Apex"]) == 5
    assert count(account_ids=[journal["lucid"]]) == 2
    assert count(symbol_roots=["ES"]) == 1
    assert count(direction="short") == 1
    assert count(direction="long", account_type="evaluation") == 2
    assert count(start="2024-03-19", end="2024-03-19") == 2
    assert count(start="2024-03-20") == 1
    assert count(end="2024-03-18") == 2


def test_archived_accounts_are_left_out_unless_asked_for(db, alice, journal):
    with db.tenant_session(alice) as conn:
        pg_accounts.archive_account(conn, journal["apex"], outcome="passed", ended_on=date(2024, 3, 20))
    client = api(db, alice)
    assert client.get("/api/v1/dashboard").json()["summary"]["trade_count"] == 2
    assert client.get("/api/v1/dashboard", params={"show_archived": "true"}).json()["summary"]["trade_count"] == 5


@pytest.mark.parametrize(
    "params",
    [
        {"preset": "last_decade"},
        {"direction": "sideways"},
        {"account_type": "demo"},
        {"start": "not-a-date"},
        {"start": "2024-03-20", "end": "2024-03-19"},
        {"nonsense": "1"},
    ],
)
def test_a_bad_filter_is_a_422_problem_not_a_wider_answer(db, alice, params):
    response = api(db, alice).get("/api/v1/dashboard", params=params)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"


def test_a_preset_means_the_persons_trading_day_not_the_servers_calendar_day(db, alice, journal):
    """2024-03-20 22:30 UTC is 18:30 in New York: the Globex session that opened at 18:00 already counts for the 21st."""
    after_the_open = datetime(2024, 3, 20, 22, 30, tzinfo=UTC)
    client = api(db, alice, now=after_the_open)
    last_week = client.get("/api/v1/dashboard", params={"preset": "7d"}).json()
    assert last_week["range"] == {"start": "2024-03-18", "end": "2024-03-21"}  # 15th to the 21st: all five trades
    only_today = client.get("/api/v1/dashboard", params={"preset": "7d", "start": "2024-03-21"}).json()
    assert only_today["summary"]["trade_count"] == 1  # an explicit start replaces the preset's

    with db.tenant_session(alice) as conn:  # plain calendar days: now it is still the 20th, so the 21st is the future
        pg_settings.set_setting(conn, "trading_day_start", "00:00")
    assert client.get("/api/v1/dashboard", params={"preset": "30d"}).json()["summary"]["trade_count"] == 4
    assert client.get("/api/v1/dashboard", params={"preset": "mtd"}).json()["summary"]["trade_count"] == 4
    assert client.get("/api/v1/dashboard", params={"preset": "last_month"}).json()["summary"]["trade_count"] == 0
    later = api(db, alice, now=datetime(2024, 3, 25, 12, 0, tzinfo=UTC))  # the 25th: "7d" starts on the 19th
    assert later.get("/api/v1/dashboard", params={"preset": "7d"}).json()["summary"]["trade_count"] == 3


def test_filter_options_list_the_firms_and_contracts_the_person_has(db, alice, journal):
    body = api(db, alice).get("/api/v1/filter-options").json()
    assert body == {"firms": ["Apex", "Lucid"], "symbol_roots": ["ES", "NQ"]}


# ---- accounts ------------------------------------------------------------------------------------------------------


def test_accounts_are_listed_by_name_with_their_rules_and_money_as_strings(db, alice):
    with db.tenant_session(alice) as conn:
        make_account(
            conn,
            "lucid 01",
            firm="Lucid",
            account_type="funded",
            max_drawdown=Decimal("2500"),
            trailing_type="eod",
            profit_target=Decimal("3000"),
        )
        archived = make_account(conn, "Apex 07")
        pg_accounts.archive_account(conn, archived.id, outcome="failed", ended_on=date(2024, 3, 1), note="blew it")
    client = api(db, alice)
    everything = client.get("/api/v1/accounts").json()
    assert [a["name"] for a in everything] == ["Apex 07", "lucid 01"]  # case-insensitive order
    lucid = everything[1]
    assert lucid["max_drawdown"] == "2500.00" and lucid["profit_target"] == "3000.00"
    assert lucid["trailing_type"] == "eod" and lucid["daily_loss_limit"] is None and lucid["outcome"] is None
    assert lucid["starting_balance"] == "50000.00" and lucid["account_type"] == "funded"
    assert everything[0]["outcome"] == "failed" and everything[0]["ended_on"] == "2024-03-01"
    assert everything[0]["archive_note"] == "blew it"
    active = client.get("/api/v1/accounts", params={"include_archived": "false"}).json()
    assert [a["name"] for a in active] == ["lucid 01"]


# ---- isolation ----------------------------------------------------------------------------------------------------


def test_one_user_never_sees_anothers_trades_accounts_or_choices(db, alice, bob, journal):
    with db.tenant_session(bob) as conn:
        mine = make_account(conn, "Bob Tradeify", firm="Tradeify")
        add_trade(conn, mine.id, date(2024, 3, 20), "7.00", root="CL")
    as_bob = api(db, bob)
    dashboard = as_bob.get("/api/v1/dashboard").json()
    assert dashboard["summary"]["trade_count"] == 1 and dashboard["summary"]["net_pnl"] == "7.00"
    assert [a["name"] for a in as_bob.get("/api/v1/accounts").json()] == ["Bob Tradeify"]
    assert as_bob.get("/api/v1/filter-options").json() == {"firms": ["Tradeify"], "symbol_roots": ["CL"]}
    # Naming alice's account id changes nothing: row-level security hides her rows from his transaction.
    guessed = as_bob.get("/api/v1/dashboard", params={"account_ids": [journal["apex"], journal["lucid"]]}).json()
    assert guessed["summary"]["trade_count"] == 0 and guessed["accounts"] == []
    # And alice still sees only hers.
    assert api(db, alice).get("/api/v1/dashboard").json()["summary"]["trade_count"] == 5
