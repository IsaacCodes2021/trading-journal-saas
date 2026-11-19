"""Telling which platform wrote a file from its header row (tradejournal/platforms/detect.py)."""

from helpers import HEADER, SAMPLE, fixture_bytes

from tradejournal.platforms import detect, round_turn_monitor

TRADESEA_HEADER = (
    '"Time","Symbol","Qty","Side","Order Type","Limit Price","Stop Price","Avg Price","Commission","Status"'
)


def test_each_platforms_header_names_it_in_any_column_order_and_with_extra_columns():
    assert detect.platform_of(fixture_bytes(SAMPLE)) == detect.TRADOVATE
    assert detect.platform_of(fixture_bytes("tradesea_2026-11-18_page1_filled.csv")) == detect.TRADESEA
    shuffled = ",".join(reversed(TRADESEA_HEADER.split(","))) + ',"Account"\n'
    assert detect.platform_of(shuffled.encode()) == detect.TRADESEA
    assert detect.platform_of(HEADER + ",extra\n") == detect.TRADOVATE  # text, as well as bytes


def test_the_round_turn_monitors_log_names_it_and_so_do_the_two_files_its_reader_refuses_in_words():
    assert detect.platform_of(fixture_bytes("rtm_log_2026-11-18.csv")) == detect.ROUND_TURN_MONITOR
    assert detect.platform_of(fixture_bytes("rtm_export_2026-11-18.csv")) == detect.ROUND_TURN_MONITOR
    old = ",".join(round_turn_monitor.OLD_COLUMNS) + "\n"
    assert detect.platform_of(old.encode()) == detect.ROUND_TURN_MONITOR
    log, tradesea = fixture_bytes("rtm_log_2026-11-18.csv"), TRADESEA_HEADER.encode()
    assert detect.platform_of_files([log, log]) == (detect.ROUND_TURN_MONITOR, None)
    assert detect.platform_of_files([log, tradesea]) == (None, detect.MIXED)
    assert detect.SOURCES[round_turn_monitor.SOURCE] == detect.ROUND_TURN_MONITOR


def test_a_byte_order_mark_and_blank_first_lines_do_not_hide_the_header():
    assert detect.platform_of(b"\xef\xbb\xbf\r\n\r\n" + TRADESEA_HEADER.encode() + b"\r\n") == detect.TRADESEA


def test_a_file_that_is_neither_or_empty_names_no_platform():
    for data in (b"", b"\n\n", b"name,age\nalice,31\n", TRADESEA_HEADER.replace('"Status"', '"State"').encode()):
        assert detect.platform_of(data) is None


def test_a_big_file_is_judged_by_its_head_without_cutting_a_character_in_half():
    row = '"11/18/2026, 8:45:34 AM PST","CME-Delayed:NQ","2","Sell","Limit","27049.25","","27054.63","5","Filled — é"\n'
    big = (TRADESEA_HEADER + "\n" + row * 2000).encode()
    assert len(big) > 64 * 1024 and detect.platform_of(big) == detect.TRADESEA


def test_several_files_must_all_come_from_one_platform():
    tradovate, tradesea = fixture_bytes(SAMPLE), TRADESEA_HEADER.encode()
    assert detect.platform_of_files([tradesea, tradesea]) == (detect.TRADESEA, None)
    assert detect.platform_of_files([tradesea, tradovate]) == (None, detect.MIXED)
    assert detect.platform_of_files([tradesea, b"name,age\n"]) == (None, detect.NEITHER)


def test_the_front_end_lists_the_same_tradesea_columns_the_reader_needs():
    """`frontend/src/lib/platforms.ts` shows TradeSea's columns on the Help page; if the reader changes, this fails until
    the front end says the same."""
    import re
    from pathlib import Path

    from tradejournal.platforms import tradesea

    source = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "platforms.ts").read_text(
        encoding="utf-8"
    )
    block = source.split("export const TRADESEA_COLUMNS = [")[1].split("] as const")[0]
    assert tuple(re.findall(r"'([^']+)'", block)) == tradesea.COLUMNS
    assert int(re.search(r"export const TRADESEA_PAGE_ROWS = (\d+)", source).group(1)) == tradesea.PAGE_ROWS
