"""The day and week share card's numbers, built from plain trade records: no Postgres needed."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tradejournal.api.period_card import build_period_card
from tradejournal.queries import TradeRecord

DAY = date(2026, 11, 13)
ACCOUNT_IDS = {"Apex 07": 1, "lucid 01": 2}


def trade(
    net: str, points: str, *, n: int = 0, qty: int = 1, account: str = "Apex 07", symbol: str = "NQZ6"
) -> TradeRecord:
    exit_at = datetime(2026, 11, 13, 15, n, tzinfo=UTC)
    return TradeRecord(
        id=n + 1,
        account_id=ACCOUNT_IDS[account],
        account_name=account,
        account_type="evaluation",
        firm="Apex",
        symbol=symbol,
        symbol_root=symbol[:-2],
        contract_month=None,
        direction="long",
        entry_at=exit_at - timedelta(seconds=60),
        exit_at=exit_at,
        trade_date=DAY,
        hold_seconds=60,
        total_qty=qty,
        max_position=qty,
        fill_pair_count=1,
        tick_size=Decimal("0.25"),
        avg_entry=Decimal("100"),
        avg_exit=Decimal("101"),
        points=Decimal(points),
        ticks=Decimal(points) * 4,
        gross_pnl=Decimal(net) + 4,
        fees=Decimal("4"),
        net_pnl=Decimal(net),
        notes="",
    )


def card(trades):
    return build_period_card(trades, DAY, DAY, number=7).model_dump(mode="json")


def test_net_points_add_up_each_trades_points_per_contract_and_ignore_the_size():
    # +10 points on 1 contract and -5 points on 3 contracts: +5 points, though the money is down.
    body = card([trade("200.00", "10", n=0, qty=1), trade("-300.00", "-5", n=1, qty=3)])
    assert body["net_points"] == "5"
    assert body["contracts"] == 4
    assert body["summary"]["trade_count"] == 2 and body["summary"]["net_pnl"] == "-100.00"
    assert body["number"] == 7 and body["start"] == body["end"] == "2026-11-13"


def test_best_and_worst_are_by_money_and_separately_by_points():
    # The biggest money win is a 4-contract trade with few points; the most points came on 1 contract.
    trades = [
        trade("400.00", "5", n=0, qty=4),
        trade("300.00", "15", n=1, qty=1),
        trade("-50.00", "-2.5", n=2, qty=1),
        trade("-90.00", "-1", n=3, qty=5),
    ]
    body = card(trades)
    assert (body["best"]["id"], body["worst"]["id"]) == (1, 4)
    assert (body["best_points"]["id"], body["worst_points"]["id"]) == (2, 3)
    assert body["best"] == {
        "id": 1,
        "symbol": "NQZ6",
        "direction": "long",
        "trade_day": "2026-11-13",
        "entry_at": "2026-11-13T14:59:00Z",
        "total_qty": 4,
        "net_pnl": "400.00",
        "points": "5",
    }


def test_a_tie_goes_to_the_trade_that_closed_first():
    body = card([trade("100.00", "4", n=0), trade("100.00", "4", n=1), trade("100.00", "4", n=2)])
    assert [body[k]["id"] for k in ("best", "worst", "best_points", "worst_points")] == [1, 1, 1, 1]


def test_one_trade_is_both_the_best_and_the_worst():
    body = card([trade("-25.00", "-1.25")])
    assert body["best"] == body["worst"] and body["best"]["id"] == 1
    assert body["net_points"] == "-1.25"


def test_accounts_are_named_once_each_in_alphabetical_order():
    trades = [
        trade("1.00", "1", n=0, account="lucid 01"),
        trade("1.00", "1", n=1, account="Apex 07"),
        trade("1.00", "1", n=2, account="lucid 01"),
    ]
    assert card(trades)["accounts"] == ["Apex 07", "lucid 01"]


def test_with_no_trades_there_is_nothing_to_name():
    body = card([])
    assert body["summary"]["trade_count"] == 0 and body["contracts"] == 0 and body["net_points"] == "0"
    assert [body[k] for k in ("best", "worst", "best_points", "worst_points")] == [None] * 4
    assert body["accounts"] == []
