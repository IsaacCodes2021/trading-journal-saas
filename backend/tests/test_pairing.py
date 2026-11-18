from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradejournal.grouping import group_fill_pairs
from tradejournal.models import Execution
from tradejournal.pairing import pair_fifo
from tradejournal.timeutil import TradingSession
from tradejournal.trades import build_trade

D = Decimal
T0 = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)


def fills(*specs, symbol="NQ", first_id=1):
    """(side, qty, price, seconds after T0) -> executions whose fill ids follow the order given."""
    return [
        Execution(
            executed_at=T0 + timedelta(seconds=seconds),
            symbol=symbol,
            side=side,
            qty=qty,
            price=D(price),
            ref=f"{symbol}-{n}",
            fill_id=n,
            line=n + 1,
        )
        for n, (side, qty, price, seconds) in enumerate(specs, start=first_id)
    ]


def trades(pairs):
    groups = group_fill_pairs(pairs)
    return [build_trade([pairs[i] for i in g], fee_schedule={"*": D("5")}, session=TradingSession()) for g in groups]


def keys(pairs):
    return [(p.buy_fill_id, p.sell_fill_id, p.qty, p.pnl) for p in pairs]


def test_one_long_round_turn():
    result = pair_fifo(fills(("buy", 1, "27000", 0), ("sell", 1, "27005", 10)))
    [pair] = result.pairs
    assert pair.direction == "long"
    assert (pair.buy_fill_id, pair.sell_fill_id, pair.qty) == (1, 2, 1)
    assert (pair.buy_price, pair.sell_price, pair.pnl) == (D("27000"), D("27005"), D("100.00"))
    assert (pair.bought_at, pair.sold_at, pair.duration_seconds) == (T0, T0 + timedelta(seconds=10), 10)
    assert (pair.tick_size, pair.price_format, pair.price_format_type) == (D("0.25"), "-2", "0")
    assert pair.symbol == "NQ" and pair.line == 3  # the closing fill's line
    assert result.open_lots == []


def test_one_short_round_turn():
    [pair] = pair_fifo(fills(("sell", 2, "27005", 0), ("buy", 2, "27000", 5))).pairs
    assert pair.direction == "short"
    assert (pair.buy_fill_id, pair.sell_fill_id) == (2, 1)
    assert pair.pnl == D("200.00")
    assert pair.entry_at == T0  # the short's entry is the sell


def test_the_oldest_open_contract_closes_first():
    result = pair_fifo(fills(("buy", 1, "27000", 0), ("buy", 1, "27010", 1), ("sell", 1, "27005", 2)))
    assert keys(result.pairs) == [(1, 3, 1, D("100.00"))]
    [lot] = result.open_lots
    assert (lot.side, lot.qty, lot.price, lot.fill_id) == ("buy", 1, D("27010"), 2)


def test_scaling_in_and_out_makes_one_trade():
    result = pair_fifo(
        fills(("buy", 1, "27000", 0), ("buy", 2, "27010", 1), ("sell", 2, "27020", 2), ("sell", 1, "27030", 3))
    )
    assert keys(result.pairs) == [(1, 3, 1, D("400.00")), (2, 3, 1, D("200.00")), (2, 4, 1, D("400.00"))]
    [trade] = trades(result.pairs)
    assert (trade.total_qty, trade.max_position, trade.gross_pnl, trade.net_pnl) == (3, 3, D("1000.00"), D("985.00"))


def test_one_order_that_closes_a_long_and_opens_a_short():
    result = pair_fifo(fills(("buy", 2, "27000", 0), ("sell", 4, "27010", 1), ("buy", 2, "27000", 2)))
    assert [(p.direction, p.qty, p.pnl) for p in result.pairs] == [("long", 2, D("400.00")), ("short", 2, D("400.00"))]
    built = trades(result.pairs)
    assert [(t.direction, t.total_qty) for t in built] == [("long", 2), ("short", 2)]
    assert result.open_lots == []


def test_fills_in_the_same_second_go_by_fill_id():
    buy_first = pair_fifo(fills(("buy", 1, "27000", 0), ("sell", 1, "27002", 0)))
    assert buy_first.pairs[0].direction == "long"
    sell_first = pair_fifo([*fills(("sell", 1, "27002", 0)), *fills(("buy", 1, "27000", 0), first_id=2)])
    assert sell_first.pairs[0].direction == "short"


def test_each_symbol_is_paired_on_its_own_with_its_own_point_value():
    nq = fills(("buy", 1, "27000", 0), ("sell", 1, "27010", 3))
    mnq = fills(("sell", 1, "27005", 1), ("buy", 1, "27000", 2), symbol="MNQ", first_id=3)
    result = pair_fifo([nq[0], mnq[0], mnq[1], nq[1]])
    by_symbol = {p.symbol: p for p in result.pairs}
    assert by_symbol["NQ"].pnl == D("200.00")  # 10 points at $20
    assert by_symbol["MNQ"].pnl == D("10.00")  # 5 points at $2
    assert by_symbol["MNQ"].direction == "short"


def test_an_exact_average_with_many_decimals_rounds_to_the_cent_like_tradesea():
    result = pair_fifo(fills(("buy", 3, "27420.8333333333", 0), ("sell", 1, "27432.75", 1), ("sell", 2, "27435.5", 2)))
    assert [p.pnl for p in result.pairs] == [
        D("238.33"),
        D("586.67"),
    ]  # TradeSea shows $233.33 and $576.67 after $5 fees
    [trade] = trades(result.pairs)
    assert trade.gross_pnl == D("825.00")


def test_contracts_still_open_at_the_end_are_lots_not_pairs():
    result = pair_fifo(fills(("buy", 2, "27000", 0), ("sell", 1, "27004", 1)))
    assert keys(result.pairs) == [(1, 2, 1, D("80.00"))]
    [lot] = result.open_lots
    assert (lot.symbol, lot.side, lot.qty, lot.opened_at) == ("NQ", "buy", 1, T0)


def test_fills_after_a_flat_moment_never_change_the_pairs_before_it():
    morning = fills(("buy", 1, "27000", 0), ("buy", 1, "27002", 1), ("sell", 2, "27006", 2))
    later = fills(("sell", 3, "27010", 10), ("buy", 1, "27001", 11), ("buy", 2, "27003", 12), first_id=4)
    alone = pair_fifo(morning).pairs
    together = pair_fifo([*later, *morning]).pairs  # order given does not matter: times decide
    assert keys(together)[: len(alone)] == keys(alone)


def test_a_contract_missing_from_the_table_is_a_programming_error():
    with pytest.raises(ValueError, match="no contract"):
        pair_fifo(fills(("buy", 1, "10", 0), ("sell", 1, "11", 1), symbol="XYZ"))
