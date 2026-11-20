"""From trades to a day or week share card. Pure: no database, no clock, so it is tested on plain trade records."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal

from .. import finance
from ..queries import TradeRecord
from .cents import cents
from .dashboard import summary_of
from .schemas.share import CardTradeOut, PeriodCardOut


def _card_trade(trade: TradeRecord | None) -> CardTradeOut | None:
    if trade is None:
        return None
    return CardTradeOut(
        id=trade.id,
        symbol=trade.symbol,
        direction=trade.direction,
        trade_day=trade.trade_date,
        entry_at=trade.entry_at,
        total_qty=trade.total_qty,
        net_pnl=cents(trade.net_pnl),
        points=trade.points,
    )


def _net(trade: TradeRecord) -> Decimal:
    return trade.net_pnl


def _points(trade: TradeRecord) -> Decimal:
    return trade.points


def _top(trades: Sequence[TradeRecord], key: Callable[[TradeRecord], Decimal]) -> CardTradeOut | None:
    """The trade with the highest value. On a tie, the one that closed first: `max` keeps the first of equals, and the
    trades come in the order they closed. The same for `_bottom`."""
    return _card_trade(max(trades, key=key)) if trades else None


def _bottom(trades: Sequence[TradeRecord], key: Callable[[TradeRecord], Decimal]) -> CardTradeOut | None:
    return _card_trade(min(trades, key=key)) if trades else None


def build_period_card(trades: Sequence[TradeRecord], start: date, end: date, *, number: int) -> PeriodCardOut:
    """`trades` in the order they closed, as `queries.load_trades` returns them."""
    return PeriodCardOut(
        start=start,
        end=end,
        number=number,
        summary=summary_of(trades),
        contracts=sum(t.total_qty for t in trades),
        net_points=sum((t.points for t in trades), finance.ZERO),
        accounts=sorted({t.account_name for t in trades}, key=str.lower),
        best=_top(trades, _net),
        worst=_bottom(trades, _net),
        best_points=_top(trades, _points),
        worst_points=_bottom(trades, _points),
    )
