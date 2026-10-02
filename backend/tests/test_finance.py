from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradejournal import finance
from tradejournal.models import FillPair

D = Decimal


def make_pair(direction, qty, entry, exit_, pnl, *, buy_id=1, sell_id=2) -> FillPair:
    long = direction == "long"
    t0 = datetime(2024, 1, 5, 14, 30, tzinfo=UTC)
    t1 = datetime(2024, 1, 5, 14, 31, tzinfo=UTC)
    return FillPair(
        symbol="NQH4",
        tick_size=D("0.25"),
        price_format="-2",
        price_format_type="0",
        buy_fill_id=buy_id,
        sell_fill_id=sell_id,
        qty=qty,
        buy_price=D(entry if long else exit_),
        sell_price=D(exit_ if long else entry),
        pnl=D(pnl),
        bought_at=t0 if long else t1,
        sold_at=t1 if long else t0,
        duration_raw="1min",
        duration_seconds=60,
        direction=direction,
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("$625.00", "625.00"),
        ("$(475.00)", "-475.00"),
        ("$4,400.00", "4400.00"),
        ("$(9,999.99)", "-9999.99"),
        ("-$12.50", "-12.50"),
        ("$-12.50", "-12.50"),
        ("($3.25)", "-3.25"),
        ("  $1.00 ", "1.00"),
        ("$0.00", "0.00"),
        ("$(0.00)", "0.00"),
        ("42", "42"),
    ],
)
def test_parse_money(text, expected):
    value = finance.parse_money(text)
    assert value == D(expected)
    assert str(value) == expected  # exact representation, and never "-0.00"


@pytest.mark.parametrize("text", ["", "$", "abc", "$(5.00", "$5.00)", "-$(5.00)", "$5.00.00", "1e5"])
def test_parse_money_rejects_junk(text):
    with pytest.raises(ValueError):
        finance.parse_money(text)


def test_money_str_and_plain_are_canonical():
    assert finance.money_str(D("-475")) == "-475.00"
    assert finance.money_str(D("-0.001")) == "0.00"
    assert finance.money_str(D("41.205")) == "41.21"  # half up
    assert finance.plain(D("16445.50")) == "16445.5"
    assert finance.plain(D("16460.000000")) == "16460"
    assert finance.plain(D("-0.000000")) == "0"


def test_weighted_average_price():
    legs = [(5, D("16457.00")), (3, D("16460.75")), (2, D("16461.00"))]
    assert finance.weighted_average_price(legs) == D("16458.925")
    thirds = finance.weighted_average_price([(1, D("1")), (2, D("2"))])
    assert thirds == D("1.666667")
    with pytest.raises(ValueError):
        finance.weighted_average_price([])


def test_fee_rate_lookup_and_round_trip_fees():
    schedule = {"*": D("4.12"), "MNQ": D("1.52")}
    assert finance.fee_rate_for(schedule, "NQ") == D("4.12")
    assert finance.fee_rate_for(schedule, "MNQ") == D("1.52")
    assert finance.fee_rate_for({}, "NQ") == D("0")
    assert finance.round_trip_fees(10, D("4.12")) == D("41.20")
    assert finance.round_trip_fees(3, D("0.625")) == D("1.88")


def test_trade_pnl_long_scale_in():
    # Trade F of the sample file: three entries, one exit.
    pairs = [
        make_pair("long", 5, "16457.00", "16465.00", "800.00", buy_id=735, sell_id=750),
        make_pair("long", 3, "16460.75", "16465.00", "255.00", buy_id=741, sell_id=750),
        make_pair("long", 2, "16461.00", "16465.00", "160.00", buy_id=744, sell_id=750),
    ]
    result = finance.trade_pnl(pairs, D("0.25"), D("4.00"))
    assert result.total_qty == 10
    assert result.avg_entry == D("16458.925")
    assert result.avg_exit == D("16465")
    assert result.points == D("6.075")
    assert result.ticks == D("24.3")
    assert result.gross_pnl == D("1215.00")
    assert result.fees == D("40.00")
    assert result.net_pnl == D("1175.00")


def test_trade_pnl_short_points_are_positive_when_price_falls():
    pairs = [make_pair("short", 2, "18279.25", "18235.25", "1760.00")]
    result = finance.trade_pnl(pairs, D("0.25"), D("0"))
    assert result.avg_entry == D("18279.25")
    assert result.avg_exit == D("18235.25")
    assert result.points == D("44")
    assert result.ticks == D("176")
    assert result.net_pnl == D("1760.00")


def test_trade_pnl_points_come_from_exact_sums_not_rounded_averages():
    pairs = [
        make_pair("long", 1, "100.00", "100.25", "5.00", buy_id=1, sell_id=4),
        make_pair("long", 1, "100.00", "100.25", "5.00", buy_id=2, sell_id=4),
        make_pair("long", 1, "100.25", "100.25", "0.00", buy_id=3, sell_id=4),
    ]
    result = finance.trade_pnl(pairs, D("0.25"), D("0"))
    assert result.avg_entry == D("100.083333")  # rounded for display
    assert result.points == D("0.166667")  # 0.5 / 3, not 100.25 - 100.083333
    assert result.ticks == D("0.6667")


def test_trade_pnl_rejects_mixed_directions():
    with pytest.raises(ValueError):
        finance.trade_pnl(
            [make_pair("long", 1, "1", "2", "20"), make_pair("short", 1, "2", "1", "20")], D("0.25"), D("0")
        )


def test_expected_pnl_matches_csv_convention():
    pair = make_pair("long", 5, "16450.25", "16445.50", "-475.00")
    assert finance.expected_pnl(pair, finance.POINT_VALUES["NQ"]) == D("-475.00")
