"""Read TradeSea's Orders export into executions.

What the export is, from seven real files checked to the cent against TradeSea's own totals for two trading days
(`docs/design/tradesea-import-plan.md`, section 1):

* One row per order, with its status. Only `Filled` rows are fills; the others are counted by status and skipped.
* `Time` is when the order filled (or was cancelled): `11/16/2026, 9:37:10 AM PST`, month first, a 12-hour clock and a
  time zone abbreviation. The file's own zone is used, not the person's CSV time zone setting.
* Rows are sorted by when each order was placed, not when it filled, so they are put in `Time` order here.
* `Avg Price` is rounded to 2 decimals. Every fill is a whole number of ticks, so the exact average is recovered from the
  quantity and the tick size whenever only one answer fits (TradeSea's own P&L uses the exact one).
* `Commission` is the order's total.
* `Symbol` is a data-feed prefix and the contract root with no month (`CME-Delayed:NQ`); it is stored as the root, `NQ`.
* There are no order or fill IDs and no account column, so each order's reference is built from what it is.
* The Orders list shows 10 rows a page and the export saves one page, so a day takes one file per page. Several files
  are read together as one import; an order on two pages counts once.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .. import finance
from ..models import Execution
from ..pairing import contract_of
from ..parser import MAX_ROWS, RowIssue, _decode
from ..parser import REQUIRED_COLUMNS as TRADOVATE_COLUMNS
from ..timeutil import zone

SOURCE = "tradesea_orders"
COLUMNS = (
    "Time",
    "Symbol",
    "Qty",
    "Side",
    "Order Type",
    "Limit Price",
    "Stop Price",
    "Avg Price",
    "Commission",
    "Status",
)
PAGE_ROWS = 10  # TradeSea's Orders list shows 10 rows a page, and its export saves only the page on screen
DISPLAY_STEP = Decimal("0.01")  # TradeSea writes average prices to at most 2 decimals
PRICE_PLACES = Decimal("1E-10")  # a recovered average is kept to 10 decimals (27420.8333333333)

ONE_PAGE = (
    "Every file here has exactly 10 orders, which is one page of TradeSea's Orders list. If you had more than 10 filled "
    "orders, export the other pages too and import them all together."
)

# Abbreviations a US computer writes, as fixed offsets: the abbreviation itself says whether daylight saving time
# applied. Others, such as CET, or IST (which names two different zones), are not guessed.
_ZONE_HOURS = {
    "UTC": 0, "GMT": 0, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6,
    "PST": -8, "PDT": -7, "AKST": -9, "AKDT": -8, "HST": -10,
}  # fmt: skip
_OFFSET_ZONE = re.compile(r"(?:GMT|UTC)([+-])(\d{1,2})(?::?(\d{2}))?")
_TIME = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4}),?\s+(\d{1,2}):(\d{2}):(\d{2})(?:\s*([AaPp][Mm]))?(?:\s+(\S+))?")
_STATUS_WORD = re.compile(r"[A-Za-z]+")


@dataclass
class ReadResult:
    executions: list[Execution] = field(default_factory=list)  # in time order, fill_id 1, 2, 3...
    errors: list[RowIssue] = field(default_factory=list)  # rows skipped because something was wrong
    warnings: list[RowIssue] = field(default_factory=list)  # rows kept, but something looked off; line 0: whole file
    skipped: Counter[str] = field(default_factory=Counter)  # orders that did not fill, by status ("cancelled": 4)
    rows: int = 0  # data rows read, whatever their status
    duplicates_across_files: int = 0  # filled orders on more than one page or file, counted once
    commission: Decimal = finance.ZERO  # the files' own commission for the filled orders kept
    zones: set[str] = field(default_factory=set)  # the files' own zone abbreviations that were read ("PST")
    fell_back: bool = False  # some times had no zone Roundturn reads, so the CSV time zone setting was used
    fatal: str | None = None  # the whole import was refused


def read_orders(data: bytes | str, fallback_tz: str) -> ReadResult:
    """Read one TradeSea Orders export. `fallback_tz` (the person's CSV time zone setting) is used only for a time
    whose zone abbreviation is missing or not understood, with a warning."""
    return read_order_files([("export.csv", data)], fallback_tz)


def read_order_files(files: Sequence[tuple[str, bytes | str]], fallback_tz: str) -> ReadResult:
    """Read several exports, usually the pages of one day, as one import."""
    combined = ReadResult()
    labelled = len(files) > 1
    collected: list[tuple[datetime, int, int, Execution]] = []
    seen: set[str] = set()
    page_sized: list[bool] = []
    for index, (name, data) in enumerate(files):
        label = f"{name}: " if labelled else ""
        one = _read_file(data, fallback_tz)
        if one.fatal:
            return ReadResult(fatal=label + one.fatal)
        combined.rows += one.rows
        combined.skipped.update(one.skipped)
        combined.zones |= one.zones
        combined.fell_back |= one.fell_back
        combined.errors.extend(RowIssue(issue.line, label + issue.message) for issue in one.errors)
        combined.warnings.extend(RowIssue(issue.line, label + issue.message) for issue in one.warnings)
        page_sized.append(one.rows == PAGE_ROWS)
        for execution in one.executions:
            if execution.ref in seen:
                combined.duplicates_across_files += 1
                continue
            seen.add(execution.ref)
            # Within one second, a row lower in the file happened earlier.
            collected.append((execution.executed_at, index, -execution.line, execution))
    if page_sized and all(page_sized):
        combined.warnings.insert(0, RowIssue(0, ONE_PAGE))
    collected.sort(key=lambda item: item[:3])
    combined.executions = [replace(e, fill_id=n) for n, (*_, e) in enumerate(collected, start=1)]
    combined.commission = sum((e.commission or finance.ZERO for e in combined.executions), finance.ZERO)
    combined.warnings.extend(_same_second_warnings(combined.executions))
    return combined


def parse_time(text: str, fallback_tz: str) -> tuple[datetime, str | None]:
    """A TradeSea time in UTC, and the zone abbreviation that was not understood ("" when there was none), or None when
    the file's own zone was used. An unknown or missing zone is read as `fallback_tz`."""
    match = _TIME.fullmatch(text.strip())
    if not match:
        raise ValueError(f"not a time: {text!r} (expected one like 11/16/2026, 9:37:10 AM PST)")
    month, day, year, hour, minute, second = (int(match[i]) for i in range(1, 7))
    meridiem, abbreviation = match[7], match[8]
    if meridiem:
        if not 1 <= hour <= 12:
            raise ValueError(f"not a time: {text!r} (hour {hour} with AM or PM)")
        hour = hour % 12 + (12 if meridiem.upper() == "PM" else 0)
    try:
        naive = datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise ValueError(f"not a date and time: {text!r} (TradeSea writes month/day/year)") from None
    offset = _zone_offset(abbreviation)
    if offset is not None:
        return naive.replace(tzinfo=timezone(offset)).astimezone(UTC), None
    return naive.replace(tzinfo=zone(fallback_tz)).astimezone(UTC), abbreviation or ""


def clean_symbol(text: str) -> str:
    """`CME-Delayed:NQ` -> `NQ`: the part after the data-feed prefix, upper case, no spaces."""
    symbol = text.rsplit(":", 1)[-1].replace(" ", "").upper()
    if not symbol:
        raise ValueError("Symbol is empty")
    return symbol


def display_step(shown: Decimal) -> Decimal:
    """How finely TradeSea rounded this price: 0.01, or finer when it wrote more decimals (it drops trailing zeros, so
    `27040.8` is still a 2-decimal price)."""
    decimals = max(0, -int(shown.as_tuple().exponent))
    return DISPLAY_STEP if decimals <= 2 else Decimal(1).scaleb(-decimals)


def exact_average(shown: Decimal, qty: int, tick: Decimal) -> tuple[Decimal, bool]:
    """The real average fill price behind TradeSea's rounded one, and whether it could be told for certain.

    One contract fills at one price, so its price is exact. For more, every fill is a whole number of ticks, so the
    real average is a whole number of ticks divided by the quantity: candidates `tick / qty` apart. When they are further
    apart than TradeSea's rounding (0.01), at most one lies within half of it from the shown price, and that one is the
    real average (up to 24 contracts for NQ's quarter-point tick). When they are closer together, as for 2 or more
    crude oil contracts (a 0.01 tick), the rounding cannot be undone: the shown price is kept and the caller warns.
    """
    if qty == 1:
        return shown, True
    step = display_step(shown)
    if tick / qty <= step:
        return shown, False
    ticks = shown * qty / tick
    if ticks == ticks.to_integral_value():
        return shown, True
    nearest = ticks.to_integral_value()
    candidates = [k * tick / qty for k in (nearest - 1, nearest, nearest + 1)]
    fitting = [c for c in candidates if abs(c - shown) <= step / 2]
    if len(fitting) == 1:
        return fitting[0].quantize(PRICE_PLACES), True
    return shown, False


def _zone_offset(abbreviation: str | None) -> timedelta | None:
    if not abbreviation:
        return None
    key = abbreviation.upper()
    if key in _ZONE_HOURS:
        return timedelta(hours=_ZONE_HOURS[key])
    match = _OFFSET_ZONE.fullmatch(key)
    if match is None:
        return None
    hours, minutes = int(match[2]), int(match[3] or 0)
    if hours > 14 or minutes >= 60:
        return None
    sign = -1 if match[1] == "-" else 1
    return sign * timedelta(hours=hours, minutes=minutes)


def _read_file(data: bytes | str, fallback_tz: str) -> ReadResult:
    text = _decode(data)
    lines = text.splitlines()
    first = next((line for line in lines if line.strip()), "")
    if not first:
        return ReadResult(fatal="The file is empty.")
    header = [column.strip() for column in next(csv.reader([first]))]
    missing = [column for column in COLUMNS if column not in header]
    if missing:
        if all(column in header for column in TRADOVATE_COLUMNS):
            return ReadResult(fatal="This is a Tradovate Performance export, not a TradeSea one.")
        return ReadResult(fatal=f"Not a TradeSea Orders export: missing column(s) {', '.join(missing)}.")
    if len(lines) - 1 > MAX_ROWS:
        return ReadResult(
            fatal=f"That file has {len(lines) - 1:,} rows, and one import takes at most {MAX_ROWS:,}. "
            "Export a shorter period and import the parts one at a time."
        )

    result = ReadResult()
    occurrences: Counter[str] = Counter()
    unknown_zones: set[str] = set()
    rates: list[tuple[Decimal, Execution]] = []  # commission per contract, for the consistency check
    reader = csv.reader(io.StringIO(text))
    columns: list[str] | None = None
    for row in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in row):
            continue
        if columns is None:
            columns = [cell.strip() for cell in row]
            continue
        cells = {name: (row[i].strip() if i < len(row) else "") for i, name in enumerate(columns)}
        if cells["Time"] == "Time":  # a repeated header, from files pasted together
            continue
        result.rows += 1

        status = _status_word(cells["Status"])
        if status != "filled":
            if cells["Avg Price"]:
                result.errors.append(
                    RowIssue(
                        line,
                        f"status {cells['Status']!r} but it has a fill price ({cells['Avg Price']}): a partly filled "
                        "order, which Roundturn cannot read yet",
                    )
                )
            else:
                result.skipped[status or "no status"] += 1
            continue

        try:
            execution, zone_problem, worst_error = _filled_order(cells, line, fallback_tz)
        except ValueError as problem:
            result.errors.append(RowIssue(line, str(problem)))
            continue
        if zone_problem is not None:
            unknown_zones.add(zone_problem)
            result.fell_back = True
        else:
            result.zones.add(cells["Time"].split()[-1].upper())
        if worst_error is not None:
            result.warnings.append(
                RowIssue(
                    line,
                    f"Avg Price {cells['Avg Price']} may be rounded, and the exact average of {execution.qty} contracts "
                    f"cannot be told from it; this order's P&L may be off by up to ${worst_error}",
                )
            )
        occurrences[execution.ref] += 1
        if occurrences[execution.ref] == 2:
            result.warnings.append(
                RowIssue(
                    line,
                    "two identical orders in the same second; both are kept, but if one of them is also on another "
                    "page, it is counted once",
                )
            )
        execution = replace(execution, ref=f"{execution.ref}#{occurrences[execution.ref]}")
        result.executions.append(execution)
        if execution.commission is not None:
            rates.append((execution.commission / execution.qty, execution))

    for abbreviation in sorted(unknown_zones):
        if abbreviation:
            message = (
                f"the time zone {abbreviation!r} is not one Roundturn reads, so those times were read as {fallback_tz}"
            )
        else:
            message = f"some times have no time zone, so they were read as {fallback_tz}"
        result.warnings.append(RowIssue(0, message + " (your CSV time zone setting)"))
    result.warnings.extend(_commission_warnings(rates))
    return result


