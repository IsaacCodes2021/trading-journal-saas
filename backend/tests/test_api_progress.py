"""Account progress and business totals: the arithmetic, on plain records (no database)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from test_api_dashboard import trade

from tradejournal.accounts import Account
from tradejournal.api.progress import account_progress, business_totals
from tradejournal.ledger import LedgerEntry

D = Decimal


def account(**changes) -> Account:
    base = {
        "id": 1,
        "name": "Apex 07",
        "firm": "Apex",
        "account_type": "evaluation",
        "account_size": D("50000"),
        "starting_balance": D("50000"),
        "max_drawdown": D("2500"),
        "trailing_type": "eod",
        "trail_lock_enabled": False,
        "trail_lock_level": None,
        "daily_loss_limit": None,
        "profit_target": D("3000"),
        "created_at": "2024-03-01T00:00:00+00:00",
    }
    return Account(**{**base, **changes})


def trades(*nets: str):
    return [trade(net, n=i, day=date(2024, 3, 18 + i // 2)) for i, net in enumerate(nets)]


def test_progress_is_the_net_share_of_the_target_and_the_drawdown_left_below_the_peak():
    # Cumulative 100, 60, 120, 20, 40: the peak is 120 and it now sits at 40, so 80 of the 2,500 drawdown is used.
    progress = account_progress(account(), trades("100", "-40", "60", "-100", "20")).model_dump(mode="json")
    assert progress == {
        "net_pnl": "40.00",
        "trade_count": 5,
        "target_progress": pytest.approx(40 / 3000),
        "drawdown_left": "2420.00",
        "last_trading_day": "2024-03-20",
    }


def test_progress_is_clamped_and_untracked_rules_are_null():
    over = account_progress(account(), trades("5000")).model_dump(mode="json")
    assert over["target_progress"] == 1.0  # past the target is "done", not 167%
    under = account_progress(account(), trades("-400")).model_dump(mode="json")
    assert under["target_progress"] == 0.0 and under["drawdown_left"] == "2100.00"
    blown = account_progress(account(max_drawdown=D("300")), trades("-400")).model_dump(mode="json")
    assert blown["drawdown_left"] == "0.00"  # never negative
    untracked = account_progress(account(profit_target=None, max_drawdown=None), trades("10")).model_dump(mode="json")
    assert untracked["target_progress"] is None and untracked["drawdown_left"] is None
    assert account_progress(account(profit_target=D("0")), trades("10")).target_progress is None  # no division by zero


def test_an_account_with_no_trades_has_zeros_and_all_of_its_drawdown_left():
    progress = account_progress(account(), []).model_dump(mode="json")
    assert progress == {
        "net_pnl": "0.00",
        "trade_count": 0,
        "target_progress": 0.0,
        "drawdown_left": "2500.00",
        "last_trading_day": None,
    }


def entry(account_id: int, kind: str, amount: str) -> LedgerEntry:
    return LedgerEntry(1, account_id, kind, D(amount), date(2024, 3, 1), "")


def test_the_business_adds_up_costs_and_payouts_and_the_pass_rate_ignores_closed_evaluations():
    accounts = [
        account(id=1, name="A", outcome="passed"),
        account(id=2, name="B", outcome="failed"),
        account(id=3, name="C", outcome="closed"),  # does not count either way
        account(id=4, name="D", account_type="funded", outcome="failed"),  # funded accounts are not evaluations
        account(id=5, name="E"),
    ]
    entries = [entry(1, "evaluation_fee", "167.00"), entry(2, "reset_fee", "80.50"), entry(4, "payout", "1500.00")]
    totals = business_totals(accounts, entries).model_dump(mode="json")
    assert (totals["evaluations_passed"], totals["evaluations_failed"], totals["pass_rate"]) == (1, 1, 0.5)
    assert (totals["costs"], totals["payouts"], totals["net"]) == ("247.50", "1500.00", "1252.50")
    # A payout is not a cost: account 4 has only a payout, so it still counts as having no cost recorded.
    assert totals["accounts_without_cost"] == ["C", "D", "E"]


def test_an_empty_business_has_no_pass_rate_and_zero_money():
    totals = business_totals([], []).model_dump(mode="json")
    assert totals["pass_rate"] is None and (totals["costs"], totals["payouts"], totals["net"]) == ("0.00",) * 3
