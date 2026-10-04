"""From trades to the dashboard's numbers. Pure: no database, no clock, so it is tested on plain trade records.

Every definition (what a win is, how drawdown is measured, the six score axes) lives in `finance.py`; this module only
arranges the results the way the single-page app reads them.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .. import finance
from ..queries import TradeRecord
from ..timeutil import to_utc_iso
from .schemas.journal import (
    AccountRefOut,
    AccountSummaryOut,
    DailyResultOut,
    DashboardOut,
    DateRangeOut,
    EquityPointOut,
    PerformanceScoreOut,
    ScoreAxisOut,
    SummaryOut,
)


def build_dashboard(trades: Sequence[TradeRecord]) -> DashboardOut:
    """`trades` in the order they closed, as `queries.load_trades` returns them (drawdown depends on that order)."""
    summary = finance.summarize(trades)
    days = finance.daily_results(trades)
    score = finance.performance_score(summary, days)
    by_trade = finance.equity_curve([t.net_pnl for t in trades])
    by_day = finance.equity_curve([d.net_pnl for d in days])
    return DashboardOut(
        range=DateRangeOut(start=days[0].day, end=days[-1].day) if days else None,
        summary=_summary(summary, account_count=len({t.account_id for t in trades})),
        equity_by_trade=[
            EquityPointOut(at=to_utc_iso(trade.exit_at), equity=_money(point.equity), drawdown=_money(point.drawdown))
            for trade, point in zip(trades, by_trade, strict=True)
        ],
        equity_by_day=[
            EquityPointOut(at=day.day.isoformat(), equity=_money(point.equity), drawdown=_money(point.drawdown))
            for day, point in zip(days, by_day, strict=True)
        ],
        daily=[DailyResultOut(day=d.day, net_pnl=_money(d.net_pnl), trade_count=d.trade_count) for d in days],
        score=PerformanceScoreOut(
            overall=float(score.overall),
            axes=[ScoreAxisOut(name=a.name, score=float(a.score), rule=a.rule) for a in score.axes],
        )
        if score
        else None,
        accounts=_account_summaries(trades),
    )


def _money(value: Decimal) -> Decimal:
    """Money crosses the API as cents ("0.00", never "0" or "-0.00"). Sums of nothing are a bare zero and averages
    divide, so every money value passes through here on its way out."""
    rounded = finance.to_money(value)
    return rounded if rounded != 0 else finance.ZERO.quantize(finance.CENT)


def _cents(value: Decimal | None) -> Decimal | None:
    """`_money` for a value that may be undefined (no wins, no trades)."""
    return None if value is None else _money(value)


def _ratio(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _summary(s: finance.Summary, *, account_count: int) -> SummaryOut:
    return SummaryOut(
        trade_count=s.trade_count,
        account_count=account_count,
        win_count=s.win_count,
        loss_count=s.loss_count,
        breakeven_count=s.breakeven_count,
        gross_pnl=_money(s.gross_pnl),
        fees=_money(s.fees),
        net_pnl=_money(s.net_pnl),
        total_won=_money(s.total_won),
        total_lost=_money(s.total_lost),
        win_rate=_ratio(s.win_rate),
        profit_factor=_ratio(s.profit_factor),
        avg_win=_cents(s.avg_win),
        avg_loss=_cents(s.avg_loss),
        win_loss_ratio=_ratio(s.win_loss_ratio),
        expectancy=_cents(s.expectancy),
        largest_win=_cents(s.largest_win),
        largest_loss=_cents(s.largest_loss),
        trading_days=s.trading_days,
        winning_days=s.winning_days,
        losing_days=s.losing_days,
        max_drawdown=_money(s.max_drawdown),
    )


def _account_summaries(trades: Sequence[TradeRecord]) -> list[AccountSummaryOut]:
    """One row per account that has trades in view, the best net result first (ties by account name)."""
    totals: dict[int, tuple[TradeRecord, int, Decimal]] = {}
    for trade in trades:
        first, count, net = totals.get(trade.account_id, (trade, 0, finance.ZERO))
        totals[trade.account_id] = (first, count + 1, net + trade.net_pnl)
    rows = sorted(totals.values(), key=lambda row: (-row[2], row[0].account_name.lower()))
    return [
        AccountSummaryOut(
            account=AccountRefOut(
                id=first.account_id,
                name=first.account_name,
                firm=first.firm,
                account_type=first.account_type,
            ),
            trade_count=count,
            net_pnl=_money(net),
        )
        for first, count, net in rows
    ]