def _filled_order(cells: dict[str, str], line: int, fallback_tz: str) -> tuple[Execution, str | None, Decimal | None]:
    """The order as an execution, the zone abbreviation that was not understood (see `parse_time`), and, when the
    average price may be rounded beyond recovery, the most its P&L can be off by."""
    try:
        executed_at, zone_problem = parse_time(cells["Time"], fallback_tz)
    except ValueError as problem:
        raise ValueError(f"Time: {problem}") from None
    symbol = clean_symbol(cells["Symbol"])
    contract = contract_of(symbol, executed_at)
    if contract is None:
        raise ValueError(
            f"the contract {symbol} is not in Roundturn's contract list yet, so its P&L cannot be worked out"
        )
    side = cells["Side"].lower()
    if side not in ("buy", "sell"):
        raise ValueError(f"Side must be Buy or Sell, got {cells['Side']!r}")
    qty_text = cells["Qty"]
    if not qty_text.isdigit() or int(qty_text) <= 0:
        raise ValueError(f"Qty: not a positive whole number: {qty_text!r}")
    qty = int(qty_text)
    if not cells["Avg Price"]:
        raise ValueError("Avg Price is empty")
    shown = _amount("Avg Price", cells["Avg Price"])
    if shown <= 0:
        raise ValueError(f"Avg Price must be positive, got {cells['Avg Price']!r}")
    price, exact = exact_average(shown, qty, contract.tick_size)
    worst_error = None if exact else finance.to_money(display_step(shown) / 2 * qty * contract.point_value)
    commission = _amount("Commission", cells["Commission"]) if cells["Commission"] else None
    execution = Execution(
        executed_at=executed_at,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]  # checked above
        qty=qty,
        price=price,
        ref=_reference(executed_at, cells),
        commission=commission,
        order_type=cells["Order Type"][:40],
        line=line,
    )
    return execution, zone_problem, worst_error


def _amount(column: str, text: str) -> Decimal:
    try:
        return finance.parse_money(text)
    except ValueError as problem:
        raise ValueError(f"{column}: {problem}") from None


def _status_word(text: str) -> str:
    """`Cancelled (by trader)` -> `cancelled`; `Canceled` is spelled the same way."""
    match = _STATUS_WORD.match(text.strip())
    word = match[0].lower() if match else ""
    return "cancelled" if word == "canceled" else word


def _reference(executed_at: datetime, cells: dict[str, str]) -> str:
    """What the order is, so the same order in another export (another page, or the same day exported again) gets the
    same reference. The caller appends which identical order of the file it is (#1, #2)."""
    parts = [
        executed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        cells["Symbol"],
        cells["Side"].lower(),
        cells["Qty"],
        cells["Order Type"].lower(),
        *(_plain(cells[column]) for column in ("Limit Price", "Stop Price", "Avg Price", "Commission")),
    ]
    return "ts:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _plain(text: str) -> str:
    try:
        return finance.plain(finance.parse_money(text)) if text else ""
    except (ValueError, InvalidOperation):
        return text


def _commission_warnings(rates: list[tuple[Decimal, Execution]]) -> list[RowIssue]:
    """Orders whose commission is not the per-contract rate most orders show."""
    counts = Counter(rate for rate, _ in rates)
    if len(counts) < 2:
        return []
    usual = counts.most_common(1)[0][0]
    return [
        RowIssue(
            execution.line,
            f"commission {finance.plain(execution.commission or finance.ZERO)} for {execution.qty} contract(s) is not "
            f"the {finance.plain(usual)} per contract the other orders show",
        )
        for rate, execution in rates
        if rate != usual
    ]


def _same_second_warnings(executions: list[Execution]) -> list[RowIssue]:
    """A buy and a sell of one contract in the same second: the file cannot say which came first."""
    warnings = []
    for before, after in zip(executions, executions[1:]):
        if before.symbol == after.symbol and before.executed_at == after.executed_at and before.side != after.side:
            warnings.append(
                RowIssue(
                    after.line,
                    f"a buy and a sell of {after.symbol} in the same second ({after.executed_at:%H:%M:%S} UTC); "
                    "the file cannot say which came first, which can change how the trades are split",
                )
            )
    return warnings
