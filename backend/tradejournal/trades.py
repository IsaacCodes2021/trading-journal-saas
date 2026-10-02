"""Turn a group of fill pairs into one trade's derived metrics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from . import finance
from .grouping import max_position
from .models import FillPair, TradeMetrics
from .symbols import parse_symbol
from .timeutil import TradingSession, hold_seconds, trading_date


def build_trade(
    pairs: Sequence[FillPair],
    *,
    fee_schedule: Mapping[str, Decimal],
    session: TradingSession,
) -> TradeMetrics:
    if not pairs:
        raise ValueError("a trade needs at least one fill pair")
    symbols = {p.symbol for p in pairs}
    if len(symbols) != 1:
        raise ValueError(f"fill pairs of one trade must share a symbol, got {sorted(symbols)}")
    symbol = symbols.pop()

    entry_at = min(p.entry_at for p in pairs)
    day = trading_date(entry_at, session)
    root, contract_month = parse_symbol(symbol, day)
    tick_size = pairs[0].tick_size
    pnl = finance.trade_pnl(pairs, tick_size, finance.fee_rate_for(fee_schedule, root))

    return TradeMetrics(
        symbol=symbol,
        symbol_root=root,
        contract_month=contract_month,
        direction=pairs[0].direction,
        entry_at=entry_at,
        exit_at=max(p.exit_at for p in pairs),
        trade_date=day,
        hold_seconds=hold_seconds(pairs),
        total_qty=pnl.total_qty,
        max_position=max_position(pairs),
        fill_pair_count=len(pairs),
        tick_size=tick_size,
        avg_entry=pnl.avg_entry,
        avg_exit=pnl.avg_exit,
        points=pnl.points,
        ticks=pnl.ticks,
        gross_pnl=pnl.gross_pnl,
        fees=pnl.fees,
        net_pnl=pnl.net_pnl,
    )
