"""Reading Roundturn's own import file, version 1 (tradejournal/platforms/other.py): the Other platform route.

The golden file is a made-up session as a chatbot would write it from another platform's export: five round turns on
2026-11-18 in New York time (UTC-05:00), one of them three Micro E-mini S&P contracts bought at different prices (an
average that repeats), one in the evening. Gross +$372.50, fees $25.42, net +$347.08.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from other_files import GOLDEN, HEADER, ROWS, file_of

from tradejournal.parser import MAX_ROWS
from tradejournal.platforms import other, round_turn_monitor, tradesea

D = Decimal
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def read(data: bytes | str) -> other.OtherRead:
    return other.read_file(data, now=NOW)


def only_error(*rows: str) -> str:
    result = read(file_of(*rows))
    assert result.fatal is None and not result.round_turns, result
    [issue] = result.errors
    assert issue.line == 2
    return issue.message


def row(**changes: str) -> str:
    """The first golden row with some cells changed."""
    cells = dict(zip(HEADER.split(","), ROWS[0].split(","), strict=True))
    cells.update(changes)
    return ",".join(cells.values())


# ---- the golden file -------------------------------------------------------------------------------------------------


def test_the_golden_file_reads_every_round_turn_with_the_files_own_totals():
    result = read(GOLDEN)
    assert result.fatal is None and result.errors == [] and result.warnings == []
    assert (result.gross, result.fees, result.net) == (D("372.50"), D("25.42"), D("347.08"))
    assert (result.first_day, result.last_day) == (date(2026, 11, 18), date(2026, 11, 18))
    assert result.offsets == {timedelta(hours=-5)}
    turns = result.round_turns
    assert [(t.entry.symbol, t.entry.side, t.exit.side, t.entry.qty, t.gross) for t in turns] == [
        ("NQZ6", "buy", "sell", 2, D("410.00")),
        ("NQZ6", "sell", "buy", 1, D("-105.00")),
        ("MESZ6", "buy", "sell", 3, D("17.50")),
        ("CLF7", "sell", "buy", 1, D("200.00")),
        ("GCG7", "buy", "sell", 1, D("-150.00")),
    ]
    first = turns[0]
    assert first.entry.executed_at == datetime(2026, 11, 18, 14, 31, 5, tzinfo=UTC)  # 09:31:05 at UTC-05:00
    assert first.exit.executed_at == datetime(2026, 11, 18, 14, 33, 10, tzinfo=UTC)
    assert (first.entry.commission, first.exit.commission, first.commission) == (D("4.50"), D("4.50"), D("9.00"))
    assert first.line == 2 and first.exit.line == 2


def test_an_average_price_rounded_by_the_chatbot_is_put_back_to_the_exact_whole_number_of_ticks():
    """6000.00 + 6000.00 + 6000.25 over 3 contracts is 6000.08333...; written as 6000.08, only one whole number of
    quarter-point ticks over 3 fits, so the exact average is recovered and the gross check passes."""
    rounded = ROWS[2].replace("6000.0833333333", "6000.08")
    [mes] = read(file_of(rounded)).round_turns
    assert mes.entry.price == D("6000.0833333333") and mes.gross == D("17.50")
    assert mes.entry.commission + mes.exit.commission == D("3.72")


def test_an_odd_cent_of_fees_goes_on_the_exit():
    turn = read(file_of(row(fees="9.01", net_pnl="400.99"))).round_turns[0]
    assert (turn.entry.commission, turn.exit.commission) == (D("4.50"), D("4.51"))


def test_as_orders_gives_each_round_turn_its_entry_and_exit_in_time_order_and_names_the_offset():
    orders = read(GOLDEN).as_orders()
    assert orders.rows == 5 and len(orders.executions) == 10
    assert [e.fill_id for e in orders.executions] == list(range(1, 11))
    times = [e.executed_at for e in orders.executions]
    assert times == sorted(times)
    assert {e.ref[-3:] for e in orders.executions} == {":in", "out"}
    assert orders.zones == {"UTC-05:00"} and orders.commission == D("25.42")
    assert orders.rates == {"NQ": D("4.50"), "MES": D("1.24"), "CL": D("4.00"), "GC": D("4.20")}


def test_a_reversal_at_one_moment_puts_the_exit_before_the_next_entry():
    flip = file_of(
        "1,NQZ6,long,1,2026-11-18T09:31:05-05:00,21000.25,2026-11-18T09:33:10-05:00,21010.50,205.00,0,205.00",
        "1,NQZ6,short,1,2026-11-18T09:33:10-05:00,21010.50,2026-11-18T09:35:00-05:00,21000.00,210.00,0,210.00",
    )
    sides = [(e.side, e.ref[-4:]) for e in read(flip).as_orders().executions]
    assert sides == [("buy", "1:in"), ("sell", ":out"), ("sell", "1:in"), ("buy", ":out")]


# ---- the same round turns, converted again -----------------------------------------------------------------------------


def test_the_same_round_turns_converted_again_and_written_differently_get_the_same_references():
    """Fix the refused rows and import the corrected file: what was imported already must be recognised, however the
    chatbot writes it the second time."""
    columns = HEADER.split(",")
    shuffled = list(reversed(columns))
    rewritten = []
    for line in ROWS:
        cells = dict(zip(columns, line.split(","), strict=True))
        cells["entry_price"] = cells["entry_price"] + ("0" if "." in cells["entry_price"] else ".00")
        for column in ("entry_time", "exit_time"):  # the same instants, written in UTC
            cells[column] = datetime.fromisoformat(cells[column]).astimezone(UTC).isoformat().replace("+00:00", "Z")
        cells["fees"], cells["net_pnl"] = "0", cells["gross_pnl"]  # a fee guessed differently: not part of the ref
        cells["side"] = cells["side"].upper()
        rewritten.append(",".join(cells[c] for c in shuffled))
    again = read(file_of(*rewritten, header=",".join(c.upper() for c in shuffled), eol="\n"))
    assert again.errors == []
    refs = lambda result: [(t.entry.ref, t.exit.ref) for t in result.round_turns]  # noqa: E731
    assert refs(again) == refs(read(GOLDEN))


def test_two_identical_round_turns_are_both_kept_with_their_own_references_and_a_warning():
    result = read(file_of(ROWS[0], ROWS[0]))
    first, second = result.round_turns
    assert first.entry.ref.endswith("#1:in") and second.entry.ref.endswith("#2:in")
    assert first.entry.ref[:-5] == second.entry.ref[:-5]
    [warning] = result.warnings
    assert warning.line == 3 and "both are kept" in warning.message


# ---- files refused as a whole --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"", "The file is empty."),
        (b"\r\n\r\n", "The file is empty."),
        (b"name,age\nalice,31\n", other.NOT_OURS),
        (file_of(header=HEADER), other.NO_ROWS),
    ],
)
def test_an_empty_file_or_one_that_is_not_roundturns_is_refused_whole(data, expected):
    assert read(data).fatal == expected


def test_the_header_must_have_exactly_the_versions_columns():
    missing = read(file_of(ROWS[0], header=HEADER.replace(",net_pnl", ""))).fatal
    assert missing.startswith("The header row is not Roundturn's import file version 1: it has no net_pnl.")
    extra = read(file_of(ROWS[0] + ",LFE01", header=HEADER + ",account")).fatal
    assert "it has account, which the format does not" in extra
    both = read(file_of(ROWS[0], header=HEADER.replace("fees", "commission"))).fatal
    assert "it has no fees and it has commission, which the format does not" in both
    doubled = read(file_of(ROWS[0] + ",1", header=HEADER + ",qty")).fatal
    assert doubled.startswith("The column qty is in the header twice.")
    assert other.START_AGAIN in missing and other.START_AGAIN in doubled


def test_a_trailing_comma_on_every_line_is_not_an_extra_column():
    result = read(file_of(*(r + "," for r in ROWS), header=HEADER + ","))
    assert result.fatal is None and len(result.round_turns) == 5


def test_another_version_of_the_format_is_refused_whole():
    assert read(file_of(ROWS[0], "2" + ROWS[1][1:])).fatal == other.OTHER_VERSION.format(line=3, value="2")
    assert read(file_of("," + ROWS[0][2:])).fatal == other.OTHER_VERSION.format(line=2, value="")


def test_the_supported_platforms_own_exports_are_sent_to_their_own_choice():
    tradesea_file = (",".join(tradesea.COLUMNS) + "\n").encode()
    assert "Choose TradeSea" in read(tradesea_file).fatal
    log = (",".join(round_turn_monitor.COLUMNS) + "\n").encode()
    assert "Round Turn Monitor log" in read(log).fatal


def test_a_file_longer_than_one_import_takes_is_refused():
    big = file_of(*[ROWS[0]] * (MAX_ROWS + 1))
    assert "one import takes at most" in read(big).fatal


def test_one_file_at_a_time():
    assert other.read_files([("a.csv", GOLDEN), ("b.csv", GOLDEN)]).fatal.startswith("Choose one file at a time")
    assert other.read_files([("a.csv", GOLDEN)], now=NOW).net == D("347.08")


# ---- rows refused, with the reason, by line --------------------------------------------------------------------------------


def test_a_gross_pnl_the_prices_do_not_make_is_refused_with_what_they_do_make():
    """The check that catches a wrong contract, side, quantity or price: NQ written as if it were MNQ ($2 a point)."""
    message = only_error(row(gross_pnl="41.00", net_pnl="32.00"))
    assert message == (
        "gross_pnl 41.00 is not what the prices make: 2 NQ long from 21000.25 to 21010.5 is 410.00 ($20 a point). "
        "Check the contract, side, qty and prices"
    )
    assert "is -410.00" in only_error(row(side="short"))  # long and short swapped
    assert "is 4100.00" in only_error(row(exit_price="21102.75"))  # a price misread by a digit


def test_a_cent_either_way_is_rounding_not_a_difference():
    assert read(file_of(row(gross_pnl="410.01", net_pnl="401.01"))).errors == []
    assert read(file_of(row(gross_pnl="409.99", net_pnl="400.99"))).errors == []
    assert only_error(row(gross_pnl="410.02", net_pnl="401.02")).startswith("gross_pnl 410.02 is not")


def test_net_pnl_must_be_gross_minus_fees():
    assert only_error(row(net_pnl="410.00")) == "net_pnl 410.00 is not gross_pnl 410.00 minus fees 9.00 (401.00)"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"entry_time": "2026-11-18T09:31:05"}, "entry_time '2026-11-18T09:31:05' has no UTC offset"),
        ({"exit_time": "11/18/2026 9:33:10 AM"}, "exit_time '11/18/2026 9:33:10 AM' is not a time like"),
        ({"exit_time": "2026-11-18T09:30:00-05:00"}, "exit_time 2026-11-18T09:30:00-05:00 is before entry_time"),
        ({"exit_time": "2026-11-21T09:33:10-05:00"}, "exit_time 2026-11-21T09:33:10-05:00 is in the future"),
        ({"entry_time": "1999-12-31T09:31:05-05:00"}, "entry_time 1999-12-31T09:31:05-05:00 is before 2000"),
        ({"symbol": "XYZZ6"}, "the contract XYZZ6 is not in Roundturn's contract list"),
        ({"symbol": "CME:NQZ6"}, "symbol 'CME:NQZ6' is not a contract such as NQZ6 or NQ"),
        ({"symbol": ""}, "symbol is empty"),
        ({"side": "buy"}, "side must be long or short, got 'buy'"),
        ({"qty": "0"}, "qty: not a positive whole number: '0'"),
        ({"qty": "1.5"}, "qty: not a positive whole number: '1.5'"),
        ({"entry_price": "-1"}, "entry_price must be positive"),
        ({"entry_price": "abc"}, "entry_price: not a number"),
        ({"exit_price": ""}, "exit_price is empty"),
        ({"fees": "-9.00", "net_pnl": "419.00"}, "fees must be zero or more"),
        ({"gross_pnl": ""}, "gross_pnl is empty"),
        ({"net_pnl": "lots"}, "net_pnl: not a money amount"),
    ],
)
def test_a_row_that_cannot_be_read_is_refused_with_its_reason(changes, expected):
    assert only_error(row(**changes)).startswith(expected)


def test_one_contract_must_fill_at_a_whole_number_of_ticks():
    message = only_error(row(qty="1", entry_price="21000.30", gross_pnl="204.00", net_pnl="195.00"))
    assert message == "entry_price 21000.30 is not a whole number of ticks (0.25) for one contract"


def test_an_unquoted_thousands_separator_shifts_the_cells_and_is_refused():
    shifted = row().replace("410.00", "410.00,5")  # one more cell than the header has
    assert only_error(shifted) == "has 12 cells, more than the 11 columns"


def test_good_rows_are_kept_when_others_are_refused_and_the_totals_count_only_the_good_ones():
    result = read(file_of(ROWS[0], row(net_pnl="1"), ROWS[1]))
    assert [issue.line for issue in result.errors] == [3]
    assert len(result.round_turns) == 2 and result.net == D("291.50")  # 401.00 - 109.50


def test_a_file_whose_every_row_is_refused_has_no_round_turns_but_says_why():
    result = read(file_of(row(side="buy"), row(qty="0")))
    assert result.fatal is None and result.round_turns == [] and len(result.errors) == 2
    assert (result.first_day, result.last_day, result.net) == (None, None, D("0"))


def test_a_repeated_header_and_blank_lines_are_skipped():
    result = read(file_of(ROWS[0], "", HEADER, ROWS[1]))
    assert result.errors == [] and len(result.round_turns) == 2


def test_a_byte_order_mark_does_not_hide_the_header():
    assert read(b"\xef\xbb\xbf" + GOLDEN).net == D("347.08")


def test_dates_are_the_files_own_and_offsets_across_a_daylight_saving_change_are_all_kept():
    late = "1,NQZ6,long,1,2026-11-02T23:59:00-05:00,21000.00,2026-11-03T00:01:00-05:00,21001.00,20.00,0,20.00"
    summer = "1,NQZ6,long,1,2026-10-30T09:31:00-04:00,21000.00,2026-10-30T09:32:00-04:00,21001.00,20.00,0,20.00"
    result = read(file_of(late, summer))
    assert (result.first_day, result.last_day) == (date(2026, 10, 30), date(2026, 11, 3))
    assert result.offsets == {timedelta(hours=-5), timedelta(hours=-4)}
    assert result.as_orders().zones == {"UTC-05:00", "UTC-04:00"}


def test_offset_names_read_back_as_zones():
    assert other.offset_name(timedelta(hours=-5)) == "UTC-05:00"
    assert other.offset_name(timedelta(hours=5, minutes=30)) == "UTC+05:30"
    zone = other.display_zone("UTC-05:00")
    assert zone is not None and zone.utcoffset(None) == timedelta(hours=-5)
    assert other.display_zone("America/New_York") is None and other.display_zone("UTC-xx:00") is None


# ---- the front end says the same -------------------------------------------------------------------------------------


def test_the_front_end_prompt_asks_for_the_columns_version_and_contracts_the_reader_knows():
    """`frontend/src/lib/otherPlatform.ts` holds the prompt the trader gives the chatbot. If the reader or the contract
    table changes, this fails until the prompt says the same, or chatbots would write files Roundturn refuses."""
    import re
    from pathlib import Path

    from tradejournal.contracts import CONTRACTS
    from tradejournal.finance import plain

    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "otherPlatform.ts").read_text(
        encoding="utf-8"
    )
    columns = source.split("export const OTHER_COLUMNS = [")[1].split("] as const")[0]
    assert tuple(re.findall(r"'([^']+)'", columns)) == other.COLUMNS
    assert re.search(r"export const OTHER_FORMAT_VERSION = (\d+)", source).group(1) == other.VERSION
    table = source.split("export const CONTRACTS")[1].split("\n]\n")[0]
    listed = re.findall(r"\['([A-Z0-9]+)', '([0-9.]+)'\]", table)
    assert listed == [(c.root, plain(c.point_value)) for c in CONTRACTS.values()]


def test_the_example_file_on_the_docs_page_is_one_roundturn_reads_without_a_refused_row():
    """`site/public/docs/import/other/index.html` shows a short example file; it must be one the reader takes whole."""
    import html
    import re
    from pathlib import Path

    page = (
        Path(__file__).resolve().parents[2] / "site" / "public" / "docs" / "import" / "other" / "index.html"
    ).read_text(encoding="utf-8")
    example = html.unescape(re.search(r"<pre><code>(.*?)</code></pre>", page, re.DOTALL).group(1))
    result = read(example)
    assert result.fatal is None and result.errors == [] and result.warnings == []
    assert len(result.round_turns) == 2 and result.net == D("406.01")  # 401.00 + 5.01


def test_a_whole_session_converted_by_following_the_prompt_reads_to_its_own_total():
    """The made-up session of PR 153's rebuilt exports (14 Oct 2026, gross -696.50), converted from its NinjaTrader 8
    Executions file by following the prompt step by step: Los Angeles times; a scale-out (exit average 25012.625); a
    scale-in whose average repeats (written to 8 decimals); one order filled in two parts; a reversal in one fill,
    which closes one round turn and opens the next, its commission split between them; crude oil; gold in the evening.
    The fees are the export's own commissions, $27.04 in all."""
    session = file_of(
        "1,NQZ6,long,2,2026-10-14T06:31:05-07:00,25012.25,2026-10-14T06:35:40-07:00,25012.625,15.00,5.16,9.84",
        "1,MNQZ6,short,3,2026-10-14T07:02:00-07:00,25034.33333333,2026-10-14T07:06:15-07:00,25021.25,78.50,3.12,75.38",
        "1,ESZ6,long,4,2026-10-14T08:15:20-07:00,6720.50,2026-10-14T08:40:02-07:00,6714.75,-1150.00,10.32,-1160.32",
        "1,CLX6,short,1,2026-10-14T10:05:00-07:00,58.42,2026-10-14T10:20:30-07:00,58.17,250.00,3.08,246.92",
        "1,MESZ6,long,1,2026-10-14T11:10:00-07:00,6725.00,2026-10-14T11:12:30-07:00,6728.50,17.50,1.04,16.46",
        "1,MESZ6,short,1,2026-10-14T11:12:30-07:00,6728.50,2026-10-14T11:15:45-07:00,6726.00,12.50,1.04,11.46",
        "1,GCZ6,long,1,2026-10-14T15:30:10-07:00,4102.3,2026-10-14T15:41:55-07:00,4103.1,80.00,3.28,76.72",
    )
    result = read(session)
    assert result.fatal is None and result.errors == [] and result.warnings == []
    assert (result.gross, result.fees, result.net) == (D("-696.50"), D("27.04"), D("-723.54"))
    from tradejournal.pairing import pair_fifo

    pairs = pair_fifo(result.as_orders().executions).pairs
    assert len(pairs) == 7 and sum(pair.pnl for pair in pairs) == D("-696.50")
