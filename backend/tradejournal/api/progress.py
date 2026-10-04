"""How accounts are doing: progress against their rules, and whether the prop business pays. Pure.

**Progress is worked out from closed trades only.** The profit target is the net result of the account's trades as a
share of the target; the drawdown left is the allowed drawdown less how far the cumulative result sits below its
highest point so far (the peak starts at zero). A firm's own figure can differ, most of all for an intraday trailing
drawdown, which depends on open-trade highs an export does not contain: the page says so, and the trailing type and
lock level are stored but not yet part of this arithmetic (doc 05, note A, the rule tracker, is after the beta).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

from .. import finance
from ..accounts import Account
from ..ledger import LedgerEntry
from ..queries import TradeRecord
from .accounts_view import account_out
from .cents import cents
from .schemas.accounts import AccountOverviewOut, AccountProgressOut, BusinessTotalsOut


def account_progress(account: Account, trades: Iterable[TradeRecord]) -> AccountProgressOut:
    """`trades` are this account's, in the order they closed."""
    net = peak = finance.ZERO
    count = 0
    last_day = None
    for trade in trades:
        net += trade.net_pnl
        peak = max(peak, net)
        count += 1
        last_day = trade.trade_date if last_day is None else max(last_day, trade.trade_date)
    target = account.profit_target
    return AccountProgressOut(
        net_pnl=cents(net),
        trade_count=count,
        target_progress=float(min(Decimal(1), max(finance.ZERO, net / target))) if target and target > 0 else None,
        drawdown_left=cents(max(finance.ZERO, account.max_drawdown - (peak - net)))
        if account.max_drawdown is not None
        else None,
        last_trading_day=last_day,
    )


def overviews(accounts: Sequence[Account], trades: Iterable[TradeRecord]) -> list[AccountOverviewOut]:
    """Every account with its progress. `trades` may be every trade the person has, in closing order."""
    by_account: dict[int, list[TradeRecord]] = {a.id: [] for a in accounts}
    for trade in trades:
        by_account.setdefault(trade.account_id, []).append(trade)
    return [
        AccountOverviewOut(account=account_out(a), progress=account_progress(a, by_account[a.id])) for a in accounts
    ]


def business_totals(accounts: Sequence[Account], entries: Sequence[LedgerEntry]) -> BusinessTotalsOut:
    totals = finance.business_totals(entries)
    evaluations = [a for a in accounts if a.account_type == "evaluation"]
    passed = sum(1 for a in evaluations if a.outcome == "passed")
    failed = sum(1 for a in evaluations if a.outcome == "failed")
    with_cost = {e.account_id for e in entries if not e.is_payout}
    return BusinessTotalsOut(
        evaluations_passed=passed,
        evaluations_failed=failed,
        pass_rate=passed / (passed + failed) if passed + failed else None,
        costs=cents(totals.costs),
        payouts=cents(totals.payouts),
        net=cents(totals.net),
        accounts_without_cost=[a.name for a in accounts if a.id not in with_cost],
    )
