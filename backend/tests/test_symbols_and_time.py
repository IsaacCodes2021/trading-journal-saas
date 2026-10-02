from datetime import date, datetime, time, timezone

import pytest

from helpers import parse_fixture
from tradejournal.symbols import parse_symbol
from tradejournal.timeutil import (
    TradingSession,
    from_utc_iso,
    hold_seconds,
    parse_local_timestamp,
    to_utc_iso,
    trading_date,
)

UTC = timezone.utc


@pytest.mark.parametrize(
    "symbol, day, expected",
    [
        ("NQH4", date(2024, 1, 5), ("NQ", "2024-03")),
        ("MNQM5", date(2025, 6, 20), ("MNQ", "2025-06")),
        ("ESZ3", date(2023, 11, 20), ("ES", "2023-12")),
        ("M2KH4", date(2024, 1, 5), ("M2K", "2024-03")),
        ("ZNH4", date(2024, 1, 5), ("ZN", "2024-03")),
        ("ESZ23", date(2023, 11, 20), ("ES", "2023-12")),
        ("NQH0", date(2029, 12, 20), ("NQ", "2030-03")),  # next decade
        ("NQZ9", date(2030, 1, 2), ("NQ", "2029-12")),  # just expired, previous decade
        ("nqh4", date(2024, 1, 5), ("NQ", "2024-03")),
        ("NQ", date(2024, 1, 5), ("NQ", None)),
    ],
)
def test_parse_symbol(symbol, day, expected):
    assert parse_symbol(symbol, day) == expected


def test_local_timestamps_convert_to_utc_across_dst():
    winter = parse_local_timestamp("01/05/2024 06:30:00", "America/Los_Angeles")
    summer = parse_local_timestamp("03/13/2024 06:03:54", "America/Los_Angeles")
    assert winter == datetime(2024, 1, 5, 14, 30, tzinfo=UTC)  # PST, UTC-8
    assert summer == datetime(2024, 3, 13, 13, 3, 54, tzinfo=UTC)  # PDT, UTC-7


def test_timezone_is_a_parameter():
    assert parse_local_timestamp("01/05/2024 06:30:00", "America/New_York") == datetime(
        2024, 1, 5, 11, 30, tzinfo=UTC
    )
    with pytest.raises(ValueError):
        parse_local_timestamp("01/05/2024 06:30:00", "Mars/Olympus_Mons")


@pytest.mark.parametrize("text", ["", "2024/01/05", "13/45/2024 06:30:00", "01/05/2024 25:00:00"])
def test_bad_timestamps_raise(text):
    with pytest.raises(ValueError):
        parse_local_timestamp(text, "America/Los_Angeles")


def test_iso_round_trip_is_fixed_width():
    moment = datetime(2024, 1, 5, 14, 30, 0, tzinfo=UTC)
    assert to_utc_iso(moment) == "2024-01-05T14:30:00Z"
    assert from_utc_iso("2024-01-05T14:30:00Z") == moment


def test_trading_day_rolls_over_at_six_pm_new_york():
    session = TradingSession()
    # 14:59 Pacific = 17:59 New York: still today.
    assert trading_date(datetime(2024, 3, 19, 21, 59, tzinfo=UTC), session) == date(2024, 3, 19)
    # 16:17 Pacific = 19:17 New York: tomorrow's session.
    assert trading_date(datetime(2024, 3, 19, 23, 17, tzinfo=UTC), session) == date(2024, 3, 20)
    # Sunday evening open belongs to Monday.
    assert trading_date(datetime(2024, 3, 17, 22, 0, tzinfo=UTC), session) == date(2024, 3, 18)


def test_calendar_day_session():
    session = TradingSession(timezone="America/Los_Angeles", start=time(0, 0))
    assert trading_date(datetime(2024, 3, 20, 6, 59, tzinfo=UTC), session) == date(2024, 3, 19)
    assert trading_date(datetime(2024, 3, 20, 7, 0, tzinfo=UTC), session) == date(2024, 3, 20)


def test_hold_time_prefers_csv_duration_over_truncated_timestamps():
    result = parse_fixture("short_and_multiday_2024-01-22.csv")
    first = result.pairs[0]  # 06:29:57 -> 06:30:02 by timestamp, "4sec" per Tradovate
    assert (first.sold_at - first.bought_at).total_seconds() == 5
    assert hold_seconds([first]) == 4
