"""TradeSea's Orders export, read against seven real files.

The golden figures are TradeSea's own: its Positions screen and realized P&L for 2026-11-16 (+$10,055.00) and
2026-11-18 (-$635.00 with $165.00 of charges), both confirmed by the owner. Fees here are $5.00 per contract round trip,
what TradeSea charged. See docs/design/tradesea-import-plan.md, section 1.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tradejournal.grouping import group_fill_pairs
from tradejournal.pairing import pair_fifo
from tradejournal.platforms import tradesea
from tradejournal.platforms.tradesea import (
    ONE_PAGE,
    clean_symbol,
    exact_average,
    parse_time,
    read_order_files,
    read_orders,
)
from tradejournal.timeutil import TradingSession
from tradejournal.trades import build_trade

D = Decimal
FIXTURES = Path(__file__).parent / "fixtures"
TZ = "America/Los_Angeles"
NOV16 = [
    "tradesea_2026-11-16_0937.csv",  # four orders, all filled
    "tradesea_2026-11-16_0952.csv",  # All view: brackets and cancellations; one page
    "tradesea_2026-11-16_1002.csv",  # All view; one page
    "tradesea_2026-11-16_1013_filled.csv",  # Filled view; one page
]
NOV18_PAGES = ["tradesea_2026-11-18_page1_filled.csv", "tradesea_2026-11-18_page2_filled.csv"]
HEADER = '"Time","Symbol","Qty","Side","Order Type","Limit Price","Stop Price","Avg Price","Commission","Status"'

# The three orders of 2026-11-16 that fell outside every 10-row export, as TradeSea's Positions screen showed them.
NOV16_MISSING = [
    ("11/16/2026, 9:49:24 AM PST", "1", "Buy", "27413.75"),
    ("11/16/2026, 9:49:43 AM PST", "1", "Sell", "27407.5"),
    ("11/16/2026, 9:49:56 AM PST", "1", "Sell", "27407.5"),
]


def row(time="11/18/2026, 8:45:09 AM PST", qty="1", side="Buy", avg="27000", *, symbol="CME-Delayed:NQ",
        kind="Market", limit="", stop="", commission="2.5", status="Filled"):  # fmt: skip
    return ",".join(f'"{cell}"' for cell in (time, symbol, qty, side, kind, limit, stop, avg, commission, status))


def csv(*rows, header=HEADER):
    return "\n".join([header, *rows]).encode("utf-8")


def read_files(*names, extra=()):
    files = [(name, (FIXTURES / name).read_bytes()) for name in names]
    return read_order_files([*files, *extra], TZ)


def day(result):
    pairing = pair_fifo(result.executions)
    groups = group_fill_pairs(pairing.pairs)
    built = [
        build_trade([pairing.pairs[i] for i in g], fee_schedule={"*": D("5")}, session=TradingSession()) for g in groups
    ]
    return pairing, built


def nets(built):
    return [t.net_pnl for t in built]


# --- the real files ------------------------------------------------------------------------------------------------


def test_the_first_file_is_two_trades_as_tradesea_shows():
    result = read_files(NOV16[0])
    assert (result.rows, len(result.executions), result.commission) == (4, 4, D("10.0"))
    assert result.errors == [] and result.warnings == []
    first = result.executions[0]
    assert (first.executed_at, first.side, first.qty, first.price) == (
        datetime(2026, 11, 16, 17, 34, 41, tzinfo=UTC),  # 9:34:41 AM PST
        "sell",
        1,
        D("27440"),
    )
    assert [e.fill_id for e in result.executions] == [1, 2, 3, 4]
    pairing, built = day(result)
    assert [(t.direction, t.gross_pnl, t.net_pnl) for t in built] == [
        ("short", D("330.00"), D("325.00")),
        ("long", D("-80.00"), D("-85.00")),
    ]
    assert pairing.open_lots == []


def test_the_all_view_skips_cancelled_orders_and_is_put_in_time_order():
    result = read_files(NOV16[1])
    assert result.skipped == {"cancelled": 4}
    assert len(result.executions) == 6
    times = [e.executed_at for e in result.executions]
    assert times == sorted(times)  # the file itself is not in time order (it is sorted by when orders were placed)
    assert times[0] == datetime(2026, 11, 16, 17, 50, 4, tzinfo=UTC)
    three = next(e for e in result.executions if e.qty == 3)
    assert three.price == D("27420.8333333333")  # shown as 27420.83


def test_cancelled_by_the_system_is_a_cancellation_too():
    assert read_files(NOV16[2]).skipped == {"cancelled": 5}


def test_a_single_page_is_flagged():
    for name in NOV16[1:]:
        assert read_files(name).warnings[0].message == ONE_PAGE, name
    assert all(w.message != ONE_PAGE for w in read_files(NOV16[0]).warnings)  # 4 rows: everything there was


def test_the_filled_view_of_16_november_matches_tradesea_trade_by_trade():
    result = read_files(NOV16[3])
    pairing, built = day(result)
    assert nets(built) == [D("10.00"), D("810.00"), D("1870.00"), D("7510.00")]  # TradeSea: 10, 233.33 + 576.67, ...
    assert result.commission == D("120.0")
    assert pairing.open_lots == []


def test_16_november_without_the_orders_no_export_held_ends_holding_a_contract():
    result = read_files(*NOV16)
    assert result.duplicates_across_files == 10
    pairing, _ = day(result)
    [lot] = pairing.open_lots  # the preview warns: the owner was flat, so the exports left out orders
    assert (lot.side, lot.qty) == ("buy", 1)


def test_16_november_with_every_order_is_tradesea_realized_pnl_to_the_cent():
    missing = ("positions-screen.csv", csv(*(row(t, q, s, p) for t, q, s, p in NOV16_MISSING)))
    result = read_files(*NOV16, extra=[missing])
    pairing, built = day(result)
    assert pairing.open_lots == []
    assert nets(built) == [
        D("325.00"),
        D("-85.00"),
        D("-130.00"),
        D("-255.00"),
        D("10.00"),
        D("810.00"),
        D("1870.00"),
        D("7510.00"),
    ]
    assert sum(nets(built)) == D("10055.00")


def test_18_november_both_pages_are_tradesea_realized_pnl_and_charges():
    result = read_files(*NOV18_PAGES)
    assert (result.rows, len(result.executions), result.duplicates_across_files) == (17, 17, 0)
    assert result.commission == D("165.0")  # TradeSea's Total Charges
    assert all(w.message != ONE_PAGE for w in result.warnings)  # page 2 has 7 rows
    pairing, built = day(result)
    assert pairing.open_lots == []
    assert nets(built) == [D("-1030.00"), D("1695.00"), D("-395.00"), D("2615.00"), D("-3520.00")]
    assert sum(nets(built)) == D("-635.00")


def test_18_november_first_and_last_fill():
    executions = read_files(*NOV18_PAGES).executions
    first, last = executions[0], executions[-1]
    assert (first.executed_at, first.side, first.qty, first.price) == (
        datetime(2026, 11, 18, 16, 44, 47, tzinfo=UTC),  # 8:44:47 AM PST
        "buy",
        5,
        D("27080.45"),
    )
    assert (last.executed_at, last.side, last.qty, last.price) == (
        datetime(2026, 11, 18, 16, 54, 29, tzinfo=UTC),
        "sell",
        10,
        D("26965.75"),
    )


def test_an_overlapping_export_counts_each_order_once():
    alone = read_files(*NOV18_PAGES)
    with_overlap = read_files("tradesea_2026-11-18_0847_filled.csv", *NOV18_PAGES)
    assert with_overlap.duplicates_across_files == 10
    assert [e.ref for e in with_overlap.executions] == [e.ref for e in alone.executions]
    assert nets(day(with_overlap)[1]) == nets(day(alone)[1])


def test_the_847_export_alone_is_one_page_with_two_trades():
    result = read_files("tradesea_2026-11-18_0847_filled.csv")
    assert result.warnings[0].message == ONE_PAGE
    assert nets(day(result)[1]) == [D("-395.00"), D("2615.00")]


# --- times, symbols and prices -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("11/16/2026, 9:37:10 AM PST", datetime(2026, 11, 16, 17, 37, 10, tzinfo=UTC)),
        ("7/1/2026, 9:37:10 AM PDT", datetime(2026, 7, 1, 16, 37, 10, tzinfo=UTC)),
        ("11/16/2026, 12:00:05 PM EST", datetime(2026, 11, 16, 17, 0, 5, tzinfo=UTC)),
        ("11/16/2026, 12:00:05 AM CST", datetime(2026, 11, 16, 6, 0, 5, tzinfo=UTC)),
        ("11/16/2026, 9:37:10 AM CDT", datetime(2026, 11, 16, 14, 37, 10, tzinfo=UTC)),
        ("11/16/2026, 3:37:10 PM UTC", datetime(2026, 11, 16, 15, 37, 10, tzinfo=UTC)),
        ("11/16/2026, 6:37:10 PM GMT+1", datetime(2026, 11, 16, 17, 37, 10, tzinfo=UTC)),
        ("11/16/2026, 9:37:10 AM GMT-5:30", datetime(2026, 11, 16, 15, 7, 10, tzinfo=UTC)),
        ("11/16/2026, 09:56:01 PST", datetime(2026, 11, 16, 17, 56, 1, tzinfo=UTC)),  # as TradeSea's screen writes it
        ("11/16/2026 9:37:10 am pst", datetime(2026, 11, 16, 17, 37, 10, tzinfo=UTC)),
    ],
)
def test_times_use_the_files_own_zone(text, expected):
    assert parse_time(text, "Asia/Tokyo") == (expected, None)  # the setting is not used


def test_a_zone_roundturn_does_not_read_falls_back_to_the_setting():
    assert parse_time("11/16/2026, 9:37:10 AM CET", TZ) == (datetime(2026, 11, 16, 17, 37, 10, tzinfo=UTC), "CET")
    assert parse_time("11/16/2026, 9:37:10 AM", TZ) == (datetime(2026, 11, 16, 17, 37, 10, tzinfo=UTC), "")


@pytest.mark.parametrize(
    "text", ["16/11/2026, 9:37:10 AM PST", "11/16/2026, 13:37:10 PM PST", "2026-11-16 09:37:10", "yesterday"]
)
def test_times_that_are_not_month_day_year_are_refused(text):
    with pytest.raises(ValueError):
        parse_time(text, TZ)


def test_symbols_lose_their_data_feed_prefix():
    assert clean_symbol("CME-Delayed:NQ") == "NQ"
    assert clean_symbol("CME:mnq") == "MNQ"
    assert clean_symbol("NQ") == "NQ"
    with pytest.raises(ValueError):
        clean_symbol("CME:")


@pytest.mark.parametrize(
    ("shown", "qty", "tick", "expected", "exact"),
    [
        ("27420.83", 3, "0.25", "27420.8333333333", True),  # 2026-11-16, 3 contracts
        ("27057.33", 10, "0.25", "27057.325", True),  # 2026-11-18, 10 contracts
        ("27054.63", 2, "0.25", "27054.625", True),
        ("27065.92", 3, "0.25", "27065.9166666667", True),
        ("27040.8", 5, "0.25", "27040.8", True),  # already a whole number of ticks
        ("27405", 1, "0.25", "27405", True),  # one contract fills at one price
        ("27000.01", 30, "0.25", "27000.01", False),  # 30 NQ: too many to tell
        ("75.02", 2, "0.01", "75.02", False),  # 2 crude oil: could be 75.015
        ("75.02", 1, "0.01", "75.02", True),
    ],
)
def test_rounded_averages_are_recovered_only_when_one_answer_fits(shown, qty, tick, expected, exact):
    assert exact_average(D(shown), qty, D(tick)) == (D(expected), exact)


def test_an_average_that_cannot_be_recovered_is_kept_with_a_warning():
    later = "11/18/2026, 8:45:19 AM PST"
    result = read_orders(
        csv(row(avg="75.02", qty="2", symbol="CME:CL"), row(later, "2", "Sell", "75.10", symbol="CME:CL")), TZ
    )
    assert [e.price for e in result.executions] == [D("75.02"), D("75.10")]
    assert len(result.warnings) == 2
    assert "off by up to $10.00" in result.warnings[0].message  # half a cent x 2 contracts x $1,000


# --- the file as a whole ---------------------------------------------------------------------------------------------


def test_a_tradovate_export_is_refused_by_name():
    tradovate = (FIXTURES / "01-05-2024-performance.csv").read_bytes()
    assert read_orders(tradovate, TZ).fatal == "This is a Tradovate Performance export, not a TradeSea one."


def test_missing_columns_and_empty_files_are_refused():
    assert read_orders(csv(row(), header='"Time","Symbol","Qty"'), TZ).fatal.startswith("Not a TradeSea Orders export")
    assert read_orders(b"", TZ).fatal == "The file is empty."
    assert read_orders(b"\n\n", TZ).fatal == "The file is empty."


def test_a_refused_page_refuses_the_import_naming_the_file():
    result = read_order_files([("page 1.csv", csv(row())), ("page 2.csv", b"")], TZ)
    assert result.fatal == "page 2.csv: The file is empty."


def test_columns_in_another_order_extra_columns_crlf_and_a_byte_order_mark_are_read():
    header = '"Status","Avg Price","Commission","Time","Symbol","Qty","Side","Order Type","Limit Price","Stop Price","Account"'
    line = '"Filled","27000","2.5","11/18/2026, 8:45:09 AM PST","CME:NQ","1","Buy","Market","","","x"'
    data = ("﻿" + header + "\r\n" + line + "\r\n").encode("utf-8")
    [execution] = read_orders(data, TZ).executions
    assert (execution.symbol, execution.price, execution.line) == ("NQ", D("27000"), 2)


def test_unquoted_cells_blank_lines_and_a_repeated_header_are_fine():
    data = (
        b"Time,Symbol,Qty,Side,Order Type,Limit Price,Stop Price,Avg Price,Commission,Status\n\n"
        b'"11/18/2026, 8:45:09 AM PST",NQ,1,Buy,Market,,,27000,2.5,Filled\n'
        b"Time,Symbol,Qty,Side,Order Type,Limit Price,Stop Price,Avg Price,Commission,Status\n"
        b'"11/18/2026, 8:45:19 AM PST",NQ,1,Sell,Market,,,27001,2.5,Filled\n'
    )
    result = read_orders(data, TZ)
    assert [e.line for e in result.executions] == [3, 5]
    assert result.rows == 2 and result.errors == []


def test_too_many_rows_are_refused(monkeypatch):
    monkeypatch.setattr(tradesea, "MAX_ROWS", 2)
    assert "at most 2" in read_orders(csv(row(), row(), row()), TZ).fatal


# --- rows --------------------------------------------------------------------------------------------------------------


def test_statuses_other_than_filled_are_counted_and_skipped():
    result = read_orders(
        csv(
            row(status="Cancelled (by trader)", avg="", commission=""),
            row(status="Canceled", avg="", commission=""),
            row(status="Rejected", avg="", commission=""),
            row(status="Working", avg="", commission=""),
            row(),
        ),
        TZ,
    )
    assert result.skipped == {"cancelled": 2, "rejected": 1, "working": 1}
    assert len(result.executions) == 1 and result.rows == 5


def test_a_partly_filled_order_is_refused_in_words():
    result = read_orders(csv(row(status="Cancelled (by trader)", qty="3", avg="27000.25")), TZ)
    [error] = result.errors
    assert error.line == 2 and "partly filled" in error.message
    assert result.executions == []


@pytest.mark.parametrize(
    ("cells", "message"),
    [
        ({"symbol": "CME:XYZ"}, "the contract XYZ is not in Roundturn's contract list"),
        ({"qty": "0"}, "Qty: not a positive whole number"),
        ({"qty": "1.5"}, "Qty: not a positive whole number"),
        ({"side": "Long"}, "Side must be Buy or Sell"),
        ({"avg": ""}, "Avg Price is empty"),
        ({"avg": "abc"}, "Avg Price:"),
        ({"commission": "two"}, "Commission:"),
        ({"time": "16/11/2026, 9:37:10 AM PST"}, "Time:"),
    ],
)
def test_bad_rows_are_reported_by_line_and_the_rest_kept(cells, message):
    good = row(time="11/18/2026, 8:46:00 AM PST")
    kwargs = {"time": "11/18/2026, 8:45:09 AM PST", **cells}
    result = read_orders(csv(good, row(kwargs.pop("time"), **kwargs)), TZ)
    [error] = result.errors
    assert error.line == 3 and message in error.message
    assert len(result.executions) == 1


def test_identical_orders_are_both_kept_with_a_warning():
    result = read_orders(csv(row(), row()), TZ)
    assert len(result.executions) == 2
    assert result.executions[0].ref != result.executions[1].ref
    assert [e.ref[-2:] for e in result.executions] == ["#2", "#1"]  # in time order, the lower line is earlier
    assert "two identical orders" in result.warnings[0].message


def test_a_commission_unlike_the_others_is_flagged():
    result = read_orders(
        csv(
            row(),
            row("11/18/2026, 8:45:19 AM PST", side="Sell"),
            row("11/18/2026, 8:45:29 AM PST", qty="2", commission="9"),
        ),
        TZ,
    )
    [warning] = result.warnings
    assert warning.line == 4 and "commission 9 for 2 contract(s) is not the 2.5 per contract" in warning.message


def test_a_buy_and_a_sell_in_the_same_second_are_flagged():
    result = read_orders(csv(row(side="Sell"), row(side="Buy")), TZ)
    assert any("same second" in w.message for w in result.warnings)


def test_an_unknown_zone_is_warned_once_per_file():
    result = read_orders(csv(row("11/18/2026, 8:45:09 AM CET"), row("11/18/2026, 8:45:19 AM CET", side="Sell")), TZ)
    zone_warnings = [w for w in result.warnings if "time zone" in w.message]
    assert [(w.line, w.message) for w in zone_warnings] == [
        (0, "the time zone 'CET' is not one Roundturn reads, so those times were read as America/Los_Angeles "
            "(your CSV time zone setting)")
    ]  # fmt: skip


def test_issues_name_their_file_when_several_are_read():
    result = read_order_files([("page 1.csv", csv(row())), ("page 2.csv", csv(row(qty="x")))], TZ)
    assert result.errors[0].message.startswith("page 2.csv: Qty:")


# --- references --------------------------------------------------------------------------------------------------------


def test_the_same_order_in_two_exports_has_the_same_reference():
    page = read_files("tradesea_2026-11-18_page1_filled.csv")
    earlier = read_files("tradesea_2026-11-18_0847_filled.csv")
    shared = {e.ref for e in page.executions} & {e.ref for e in earlier.executions}
    assert len(shared) == 7  # 08:45:37 to 08:46:50 are on both


def test_a_reference_depends_on_the_instant_not_how_the_zone_is_written():
    pst = read_orders(csv(row("11/18/2026, 8:45:09 AM PST")), TZ).executions[0]
    utc = read_orders(csv(row("11/18/2026, 4:45:09 PM UTC")), TZ).executions[0]
    assert pst.ref == utc.ref


def test_a_reference_changes_with_what_the_order_is():
    base = read_orders(csv(row()), TZ).executions[0].ref
    for changed in (row(commission="5"), row(avg="27000.25"), row(side="Sell"), row(qty="2"), row(kind="Limit")):
        assert read_orders(csv(changed), TZ).executions[0].ref != base
