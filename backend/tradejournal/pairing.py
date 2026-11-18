"""Pair executions first in, first out (FIFO) into fill pairs.

A platform like TradeSea exports fills (or filled orders) with no pairing, no P&L and no fill IDs. This module turns
them into the same `FillPair` rows a Tradovate Performance export gives, so grouping into trades (`grouping.py`), P&L
and fees (`finance.py`) and everything after them stay exactly as they are.

Per symbol, fills are taken in time order (`executed_at`, then `fill_id`). A fill on the side opposite the open position
closes the oldest open contracts first; whatever is left of it opens a new position, so one order that sells 4 while
long 2 closes the long and opens a short of 2. Pairing is deterministic and restarts at every flat moment: adding fills
after a flat moment never changes the pairs before it.

Contracts still open at the end are returned as open lots, not pairs. An open lot at the end of a day usually means the
export left out an order, and the preview says so.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .contracts import Contract, contract_for, price_format
from .finance import to_money
from .models import Execution, FillPair, Side
from .symbols import parse_symbol


@dataclass(frozen=True)
class OpenLot:
    """Contracts still open after the last fill: what remains of one opening fill."""

    symbol: str
    side: Side  # the side that opened it: buy means long
    qty: int
    price: Decimal
    opened_at: datetime
    fill_id: int


@dataclass
class PairingResult:
    pairs: list[FillPair] = field(default_factory=list)
    open_lots: list[OpenLot] = field(default_factory=list)


def contract_of(symbol: str, when: datetime) -> Contract | None:
    """The contract a stored symbol ("NQ", "NQZ6") trades, or None when the root is not in the contract table."""
    root, _ = parse_symbol(symbol, when.date())
    return contract_for(root)


def pair_fifo(executions: Iterable[Execution]) -> PairingResult:
    """Pair the executions of one account. Every symbol's contract must be in the contract table: the readers refuse
    rows whose contract is unknown, so a missing one here is a programming error."""
    by_symbol: dict[str, list[Execution]] = defaultdict(list)
    for execution in executions:
        by_symbol[execution.symbol].append(execution)

    result = PairingResult()
    for symbol, fills in by_symbol.items():
        fills.sort(key=lambda e: (e.executed_at, e.fill_id))
        contract = contract_of(symbol, fills[0].executed_at)
        if contract is None:
            raise ValueError(f"no contract for {symbol!r}: the reader should have refused these rows")
        lots: deque[list] = deque()  # [opening execution, contracts still open]; all on one side
        for fill in fills:
            left = fill.qty
            while left and lots and lots[0][0].side != fill.side:
                opener, still_open = lots[0]
                qty = min(still_open, left)
                result.pairs.append(_pair(opener, fill, qty, contract))
                left -= qty
                if qty == still_open:
                    lots.popleft()
                else:
                    lots[0][1] = still_open - qty
            if left:
                lots.append([fill, left])
        result.open_lots.extend(
            OpenLot(symbol, opener.side, qty, opener.price, opener.executed_at, opener.fill_id) for opener, qty in lots
        )
    return result


def _pair(opener: Execution, closer: Execution, qty: int, contract: Contract) -> FillPair:
    buy, sell = (opener, closer) if opener.side == "buy" else (closer, opener)
    price_fmt, price_fmt_type = price_format(contract.tick_size)
    return FillPair(
        symbol=opener.symbol,
        tick_size=contract.tick_size,
        price_format=price_fmt,
        price_format_type=price_fmt_type,
        buy_fill_id=buy.fill_id,
        sell_fill_id=sell.fill_id,
        qty=qty,
        buy_price=buy.price,
        sell_price=sell.price,
        pnl=to_money((sell.price - buy.price) * qty * contract.point_value),
        bought_at=buy.executed_at,
        sold_at=sell.executed_at,
        duration_raw="",
        duration_seconds=int(abs((sell.executed_at - buy.executed_at).total_seconds())),
        direction="long" if opener.side == "buy" else "short",
        line=closer.line,
    )
