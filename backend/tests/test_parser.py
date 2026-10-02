from datetime import UTC, datetime
from decimal import Decimal

import pytest
from helpers import HEADER, SAMPLE, TZ, parse_fixture

from tradejournal.parser import parse_duration, parse_performance_csv

UTC = UTC


def test_sample_file_parses_all_rows_cleanly():
    result = parse_fixture(SAMPLE)
    assert result.fatal is None
    assert len(result.pairs) == 14
    assert result.errors == []
    assert result.warnings == []  # every P&L matches prices x qty x $20
    assert result.duplicates_in_file == 0


def test_first_sample_row_fields():
    first = parse_fixture(SAMPLE).pairs[0]
    assert first.line == 2
    assert first.symbol == "NQH4"
    assert first.tick_size == Decimal("0.25")
    assert (first.buy_fill_id, first.sell_fill_id) == (36245250665, 36245250671)
    assert first.qty == 5
    assert (first.buy_price, first.sell_price) == (Decimal("16450.25"), Decimal("16445.50"))
    assert first.pnl == Decimal("-475.00")
    assert first.bought_at == datetime(2024, 1, 5, 14, 30, 0, tzinfo=UTC)  # 06:30 Pacific
    assert first.sold_at == datetime(2024, 1, 5, 14, 30, 5, tzinfo=UTC)
    assert first.duration_seconds == 4
    assert first.direction == "long"


def test_sample_pnl_total():
    assert sum(p.pnl for p in parse_fixture(SAMPLE).pairs) == Decimal("1035.00")


def test_sold_before_bought_is_short():
    long_row, short_row = parse_fixture("short_and_multiday_2024-01-22.csv").pairs
    assert long_row.direction == "long"
    assert short_row.direction == "short"
    assert short_row.entry_fill_id == short_row.sell_fill_id
    assert short_row.entry_price == Decimal("17420.00")
    assert short_row.exit_price == Decimal("17413.25")


def test_same_second_direction_comes_from_fill_order():
    # Line 12: bought and sold both at 06:30:11. The sell fill id is lower, so the
    # sell happened first and the row is a short; the naive <= rule would say long.
    result = parse_fixture("same_second_2023-11-17.csv")
    row = next(p for p in result.pairs if p.line == 12)
    assert row.bought_at == row.sold_at
    assert row.sell_fill_id < row.buy_fill_id
    assert row.direction == "short"
    assert row.duration_raw == ""
    assert row.duration_seconds == 0


def test_thousands_separator_and_trailing_space_duration():
    result = parse_fixture("evening_session_2024-03-20.csv")
    by_line = {p.line: p for p in result.pairs}
    assert by_line[2].duration_raw == "1min"  # "1min " in the file
    assert by_line[2].duration_seconds == 60
    assert by_line[3].pnl == Decimal("4400.00")
    assert by_line[3].duration_seconds == 2 * 3600 + 25 * 60 + 32


MESSY = (
    "﻿" + HEADER + "\n"
    "MNQH4,-2,0,0.25,100,105,2,17000.00,17010.00,$40.00,01/10/2024 06:30:00,01/10/2024 06:31:05,1min \n"  # 2
    'ESH4,-2,0,0.25,110,115,1,4800.00,4790.00,"$(500.00)",01/10/2024 06:32:00,01/10/2024 06:32:30,30sec\n'  # 3
    "\n"  # 4: blank
     + HEADER + "\n"  # 5: header repeated by a concatenated export
    "NQH4,-2,0,0.25,120,125,1,17000.00,17001.00,$20.00,01/10/2024 06:40:00,01/10/2024 06:40:03,\n"  # 6
    "NQH4,-2,0,0.25,120,125,1,17000.00,17001.00,$20.00,01/10/2024 06:40:00,01/10/2024 06:40:03,\n"  # 7: duplicate
    "NQH4,-2,0,0.25,130,135,1,17000.00,abc,$20.00,01/10/2024 06:41:00,01/10/2024 06:41:03,3sec\n"  # 8
    "NQH4,-2,0,0.25,140,145,1,17000.00,17001.00,$20.00,13/45/2024 06:42:00,01/10/2024 06:42:03,3sec\n"  # 9
    "NQH4,-2,0,0.25,150,155,1,17000.00,17001.00,$25.00,01/10/2024 06:43:00,01/10/2024 06:43:03,3sec\n"  # 10
    "NQH4,-2,0,0.25,160,165,1,17000.00,17001.00,$20.00,01/10/2024 06:44:00,01/10/2024 06:44:02,soon\n"  # 11
    "\x1a"  # DOS end-of-file marker
)


def test_messy_export():
    result = parse_performance_csv(MESSY.encode("utf-8"), TZ)
    assert result.fatal is None
    assert [p.line for p in result.pairs] == [2, 3, 6, 10, 11]
    assert result.duplicates_in_file == 1
    assert [(e.line, e.message.split(":")[0]) for e in result.errors] == [
        (8, "sellPrice"),
        (9, "boughtTimestamp"),
    ]
    assert [w.line for w in result.warnings] == [10, 11]
    assert "expected 20.00" in result.warnings[0].message
    assert "not understood" in result.warnings[1].message

    by_line = {p.line: p for p in result.pairs}
    assert by_line[2].duration_seconds == 60
    assert by_line[3].pnl == Decimal("-500.00")
    assert by_line[6].duration_seconds == 3  # empty duration: computed from timestamps
    assert by_line[11].duration_seconds == 2


def test_rejects_order_history_exports():
    text = (
        "Working Orders\n"
        '"Account","Status","Buy/Sell","Qty To Fill","Symbol"\n\n\n'
        "Completed Orders\n"
        '"Account","Status","Remarks","Buy/Sell","Qty To Fill","Symbol","Avg Fill Price"\n'
        '"TPT0000000","Filled","","S","1","NQU5","21861.0000"\n'
    )
    result = parse_performance_csv(text.encode(), TZ)
    assert result.pairs == []
    assert "order-history export" in result.fatal


def test_rejects_unrelated_csv_listing_missing_columns():
    result = parse_performance_csv(b"date,amount\n2024-01-05,12.50\n", TZ)
    assert result.fatal.startswith("Not a Tradovate Performance export")
    assert "buyFillId" in result.fatal


def test_rejects_empty_file():
    assert parse_performance_csv(b"", TZ).fatal == "The file is empty."


def test_header_only_file_has_no_rows():
    result = parse_performance_csv((HEADER + "\n").encode(), TZ)
    assert result.fatal is None
    assert result.pairs == []


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("4sec", 4),
        ("1min 5sec", 65),
        ("1min ", 60),
        ("1h 2min", 3720),
        ("2h 25min 32sec", 8732),
        ("1d 1h", 90000),
        ("  3 sec ", 3),
        ("", None),
        ("soon", None),
        ("5", None),
        ("5sec later", None),
        ("5 fortnights", None),
    ],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds
