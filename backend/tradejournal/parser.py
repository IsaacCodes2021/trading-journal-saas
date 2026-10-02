"""Parse Tradovate "Performance" CSV exports into FillPair records.

The parser never raises on bad rows. It reports them with their line number and
keeps going, so the import preview can show exactly what will and won't be imported.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from . import finance
from .models import Direction, FillPair
from .symbols import parse_symbol
from .timeutil import parse_local_timestamp

REQUIRED_COLUMNS = (
    "symbol", "_priceFormat", "_priceFormatType", "_tickSize", "buyFillId", "sellFillId",
    "qty", "buyPrice", "sellPrice", "pnl", "boughtTimestamp", "soldTimestamp", "duration",
)  # fmt: skip

_DURATION_UNITS = {
    "d": 86400, "day": 86400, "days": 86400,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "m": 60, "min": 60, "mins": 60,
    "s": 1, "sec": 1, "secs": 1,
}  # fmt: skip
_DURATION_TOKEN = re.compile(r"(\d+)\s*([a-z]+)")


@dataclass(frozen=True)
class RowIssue:
    line: int
    message: str


@dataclass
class ParseResult:
    pairs: list[FillPair] = field(default_factory=list)
    errors: list[RowIssue] = field(default_factory=list)  # rows skipped
    warnings: list[RowIssue] = field(default_factory=list)  # rows kept, but something looked off
    duplicates_in_file: int = 0
    fatal: str | None = None  # the whole file was rejected


class _RowError(ValueError):
    pass


def parse_duration(text: str) -> int | None:
    """ "4sec" -> 4, "1min 5sec" -> 65, "1h 2min" -> 3720. None when empty or unrecognized."""
    cleaned = text.strip().lower()
    total = 0
    position = 0
    for match in _DURATION_TOKEN.finditer(cleaned):
        unit = _DURATION_UNITS.get(match[2])
        if unit is None or cleaned[position : match.start()].strip():
            return None
        total += int(match[1]) * unit
        position = match.end()
    if position == 0 or cleaned[position:].strip():
        return None
    return total


def infer_direction(bought_at: datetime, sold_at: datetime, buy_fill_id: int, sell_fill_id: int) -> Direction:
    """Long if the buy came first, short if the sell did.

    When both fills carry the same second, fall back to fill IDs: Tradovate assigns
    them in execution order (true for every row in the sample exports), so the lower
    ID was the entry.
    """
    if bought_at != sold_at:
        return "long" if bought_at < sold_at else "short"
    return "long" if buy_fill_id < sell_fill_id else "short"


def parse_performance_csv(data: bytes | str, tz_name: str) -> ParseResult:
    """Parse one export. Timestamps are read as tz_name local time and stored as UTC."""
    text = _decode(data)
    header_problem = _check_header(text)
    if header_problem:
        return ParseResult(fatal=header_problem)

    try:
        frame = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skip_blank_lines=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        return ParseResult(fatal=f"Could not read the CSV: {exc}")

    # With blank lines kept, row i of the frame is line i + 2 of the file (line 1 is the header).
    frame = frame.fillna("").rename(columns=str.strip)
    frame = frame.apply(lambda column: column.str.strip())
    frame.insert(0, "_line", frame.index + 2)

    blank = (frame[list(REQUIRED_COLUMNS)] == "").all(axis=1)
    repeated_header = frame["symbol"].str.lower() == "symbol"  # concatenated exports
    frame = frame[~blank & ~repeated_header]

    duplicate = frame.duplicated(subset=["buyFillId", "sellFillId"], keep="first")
    result = ParseResult(duplicates_in_file=int(duplicate.sum()))

    for record in frame[~duplicate].to_dict("records"):
        line = int(record["_line"])
        try:
            pair, warnings = _parse_row(record, line, tz_name)
        except _RowError as exc:
            result.errors.append(RowIssue(line, str(exc)))
            continue
        result.pairs.append(pair)
        result.warnings.extend(RowIssue(line, w) for w in warnings)
    return result


def _decode(data: bytes | str) -> str:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("cp1252")
    else:
        text = data.removeprefix("﻿")
    return text.replace("\x1a", "")  # DOS end-of-file marker some tools append


def _check_header(text: str) -> str | None:
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return "The file is empty."
    header = [column.strip() for column in next(csv.reader([first_line]))]
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if not missing:
        return None
    if first_line.startswith(("Working Orders", "Completed Orders")) or header[:1] == ["Account"]:
        return (
            "This looks like an order-history export (Rithmic / R|Trader style), not a "
            "Tradovate Performance export. Export Performance from Tradovate instead."
        )
    return f"Not a Tradovate Performance export: missing column(s) {', '.join(missing)}."


def _parse_row(record: dict[str, Any], line: int, tz_name: str) -> tuple[FillPair, list[str]]:
    warnings: list[str] = []
    symbol = record["symbol"].upper()
    if not symbol:
        raise _RowError("symbol is empty")

    tick_size = _field(record, "_tickSize", finance.parse_decimal)
    if tick_size <= 0:
        raise _RowError(f"_tickSize must be positive, got {record['_tickSize']!r}")
    buy_fill_id = _field(record, "buyFillId", _parse_id)
    sell_fill_id = _field(record, "sellFillId", _parse_id)
    qty = _field(record, "qty", _parse_positive_int)
    buy_price = _field(record, "buyPrice", finance.parse_decimal)
    sell_price = _field(record, "sellPrice", finance.parse_decimal)
    pnl = _field(record, "pnl", finance.parse_money)
    bought_at = _field(record, "boughtTimestamp", lambda v: parse_local_timestamp(v, tz_name))
    sold_at = _field(record, "soldTimestamp", lambda v: parse_local_timestamp(v, tz_name))

    duration_raw = record["duration"]
    duration_seconds = parse_duration(duration_raw)
    if duration_seconds is None:
        if duration_raw:
            warnings.append(f"duration {duration_raw!r} not understood; used the timestamps instead")
        duration_seconds = int(abs((sold_at - bought_at).total_seconds()))

    pair = FillPair(
        symbol=symbol,
        tick_size=tick_size,
        price_format=record["_priceFormat"],
        price_format_type=record["_priceFormatType"],
        buy_fill_id=buy_fill_id,
        sell_fill_id=sell_fill_id,
        qty=qty,
        buy_price=buy_price,
        sell_price=sell_price,
        pnl=pnl,
        bought_at=bought_at,
        sold_at=sold_at,
        duration_raw=duration_raw,
        duration_seconds=duration_seconds,
        direction=infer_direction(bought_at, sold_at, buy_fill_id, sell_fill_id),
        line=line,
    )

    root, _ = parse_symbol(symbol, bought_at.date())
    point_value = finance.POINT_VALUES.get(root)
    if point_value is not None:
        expected = finance.expected_pnl(pair, point_value)
        if expected != pnl:
            warnings.append(f"pnl {pnl} does not match prices x qty (expected {expected}); kept the CSV value")
    return pair, warnings


def _field(record: dict[str, Any], column: str, convert):
    value = record[column]
    if value == "":
        raise _RowError(f"{column} is empty")
    try:
        return convert(value)
    except ValueError as exc:
        raise _RowError(f"{column}: {exc}") from None


def _parse_id(text: str) -> int:
    if not text.isdigit():
        raise ValueError(f"not a fill id: {text!r}")
    return int(text)


def _parse_positive_int(text: str) -> int:
    if not text.isdigit() or int(text) <= 0:
        raise ValueError(f"not a positive whole number: {text!r}")
    return int(text)
