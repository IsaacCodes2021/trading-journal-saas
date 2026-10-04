"""The row cap (doc 04, S7): a file is refused before it is parsed when it has more rows than one import takes."""

import pytest
from helpers import HEADER, TZ

from tradejournal import parser
from tradejournal.parser import MAX_ROWS, parse_performance_csv


def csv(rows: int, line: str = "", ending: str = "\n") -> str:
    return HEADER + ending + (line + ending) * rows


def test_the_cap_is_fifty_thousand_rows():
    assert MAX_ROWS == 50_000


def test_a_file_at_the_cap_is_read_and_one_row_over_is_refused():
    # Blank rows: read like any other line, but cheap to make, so the real limit is tested and not a stand-in.
    at_cap = parse_performance_csv(csv(MAX_ROWS), TZ)
    assert at_cap.fatal is None
    over = parse_performance_csv(csv(MAX_ROWS + 1), TZ)
    assert over.fatal is not None
    assert "50,001 rows" in over.fatal
    assert "50,000" in over.fatal
    assert over.pairs == [] and over.errors == []  # nothing was parsed


def test_the_refusal_tells_the_person_what_to_do():
    message = parse_performance_csv(csv(MAX_ROWS + 5), TZ).fatal
    assert message is not None
    assert "by month" in message


def test_a_last_line_with_no_newline_after_it_is_counted_too():
    assert parse_performance_csv(HEADER + "\n" + "\n" * (MAX_ROWS - 1) + "x", TZ).fatal is None  # 50,000 lines
    assert parse_performance_csv(HEADER + "\n" + "\n" * MAX_ROWS + "x", TZ).fatal is not None  # 50,001 lines


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r"])
def test_every_kind_of_line_ending_is_counted(ending):
    assert parse_performance_csv(csv(MAX_ROWS, ending=ending), TZ).fatal is None
    assert parse_performance_csv(csv(MAX_ROWS + 1, ending=ending), TZ).fatal is not None


def test_the_header_is_checked_before_the_rows_are_counted():
    # A huge file of the wrong kind still gets the more useful answer.
    message = parse_performance_csv("a,b,c\n" + "1,2,3\n" * (MAX_ROWS + 10), TZ).fatal
    assert message is not None and "Not a Tradovate Performance export" in message


def test_a_small_cap_applies_to_real_rows(monkeypatch):
    monkeypatch.setattr(parser, "MAX_ROWS", 2)
    row = "NQH4,2,0,0.25,1,2,1,100,101,20,01/05/2024 06:30:00,01/05/2024 06:30:05,5sec"
    assert parse_performance_csv(HEADER + "\n" + row + "\n" + row + "\n", TZ).fatal is None
    refused = parse_performance_csv(HEADER + "\n" + (row + "\n") * 3, TZ).fatal
    assert refused is not None and "3 rows" in refused and "at most 2" in refused
