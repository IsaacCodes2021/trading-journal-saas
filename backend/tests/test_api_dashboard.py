"""The dashboard builder and the tenant routes' behaviour with no database: no Postgres needed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from api_fakes import make_services
from fastapi.testclient import TestClient

from tradejournal.api.app import create_app
from tradejournal.api.config import ApiConfig
from tradejournal.api.dashboard import build_dashboard
from tradejournal.api.deps import SESSION_COOKIE
from tradejournal.presets import preset_range
from tradejournal.queries import TradeRecord

ORIGIN = "https://app.example.com"


def trade(net: str, *, n: int = 0, day: date = date(2024, 3, 18), account_id: int = 1) -> TradeRecord:
    exit_at = datetime(day.year, day.month, day.day, 15, n, tzinfo=UTC)
    return TradeRecord(
        id=n + 1,
        account_id=account_id,
        account_name=f"Account {account_id}",
        account_type="evaluation",
        firm="Apex",
        symbol="NQH4",
        symbol_root="NQ",
        contract_month=None,
        direction="long",
        entry_at=exit_at - timedelta(seconds=60),
        exit_at=exit_at,
        trade_date=day,
        hold_seconds=60,
        total_qty=1,
        max_position=1,
        fill_pair_count=1,
        tick_size=Decimal("0.25"),
        avg_entry=Decimal("100"),
        avg_exit=Decimal("101"),
        points=Decimal("1"),
        ticks=Decimal("4"),
        gross_pnl=Decimal(net),
        fees=Decimal("0"),
        net_pnl=Decimal(net),
        notes="",
    )


def test_money_leaves_in_cents_even_when_there_is_nothing_to_add_up():
    body = build_dashboard([]).model_dump(mode="json")
    summary = body["summary"]
    assert [summary[k] for k in ("gross_pnl", "fees", "net_pnl", "total_won", "total_lost", "max_drawdown")] == [
        "0.00"
    ] * 6
    assert body["range"] is None and body["score"] is None and body["accounts"] == []


def test_averages_are_rounded_to_cents_and_a_zero_is_never_negative():
    trades = [trade("10.00", n=0), trade("10.01", n=1), trade("10.01", n=2), trade("-0.00", n=3)]
    summary = build_dashboard(trades).model_dump(mode="json")["summary"]
    assert summary["expectancy"] == "7.51"  # 30.02 / 4 = 7.505, rounded half up
    assert summary["avg_win"] == "10.01"  # 30.02 / 3 = 10.0066...
    assert summary["avg_loss"] is None and summary["largest_loss"] is None
    assert summary["breakeven_count"] == 1 and summary["max_drawdown"] == "0.00"


def test_accounts_are_ordered_by_result_then_name():
    trades = [trade("5.00", n=0, account_id=2), trade("5.00", n=1, account_id=1), trade("9.00", n=2, account_id=3)]
    names = [a["account"]["name"] for a in build_dashboard(trades).model_dump(mode="json")["accounts"]]
    assert names == ["Account 3", "Account 1", "Account 2"]


def test_presets_count_back_from_today_inclusive():
    today = date(2024, 3, 21)
    assert preset_range("7d", today) == (date(2024, 3, 15), today)
    assert preset_range("mtd", today) == (date(2024, 3, 1), today)
    assert preset_range("last_month", today) == (date(2024, 2, 1), date(2024, 2, 29))
    assert preset_range("ytd", today) == (date(2024, 1, 1), today)
    assert preset_range("all", today) == (None, None)


def test_a_signed_in_request_to_a_server_with_no_database_is_a_503_not_an_empty_answer(tmp_path):
    services, sessions, accounts = make_services()
    token, _ = sessions.sign_in(accounts.add("a@example.test").user.id)
    config = ApiConfig(app_origin=ORIGIN, web_dist=tmp_path / "none", session_secret=b"k" * 32)
    client = TestClient(create_app(config, services), base_url=ORIGIN)
    for path in ("/api/v1/dashboard", "/api/v1/reports", "/api/v1/accounts", "/api/v1/filter-options"):
        assert client.get(path).status_code == 401  # anonymous: signing in comes first
        response = client.get(path, headers={"cookie": f"{SESSION_COOKIE}={token}"})
        assert response.status_code == 503, path
        assert response.headers["content-type"] == "application/problem+json"
