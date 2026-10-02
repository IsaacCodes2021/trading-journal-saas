import random
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from helpers import SAMPLE, lines_of, parse_fixture
from tradejournal.grouping import group_fill_pairs, max_position
from tradejournal.timeutil import TradingSession
from tradejournal.trades import build_trade

D = Decimal
FEES = {"*": D("4.00")}


def trades_for(name):
    result = parse_fixture(name)
    groups = group_fill_pairs(result.pairs)
    built = [
        build_trade([result.pairs[i] for i in g], fee_schedule=FEES, session=TradingSession())
        for g in groups
    ]
    return result, groups, built


def test_sample_file_collapses_14_rows_into_8_trades():
    result = parse_fixture(SAMPLE)
    assert len(result.pairs) == 14
    assert len(group_fill_pairs(result.pairs)) == 8


def test_sample_file_grouping_by_line():
    result, groups, _ = trades_for(SAMPLE)
    assert lines_of(result, groups) == [
        [2], [3], [4], [5], [6],
        [7, 8, 9],  # scaled in: three buys share sell fill ...750
        [10, 11, 12, 13],  # scaled in: four buys share sell fill ...784
        [14, 15],  # scaled out: one buy (...798) split across two sells
    ]  # fmt: skip


def test_sample_trade_metrics():
    _, _, trades = trades_for(SAMPLE)
    assert [t.gross_pnl for t in trades] == [
        D("-475.00"), D("625.00"), D("-925.00"), D("-400.00"),
        D("150.00"), D("1215.00"), D("670.00"), D("175.00"),
    ]  # fmt: skip
    assert sum(t.gross_pnl for t in trades) == D("1035.00")
    assert sum(t.fees for t in trades) == D("200.00")  # 50 contracts x $4
    assert sum(t.net_pnl for t in trades) == D("835.00")
    assert {t.direction for t in trades} == {"long"}
    assert {t.symbol_root for t in trades} == {"NQ"}
    assert {t.contract_month for t in trades} == {"2024-03"}
    assert {str(t.trade_date) for t in trades} == {"2024-01-05"}


def test_scale_in_trade():
    _, _, trades = trades_for(SAMPLE)
    trade = trades[5]
    assert trade.fill_pair_count == 3
    assert trade.total_qty == 10
    assert trade.max_position == 10
    assert trade.avg_entry == D("16458.925")
    assert trade.avg_exit == D("16465")
    assert trade.points == D("6.075")
    assert trade.ticks == D("24.3")
    assert trade.gross_pnl == D("1215.00")
    assert trade.fees == D("40.00")
    assert trade.net_pnl == D("1175.00")
    assert trade.hold_seconds == 29


def test_scale_out_trade():
    _, _, trades = trades_for(SAMPLE)
    trade = trades[7]
    assert trade.fill_pair_count == 2
    assert trade.total_qty == 5
    assert trade.max_position == 5  # one 5-lot entry, not 4 + 1 separately
    assert trade.points == D("1.75")
    assert trade.ticks == D("7")
    assert trade.hold_seconds == 39


def test_overlapping_positions_merge_without_shared_fills():
    # Lines 17-18: buy 2, buy 2, sell 2, sell 2. No fill id is shared, but the
    # position never went flat, so it is one trade that peaked at 4 contracts.
    result, groups, trades = trades_for("reversal_2023-11-20.csv")
    grouped = lines_of(result, groups)
    assert [17, 18] in grouped
    trade = trades[grouped.index([17, 18])]
    assert trade.total_qty == 4
    assert trade.max_position == 4


def test_reversal_fill_splits_into_two_trades():
    # Buy fill ...417 covered a 1-lot short (line 30) and opened a 1-lot long (line 31).
    result, groups, trades = trades_for("reversal_2023-11-20.csv")
    grouped = lines_of(result, groups)
    assert [30] in grouped and [31] in grouped
    assert trades[grouped.index([30])].direction == "short"
    assert trades[grouped.index([31])].direction == "long"
    assert len(result.pairs) == 31
    assert len(groups) == 27


def test_symbols_never_share_a_trade():
    result, groups, trades = trades_for("reversal_2023-11-20.csv")
    mnq = [t for t in trades if t.symbol == "MNQZ3"]
    assert len(mnq) == 1 and mnq[0].symbol_root == "MNQ"


def test_chained_shorts():
    result, groups, trades = trades_for("chained_shorts_2024-03-13.csv")
    grouped = lines_of(result, groups)
    assert len(result.pairs) == 38
    assert len(groups) == 31
    for chain in ([6, 7], [11, 12], [13, 14], [20, 21], [22, 23], [27, 28], [30, 31]):
        assert chain in grouped
    trade = trades[grouped.index([6, 7])]
    assert trade.direction == "short"
    assert trade.max_position == 10
    assert trade.gross_pnl == D("-775.00")


def test_evening_session_and_large_scale_in():
    result, groups, trades = trades_for("evening_session_2024-03-20.csv")
    grouped = lines_of(result, groups)
    assert grouped == [[2], [3], [4], [5], list(range(6, 15)), [15, 16, 17, 18], [19]]
    big = trades[4]
    assert big.total_qty == 33
    assert big.max_position == 33
    assert big.hold_seconds == 409  # line 7 spans the whole trade: "6min 49sec"
    # 16:17 Pacific on 3/19 is 19:17 New York: part of the 3/20 session.
    assert {str(t.trade_date) for t in trades} == {"2024-03-20"}
    # Line 2's exit fill (...017) opened line 3's short: a reversal.
    assert (trades[0].direction, trades[1].direction) == ("long", "short")


def test_same_second_short_stands_alone():
    result, groups, trades = trades_for("same_second_2023-11-17.csv")
    grouped = lines_of(result, groups)
    assert len(result.pairs) == 27
    assert len(groups) == 19
    assert [12] in grouped and [13] in grouped
    assert trades[grouped.index([12])].direction == "short"


def test_grouping_does_not_depend_on_row_order():
    result = parse_fixture("chained_shorts_2024-03-13.csv")
    expected = sorted(sorted(g) for g in lines_of(result, group_fill_pairs(result.pairs)))
    shuffled = result.pairs[:]
    for seed in range(5):
        random.Random(seed).shuffle(shuffled)
        groups = [[shuffled[i].line for i in g] for g in group_fill_pairs(shuffled)]
        assert sorted(sorted(g) for g in groups) == expected


def test_max_position_with_partial_exit_then_add():
    # buy 5 -> sell 3 -> buy 3 -> sell 5: peak is 5 even though 8 contracts traded.
    base = parse_fixture(SAMPLE).pairs[0]
    t = base.bought_at
    pairs = [
        replace(base, buy_fill_id=1, sell_fill_id=2, qty=3, bought_at=t, sold_at=t + timedelta(seconds=10)),
        replace(base, buy_fill_id=1, sell_fill_id=4, qty=2, bought_at=t, sold_at=t + timedelta(seconds=30)),
        replace(base, buy_fill_id=3, sell_fill_id=4, qty=3, bought_at=t + timedelta(seconds=20), sold_at=t + timedelta(seconds=30)),
    ]  # fmt: skip
    assert len(group_fill_pairs(pairs)) == 1
    assert max_position(pairs) == 5
