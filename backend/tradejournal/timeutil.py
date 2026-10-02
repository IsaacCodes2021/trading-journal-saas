"""Timestamp parsing, UTC storage format, and trading-day assignment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import FillPair

_CSV_FORMATS = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S")
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@lru_cache(maxsize=None)
def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone: {name!r}") from None


def parse_local_timestamp(text: str, tz_name: str) -> datetime:
    """Parse a CSV timestamp written in tz_name local time and return it in UTC.

    An ambiguous wall time during the autumn DST change resolves to its first
    occurrence (fold=0).
    """
    cleaned = text.strip()
    for fmt in _CSV_FORMATS:
        try:
            naive = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=zone(tz_name)).astimezone(timezone.utc)
    raise ValueError(f"not a timestamp: {text!r} (expected MM/DD/YYYY HH:MM:SS)")


def to_utc_iso(moment: datetime) -> str:
    """Storage format. Fixed width, so stored timestamps sort and compare as text."""
    return moment.astimezone(timezone.utc).strftime(_ISO_FORMAT)


def from_utc_iso(text: str) -> datetime:
    return datetime.strptime(text, _ISO_FORMAT).replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class TradingSession:
    """When a trading day begins.

    The default follows CME Globex: the session opening at 18:00 New York time
    belongs to the next calendar day, so a 16:30 Pacific trade on the 19th counts
    toward the 20th. Set start to 00:00 to use plain calendar days.
    """

    timezone: str = "America/New_York"
    start: time = time(18, 0)


def trading_date(moment: datetime, session: TradingSession) -> date:
    local = moment.astimezone(zone(session.timezone)).replace(tzinfo=None)
    day = timedelta(days=1)
    shift = (day - timedelta(hours=session.start.hour, minutes=session.start.minute)) % day
    return (local + shift).date()


def hold_seconds(pairs: Sequence[FillPair]) -> int:
    """Seconds from a trade's first entry to its last exit.

    CSV timestamps are truncated to the second, but Tradovate's duration column is
    computed from the exact fill times ("4sec" where the timestamps say 5). When one
    fill pair spans the whole trade, its duration is the more precise answer.
    """
    entry = min(p.entry_at for p in pairs)
    exit_ = max(p.exit_at for p in pairs)
    spanning = [p.duration_seconds for p in pairs if p.entry_at == entry and p.exit_at == exit_]
    if spanning:
        return max(spanning)
    return int((exit_ - entry).total_seconds())
