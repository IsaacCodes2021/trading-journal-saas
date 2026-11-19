"""Read Roundturn's own import file: the "Other platform" route, for every platform Roundturn has no reader for.

Roundturn does not read the other platform's export. The trader gives it, with the prompt the Import page copies
(`frontend/src/lib/otherPlatform.ts`), to an AI chatbot of their choice, which writes this file: one row per round turn,
in a fixed format with a version. Roundturn then does what it does best, strictly: it refuses a file that is not in the
format, refuses each row that does not add up, and the trader checks the file's net total against their own platform's
statement before importing (the Import page compares the two). Bad output from the chatbot stops here, not in the
journal.

The format, version 1. A header row with exactly these columns, in any order:

* `roundturn_version`: `1` on every row.
* `symbol`: the contract as Tradovate writes it (`NQZ6`, `NQZ26`), or its root alone (`NQ`) when the export has no
  month. The root must be in Roundturn's contract table (`contracts.py`), which sets the dollars per point.
* `side`: `long` or `short`.
* `qty`: the contracts bought (and sold) from flat back to flat, a whole number.
* `entry_time`, `exit_time`: ISO 8601 with the UTC offset, `2026-11-18T09:31:05-05:00`. The first opening fill, and the
  fill that made the position flat. A time without an offset is refused: the file must say its own zone.
* `entry_price`, `exit_price`: the average price of the opening fills and of the closing fills, in the contract's own
  quoting units, not rounded. A rounded average is put back to the exact one when only one whole number of ticks fits.
* `gross_pnl`: dollars before fees. It must be what the prices make (prices x contracts x dollars per point), to the
  cent, or the row is refused: a wrong contract, side, quantity or price shows up here.
* `fees`: the round turn's commissions and fees, both sides, zero or more.
* `net_pnl`: `gross_pnl` minus `fees`, to the cent.

Each round turn is stored as two orders, its entry and its exit, and paired with the account's other orders of the same
source, like the Round Turn Monitor's (`pg/executions.py`). Each order's reference is built from what the round turn is
(contract, side, contracts, times as instants, prices as numbers), never from the fees or P&L, so the same trades
converted again, even written a little differently, are recognised and imported once. That is what makes "fix the
refused rows and import the corrected file" safe. As for every import, a trade's fees come from the account's fee
setting; the file's fees are shown beside it.
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from decimal import ROUND_DOWN, Decimal

from .. import finance
from ..models import Execution
from ..pairing import contract_of
from ..parser import MAX_ROWS, RowIssue, _decode
from ..parser import REQUIRED_COLUMNS as TRADOVATE_COLUMNS
from . import round_turn_monitor, tradesea
from .round_turn_monitor import RoundTurn

SOURCE = "roundturn_csv"
VERSION = "1"
COLUMNS = (
    "roundturn_version",
    "symbol",
    "side",
    "qty",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "gross_pnl",
    "fees",
    "net_pnl",
)

MONEY_SLACK = Decimal("0.01")  # a cent either way is rounding, not a difference
FUTURE_SLACK = timedelta(days=1)  # a time later than this from now is not a trade that happened
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)
_CENT = Decimal("0.01")

START_AGAIN = (
    "On the Import page, choose Other platform, copy the prompt again, and give it to the AI chatbot with your export."
)
NOT_OURS = "This is not Roundturn's import file. " + START_AGAIN
NO_ROWS = "The file has its header row but no round turns."
OTHER_VERSION = (
    "Line {line}: roundturn_version is {value!r}, and Roundturn reads version 1 of its import file. " + START_AGAIN
)


@dataclass
class OtherRead:
    """The file's round turns, what was wrong with the others, and the file's own totals over the round turns kept."""

    round_turns: list[RoundTurn] = field(default_factory=list)  # in entry order
    errors: list[RowIssue] = field(default_factory=list)  # rows refused
    warnings: list[RowIssue] = field(default_factory=list)
    gross: Decimal = finance.ZERO
    fees: Decimal = finance.ZERO
    net: Decimal = finance.ZERO
    offsets: set[timedelta] = field(default_factory=set)  # the UTC offsets the file's times were written with
    first_day: date | None = None  # the first entry's date, as the file wrote it (in its own offset)
    last_day: date | None = None  # the last exit's date, the same way
    fatal: str | None = None

    def as_orders(self) -> tradesea.ReadResult:
        """The round turns as orders, ready to store, ordered as the Round Turn Monitor's are (at one moment, an exit
        before the next entry, and a round turn's own entry before its exit)."""
        log = round_turn_monitor.LogRead(
            round_turns=self.round_turns,
            errors=[("", issue) for issue in self.errors],
            warnings=[("", issue) for issue in self.warnings],
        )
        orders = log.for_account("")
        orders.zones = {offset_name(offset) for offset in self.offsets}  # the import records "UTC-05:00"
        return orders


@dataclass(frozen=True)
class _Row:
    turn: RoundTurn  # its two orders' refs are the round turn's own, without which identical row it is or :in, :out
    net: Decimal
    entry_local: datetime  # as the file wrote it, with its offset
    exit_local: datetime


def offset_name(offset: timedelta) -> str:
    """`UTC-05:00`, the way the preview names the zone a file's times were written in."""
    total = int(offset.total_seconds()) // 60
    sign = "-" if total < 0 else "+"
    hours, minutes = divmod(abs(total), 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def display_zone(name: str) -> tzinfo | None:
    """The fixed offset a `UTC-05:00` name stands for, else None."""
    if len(name) != 9 or not name.startswith("UTC") or name[3] not in "+-" or name[6] != ":":
        return None
    try:
        hours, minutes = int(name[4:6]), int(name[7:9])
    except ValueError:
        return None
    sign = -1 if name[3] == "-" else 1
    return timezone(sign * timedelta(hours=hours, minutes=minutes), name)


def read_file(data: bytes | str, *, now: datetime | None = None) -> OtherRead:
    """Read one file. `now` (for tests) is when the import runs: a time more than a day after it is refused."""
    now = now or datetime.now(UTC)
    text = _decode(data)
    lines = text.splitlines()
    first = next((line for line in lines if line.strip()), "")
    if not first:
        return OtherRead(fatal="The file is empty.")
    header = [column.strip() for column in next(csv.reader([first]))]
    refused = _check_header(header)
    if refused:
        return OtherRead(fatal=refused)
    if len(lines) - 1 > MAX_ROWS:
        return OtherRead(
            fatal=f"That file has {len(lines) - 1:,} rows, and one import takes at most {MAX_ROWS:,}. "
            "Ask the chatbot for a shorter period at a time, and import the parts one after another."
        )

    result = OtherRead()
    occurrences: Counter[str] = Counter()
    reader = csv.reader(io.StringIO(text))
    columns: list[str] | None = None
    for row in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        if columns is None:
            columns = [cell.strip().lower() for cell in row]
            continue
        if len(row) > len(columns) and any(cell.strip() for cell in row[len(columns) :]):
            result.errors.append(RowIssue(line, f"has {len(row)} cells, more than the {len(columns)} columns"))
            continue
        cells = {name: (row[i].strip() if i < len(row) else "") for i, name in enumerate(columns)}
        if cells["roundturn_version"].lower() == "roundturn_version":  # a repeated header, from files pasted together
            continue
        if cells["roundturn_version"] != VERSION:
            return OtherRead(fatal=OTHER_VERSION.format(line=line, value=cells["roundturn_version"]))
        try:
            row_read = _round_turn(cells, line, now)
        except ValueError as problem:
            result.errors.append(RowIssue(line, str(problem)))
            continue
        turn, base = row_read.turn, row_read.turn.entry.ref
        occurrences[base] += 1
        n = occurrences[base]
        if n > 1:
            result.warnings.append(
                RowIssue(
                    line,
                    "the same round turn as an earlier row (same contract, side, contracts, times and prices); "
                    "both are kept",
                )
            )
        turn = replace(
            turn,
            entry=replace(turn.entry, ref=f"{base}#{n}:in"),
            exit=replace(turn.exit, ref=f"{base}#{n}:out"),
        )
        result.round_turns.append(turn)
        result.gross += turn.gross
        result.fees += turn.commission or finance.ZERO
        result.net += row_read.net
        result.offsets.update({row_read.entry_local.utcoffset(), row_read.exit_local.utcoffset()})  # type: ignore[arg-type]
        first, last = row_read.entry_local.date(), row_read.exit_local.date()
        result.first_day = first if result.first_day is None else min(result.first_day, first)
        result.last_day = last if result.last_day is None else max(result.last_day, last)
    if columns is not None and not result.round_turns and not result.errors:
        return OtherRead(fatal=NO_ROWS)
    result.round_turns.sort(key=lambda turn: (turn.entry.executed_at, turn.line))
    return result


def _check_header(header: list[str]) -> str | None:
    """Why this header row is not version 1's, or None when it is."""
    names = [name.lower() for name in header]
    present = set(names)
    if all(column in header for column in TRADOVATE_COLUMNS):
        return "This is a Tradovate Performance export. Choose Tradovate on the Import page instead."
    if all(column in header for column in tradesea.COLUMNS):
        return "This is a TradeSea Orders export. Choose TradeSea on the Import page instead."
    if all(column in header for column in round_turn_monitor.COLUMNS):
        return "This is the Round Turn Monitor's log. Choose NinjaTrader: Round Turn Monitor log on the Import page instead."
    if "roundturn_version" not in present:
        return NOT_OURS
    doubled = sorted({name for name in names if name and names.count(name) > 1})
    if doubled:
        return f"The column {doubled[0]} is in the header twice. {START_AGAIN}"
    missing = [column for column in COLUMNS if column not in present]
    extra = [name for name in names if name and name not in COLUMNS]
    if missing or extra:
        problems = []
        if missing:
            problems.append(f"it has no {', '.join(missing)}")
        if extra:
            problems.append(f"it has {', '.join(extra)}, which the format does not")
        return f"The header row is not Roundturn's import file version 1: {' and '.join(problems)}. {START_AGAIN}"
    return None


def _round_turn(cells: dict[str, str], line: int, now: datetime) -> _Row:
    symbol = cells["symbol"].upper()
    if not symbol:
        raise ValueError("symbol is empty")
    if not symbol.isalnum():
        raise ValueError(f"symbol {cells['symbol']!r} is not a contract such as NQZ6 or NQ (no spaces or prefixes)")
    side = cells["side"].lower()
    if side not in ("long", "short"):
        raise ValueError(f"side must be long or short, got {cells['side']!r}")
    qty_text = cells["qty"]
    if not qty_text.isdigit() or int(qty_text) <= 0:
        raise ValueError(f"qty: not a positive whole number: {qty_text!r}")
    qty = int(qty_text)

    entry_local = _time(cells, "entry_time")
    exit_local = _time(cells, "exit_time")
    entry_at, exit_at = entry_local.astimezone(UTC), exit_local.astimezone(UTC)
    if exit_at < entry_at:
        raise ValueError(f"exit_time {cells['exit_time']} is before entry_time {cells['entry_time']}")
    if entry_at < EARLIEST:
        raise ValueError(f"entry_time {cells['entry_time']} is before 2000")
    if exit_at > now + FUTURE_SLACK:
        raise ValueError(f"exit_time {cells['exit_time']} is in the future")

    contract = contract_of(symbol, entry_at)
    if contract is None:
        raise ValueError(
            f"the contract {symbol} is not in Roundturn's contract list, so its P&L cannot be checked "
            "(write it as the exchange does, for example ES or NQZ6)"
        )
    entry_price = _price(cells, "entry_price", qty, contract.tick_size)
    exit_price = _price(cells, "exit_price", qty, contract.tick_size)
    gross = _money(cells, "gross_pnl")
    fees = _money(cells, "fees")
    net = _money(cells, "net_pnl")
    if fees < 0:
        raise ValueError(f"fees must be zero or more, got {cells['fees']!r}")

    sign = 1 if side == "long" else -1
    worked_out = finance.to_money((exit_price - entry_price) * sign * qty * contract.point_value)
    if abs(worked_out - gross) > MONEY_SLACK:
        raise ValueError(
            f"gross_pnl {finance.money_str(gross)} is not what the prices make: {qty} {contract.root} {side} from "
            f"{finance.plain(entry_price)} to {finance.plain(exit_price)} is {finance.money_str(worked_out)} "
            f"(${finance.plain(contract.point_value)} a point). Check the contract, side, qty and prices"
        )
    if abs(gross - fees - net) > MONEY_SLACK:
        raise ValueError(
            f"net_pnl {finance.money_str(net)} is not gross_pnl {finance.money_str(gross)} minus fees "
            f"{finance.money_str(fees)} ({finance.money_str(gross - fees)})"
        )

    ref = _reference(symbol, side, qty, entry_at, exit_at, entry_price, exit_price)
    entry_side, exit_side = ("buy", "sell") if side == "long" else ("sell", "buy")
    # The fees are for the whole round turn: half on each order, so the comparison with the fee setting counts both
    # sides, as for the Round Turn Monitor. An odd cent goes on the exit.
    entry_fees = (fees / 2).quantize(_CENT, rounding=ROUND_DOWN)
    entry = Execution(
        executed_at=entry_at,
        symbol=symbol,
        side=entry_side,  # type: ignore[arg-type]  # buy or sell
        qty=qty,
        price=entry_price,
        ref=ref,
        commission=entry_fees,
        line=line,
    )
    exit_ = replace(entry, executed_at=exit_at, side=exit_side, price=exit_price, commission=fees - entry_fees)
    return _Row(RoundTurn(account="", entry=entry, exit=exit_, gross=worked_out), net, entry_local, exit_local)


def _time(cells: dict[str, str], column: str) -> datetime:
    text = cells[column]
    if not text:
        raise ValueError(f"{column} is empty")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{column} {text!r} is not a time like 2026-11-18T09:31:05-05:00") from None
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError(
            f"{column} {text!r} has no UTC offset; write it like 2026-11-18T09:31:05-05:00, so the time zone is certain"
        )
    return when


def _money(cells: dict[str, str], column: str) -> Decimal:
    text = cells[column]
    if not text:
        raise ValueError(f"{column} is empty")
    try:
        return finance.parse_money(text)
    except ValueError as problem:
        raise ValueError(f"{column}: {problem}") from None


def _price(cells: dict[str, str], column: str, qty: int, tick: Decimal) -> Decimal:
    text = cells[column]
    if not text:
        raise ValueError(f"{column} is empty")
    try:
        shown = finance.parse_decimal(text)
    except ValueError as problem:
        raise ValueError(f"{column}: {problem}") from None
    if shown <= 0:
        raise ValueError(f"{column} must be positive, got {text!r}")
    if qty == 1 and shown % tick != 0:
        raise ValueError(f"{column} {text} is not a whole number of ticks ({finance.plain(tick)}) for one contract")
    price, _ = tradesea.exact_average(shown, qty, tick)
    return price


def _reference(
    symbol: str, side: str, qty: int, entry_at: datetime, exit_at: datetime, entry_price: Decimal, exit_price: Decimal
) -> str:
    """What the round turn is, so the same round turn converted again (another chat, other formatting, another offset
    for the same instant) gets the same reference. The caller appends which identical round turn of the file it is and
    `:in` or `:out` for its two orders."""
    parts = [
        symbol,
        side,
        str(qty),
        entry_at.isoformat(timespec="microseconds"),
        exit_at.isoformat(timespec="microseconds"),
        finance.plain(entry_price),
        finance.plain(exit_price),
    ]
    return "rt:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def read_files(files: Sequence[tuple[str, bytes | str]], *, now: datetime | None = None) -> OtherRead:
    """One file per import: Roundturn's import file holds the whole period."""
    if len(files) != 1:
        return OtherRead(fatal="Choose one file at a time: the one the AI chatbot gave you.")
    return read_file(files[0][1], now=now)
