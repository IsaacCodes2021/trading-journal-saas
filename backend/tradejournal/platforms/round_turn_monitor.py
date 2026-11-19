"""Read the Round Turn Monitor's log (an add-on for NinjaTrader 8) into orders.

What the log is, from the add-on's source (`RoundTurnMonitor.Csv.cs`) and a real log checked against its own day total
(`docs/design/round-turn-monitor-import-plan.md`, section 1):

* `RoundTurnLog.csv` in `Documents\\NinjaTrader 8\\RoundTurnLog`, 22 columns, one row per **complete round turn**: one
  account and one contract, from flat to flat. While the main file is open in Excel, rows go to
  `RoundTurnLog_overflow.csv` instead; choosing both reads them together.
* It holds every tracked account, practice ones (Sim101) too. One import takes one account's rows (`for_account`); which
  one is the caller's choice.
* Times are NinjaTrader's clock, which the add-on writes without converting, so they are read in the person's CSV time
  zone setting. `date_local` is the entry's date. The exit is the entry plus `duration_seconds`, as the add-on itself
  reads its rows, so a round turn over midnight is right; `exit_time_local` gives its time of day to the millisecond.
* Prices are averages written to at most 6 decimals. Every fill is a whole number of ticks, so the exact average is
  recovered from the contracts and the tick size, as for TradeSea.
* `gross_result_usd` is checked against prices x contracts x point value. `commission_usd` is kept for the comparison
  with the account's fee setting; a trade's fees come from that setting, as for every import.
* `instrument` (`NQ 12-26`) is stored the way Tradovate writes a contract, `NQZ6`.

Each round turn becomes two orders, its entry and its exit, stored and paired like TradeSea's (`pg/executions.py`).
Each order's reference is built from the round turn's contents, so choosing the same growing log again adds only the
new round turns. The Export button's file (no contract, no commission, today only) and the add-on's old 19-column log
(`RoundTurnLog_v1.csv`) are refused in words.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from .. import finance
from ..models import Execution
from ..pairing import contract_of
from ..parser import MAX_ROWS, RowIssue, _decode
from ..parser import REQUIRED_COLUMNS as TRADOVATE_COLUMNS
from ..symbols import MONTH_CODES, parse_symbol
from ..timeutil import zone
from . import tradesea

SOURCE = "round_turn_monitor_log"
COLUMNS = (
    "date_local",
    "account",
    "instrument",
    "direction",
    "contracts_traded",
    "max_contracts_held",
    "entry_time_local",
    "exit_time_local",
    "duration_seconds",
    "avg_entry_price",
    "avg_exit_price",
    "max_adverse_pts_per_contract",
    "max_favorable_pts_per_contract",
    "result_pts_per_contract",
    "max_adverse_usd_at_max_contracts",
    "max_favorable_usd_at_max_contracts",
    "gross_result_usd",
    "commission_usd",
    "net_result_usd",
    "gap_to_max_pts",
    "day_pnl_usd_net",
    "traded_during_break",
)
OLD_COLUMNS = COLUMNS[:19]  # the log before the add-on's decision D58 added the last three
EXPORT_MARKERS = ("entry_time_pt", "exit_time_pt", "pnl_usd_net")  # the Export button's file (D41, D61)

LOG_FOLDER = "Documents\\NinjaTrader 8\\RoundTurnLog"
EXPORT_FILE = (
    "This is the Round Turn Monitor's Export file, which has no contract or commission. "
    f"Choose RoundTurnLog.csv instead (in {LOG_FOLDER})."
)
OLD_LOG = (
    "This is the Round Turn Monitor's old log, from before the add-on was updated (19 columns, now named "
    f"RoundTurnLog_v1.csv). Choose RoundTurnLog.csv instead (in {LOG_FOLDER})."
)

MONEY_SLACK = Decimal("0.01")  # the add-on rounds dollars from binary fractions; a cent either way is not a difference
_INSTRUMENT = re.compile(r"([A-Z0-9]+)\s+(\d{1,2})-(\d{2})")  # "NQ 12-26": root, month, year
_MONTH_LETTERS = {number: letter for letter, number in MONTH_CODES.items()}
_PRACTICE = re.compile(r"(sim|playback|backtest)", re.IGNORECASE)  # NinjaTrader's own practice accounts: Sim101...
_CENT = Decimal("0.01")


def is_practice(account: str) -> bool:
    """NinjaTrader's practice accounts: Sim101 (and any other Sim account), Playback101, Backtest."""
    return _PRACTICE.match(account.strip()) is not None


@dataclass(frozen=True)
class RoundTurn:
    """One row of the log: its entry and exit as orders, and what the log says it made."""

    account: str
    entry: Execution
    exit: Execution
    gross: Decimal  # gross_result_usd, before commission

    @property
    def line(self) -> int:
        return self.entry.line

    @property
    def commission(self) -> Decimal | None:
        """The log's commission_usd for the round turn: its two orders' halves together."""
        if self.entry.commission is None or self.exit.commission is None:
            return None
        return self.entry.commission + self.exit.commission


@dataclass
class LogRead:
    """Every account's round turns in the files, in entry order."""

    round_turns: list[RoundTurn] = field(default_factory=list)
    errors: list[tuple[str, RowIssue]] = field(default_factory=list)  # (the row's account, or "" when unknown; issue)
    warnings: list[tuple[str, RowIssue]] = field(default_factory=list)  # the same; line 0: the whole file
    duplicates: Counter[str] = field(default_factory=Counter)  # round turns in more than one file, by account
    fatal: str | None = None  # the whole import was refused

    def accounts(self) -> dict[str, int]:
        """How many round turns each account has, in the order the accounts first appear in the files. An account whose
        every row was refused is listed with 0, so its rows' problems can still be shown."""
        counts: dict[str, int] = {}
        firsts = [(turn.entry.line, turn.account) for turn in self.round_turns]
        firsts += [(issue.line, owner) for owner, issue in self.errors if owner]
        for _, account in sorted(firsts, key=lambda item: item[0]):
            counts.setdefault(account, 0)
        for turn in self.round_turns:
            counts[turn.account] += 1
        return counts

    def for_account(self, account: str) -> tradesea.ReadResult:
        """One account's round turns as orders, ready to store: entry and exit in time order, `fill_id` 1, 2, 3...

        At the same moment, an exit comes before the next round turn's entry (a reversal, or a new entry in the same
        millisecond), and a round turn's own entry comes before its exit (a fill and its close in one millisecond)."""
        turns = [turn for turn in self.round_turns if turn.account == account]
        keyed: list[tuple[datetime, int, int, Execution]] = []
        for index, turn in enumerate(turns):
            keyed.append((turn.entry.executed_at, index, 0, turn.entry))
            keyed.append((turn.exit.executed_at, index, 1, turn.exit))
        keyed.sort(key=lambda item: item[:3])
        executions = [replace(e, fill_id=n) for n, (*_, e) in enumerate(keyed, start=1)]
        rates: dict[str, Counter[Decimal]] = {}
        for turn in turns:
            if turn.commission:  # a round turn the add-on charged: $0 is no rate (none set in its Settings yet)
                root, _ = parse_symbol(turn.entry.symbol, turn.entry.executed_at.date())
                rates.setdefault(root, Counter())[turn.commission / turn.entry.qty] += 1
        return tradesea.ReadResult(
            executions=executions,
            errors=[issue for owner, issue in self.errors if owner in ("", account)],
            warnings=[issue for owner, issue in self.warnings if owner in ("", account)],
            rows=len(turns),
            duplicates_across_files=self.duplicates[account],
            commission=sum((e.commission or finance.ZERO for e in executions), finance.ZERO),
            rates={root: counts.most_common(1)[0][0] for root, counts in rates.items()},
        )


def read_log(data: bytes | str, tz_name: str) -> LogRead:
    """Read one log. Times are read as `tz_name` (the person's CSV time zone setting)."""
    return read_log_files([("RoundTurnLog.csv", data)], tz_name)


def read_log_files(files: Sequence[tuple[str, bytes | str]], tz_name: str) -> LogRead:
    """Read several files as one log: `RoundTurnLog.csv` and `RoundTurnLog_overflow.csv`, or the same log chosen twice.
    A round turn in more than one file counts once."""
    combined = LogRead()
    labelled = len(files) > 1
    seen: set[str] = set()
    collected: list[tuple[datetime, int, int, RoundTurn]] = []
    for index, (name, data) in enumerate(files):
        label = f"{name}: " if labelled else ""
        one = _read_file(data, tz_name)
        if one.fatal:
            return LogRead(fatal=label + one.fatal)
        combined.errors.extend((owner, RowIssue(i.line, label + i.message)) for owner, i in one.errors)
        combined.warnings.extend((owner, RowIssue(i.line, label + i.message)) for owner, i in one.warnings)
        for turn in one.round_turns:
            if turn.entry.ref in seen:
                combined.duplicates[turn.account] += 1
                continue
            seen.add(turn.entry.ref)
            collected.append((turn.entry.executed_at, index, turn.line, turn))
    collected.sort(key=lambda item: item[:3])
    combined.round_turns = [turn for *_, turn in collected]
    return combined


def symbol_of(instrument: str) -> str:
    """NinjaTrader's `NQ 12-26` as Tradovate writes the same contract, `NQZ6`."""
    match = _INSTRUMENT.fullmatch(instrument.strip().upper())
    if match is None:
        raise ValueError(
            f"instrument {instrument!r} is not a futures contract Roundturn reads (expected one like 'NQ 12-26')"
        )
    root, month, year = match[1], int(match[2]), match[3]
    if month not in _MONTH_LETTERS:
        raise ValueError(f"instrument {instrument!r} has no month {month}")
    return f"{root}{_MONTH_LETTERS[month]}{year[-1]}"


def _read_file(data: bytes | str, tz_name: str) -> LogRead:
    text = _decode(data)
    lines = text.splitlines()
    first = next((line for line in lines if line.strip()), "")
    if not first:
        return LogRead(fatal="The file is empty.")
    header = [column.strip() for column in next(csv.reader([first]))]
    missing = [column for column in COLUMNS if column not in header]
    if missing:
        return LogRead(fatal=_wrong_file(header, missing))
    if len(lines) - 1 > MAX_ROWS:
        return LogRead(
            fatal=f"That file has {len(lines) - 1:,} rows, and one import takes at most {MAX_ROWS:,}. "
            "Ask for help at hello@roundturn.app to import a log this long."
        )

    result = LogRead()
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
        if cells["date_local"] == "date_local":  # a repeated header, from files pasted together
            continue
        account = cells["account"]
        try:
            turn, warnings = _round_turn(cells, line, tz_name)
        except ValueError as problem:
            result.errors.append((account, RowIssue(line, str(problem))))
            continue
        result.round_turns.append(turn)
        result.warnings.extend((account, RowIssue(line, warning)) for warning in warnings)
    return result


def _wrong_file(header: list[str], missing: list[str]) -> str:
    if all(marker in header for marker in EXPORT_MARKERS):
        return EXPORT_FILE
    if all(column in header for column in OLD_COLUMNS):
        return OLD_LOG
    if all(column in header for column in TRADOVATE_COLUMNS):
        return "This is a Tradovate Performance export, not the Round Turn Monitor's log."
    if all(column in header for column in tradesea.COLUMNS):
        return "This is a TradeSea Orders export, not the Round Turn Monitor's log."
    return f"Not the Round Turn Monitor's log: missing column(s) {', '.join(missing)}."


def _round_turn(cells: dict[str, str], line: int, tz_name: str) -> tuple[RoundTurn, list[str]]:
    account = cells["account"]
    if not account:
        raise ValueError("account is empty")
    symbol = symbol_of(cells["instrument"])
    direction = cells["direction"].lower()
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be Long or Short, got {cells['direction']!r}")
    qty_text = cells["contracts_traded"]
    if not qty_text.isdigit() or int(qty_text) <= 0:
        raise ValueError(f"contracts_traded: not a positive whole number: {qty_text!r}")
    qty = int(qty_text)

    day = _parse(cells, "date_local", lambda text: date.fromisoformat(text))
    entry_clock = _parse(cells, "entry_time_local", time.fromisoformat)
    duration = _number(cells, "duration_seconds")
    if duration < 0:
        raise ValueError(f"duration_seconds is negative: {cells['duration_seconds']!r}")
    entry_local = datetime.combine(day, entry_clock)
    exit_local, exit_note = _exit_time(entry_local, duration, cells["exit_time_local"])
    local = zone(tz_name)
    entry_at = entry_local.replace(tzinfo=local).astimezone(UTC)
    exit_at = max(exit_local.replace(tzinfo=local).astimezone(UTC), entry_at)

    contract = contract_of(symbol, entry_at)
    if contract is None:
        raise ValueError(
            f"the contract {cells['instrument']} is not in Roundturn's contract list yet, so its P&L cannot be worked out"
        )
    entry_price = _price(cells, "avg_entry_price", qty, contract.tick_size)
    exit_price = _price(cells, "avg_exit_price", qty, contract.tick_size)
    gross = _money(cells, "gross_result_usd")
    commission = _money(cells, "commission_usd") if cells["commission_usd"] else None

    warnings = [exit_note] if exit_note else []
    sign = 1 if direction == "long" else -1
    worked_out = finance.to_money((exit_price - entry_price) * sign * qty * contract.point_value)
    if abs(worked_out - gross) > MONEY_SLACK:
        warnings.append(
            f"gross_result_usd {finance.plain(gross)} is not what the prices make "
            f"({finance.plain(worked_out)} for {qty} contract(s)); Roundturn uses {finance.plain(worked_out)}"
        )

    ref = _reference(cells)
    entry_side, exit_side = ("buy", "sell") if direction == "long" else ("sell", "buy")
    # The log's commission is for the whole round turn: half on each order, so the comparison with the fee setting
    # counts both sides. An odd cent goes on the exit.
    entry_commission = None if commission is None else (commission / 2).quantize(_CENT, rounding=ROUND_DOWN)
    exit_commission = None if commission is None else commission - (entry_commission or finance.ZERO)
    entry = Execution(
        executed_at=entry_at,
        symbol=symbol,
        side=entry_side,  # type: ignore[arg-type]  # buy or sell
        qty=qty,
        price=entry_price,
        ref=f"{ref}:in",
        commission=entry_commission,
        line=line,
    )
    exit_ = replace(
        entry, executed_at=exit_at, side=exit_side, price=exit_price, ref=f"{ref}:out", commission=exit_commission
    )
    return RoundTurn(account=account, entry=entry, exit=exit_, gross=gross), warnings


def _exit_time(entry: datetime, duration: Decimal, written: str) -> tuple[datetime, str | None]:
    """The exit in local time: the entry plus the duration, with the time of day the log wrote (it keeps the
    milliseconds the duration rounds), and a warning when the two disagree."""
    reckoned = entry + timedelta(microseconds=int(duration * 1_000_000))
    try:
        clock = time.fromisoformat(written)
    except ValueError:
        return reckoned, f"exit_time_local {written!r} is not a time; used the entry time plus duration_seconds"
    closest = min(
        (datetime.combine(reckoned.date() + timedelta(days=shift), clock) for shift in (-1, 0, 1)),
        key=lambda candidate: abs(candidate - reckoned),
    )
    if abs(closest - reckoned) > timedelta(seconds=1):
        return reckoned, (
            f"exit_time_local {written} is not the entry time plus duration_seconds "
            f"({reckoned:%H:%M:%S}); used the entry time plus duration_seconds"
        )
    return closest, None


def _parse(cells: dict[str, str], column: str, convert):
    text = cells[column]
    if not text:
        raise ValueError(f"{column} is empty")
    try:
        return convert(text)
    except ValueError:
        raise ValueError(f"{column}: not understood: {text!r}") from None


def _number(cells: dict[str, str], column: str) -> Decimal:
    text = cells[column]
    if not text:
        raise ValueError(f"{column} is empty")
    try:
        return finance.parse_decimal(text)
    except ValueError as problem:
        raise ValueError(f"{column}: {problem}") from None


def _money(cells: dict[str, str], column: str) -> Decimal:
    try:
        return finance.parse_money(cells[column])
    except ValueError as problem:
        raise ValueError(f"{column}: {problem}") from None


def _price(cells: dict[str, str], column: str, qty: int, tick: Decimal) -> Decimal:
    shown = _number(cells, column)
    if shown <= 0:
        raise ValueError(f"{column} must be positive, got {cells[column]!r}")
    price, _ = tradesea.exact_average(shown, qty, tick)
    return price


def _reference(cells: dict[str, str]) -> str:
    """What the round turn is, so the same row in a later copy of the log (or the overflow file) gets the same
    reference: the account, the contract, the entry to the millisecond, the side, the contracts, the prices and the
    duration. The caller appends `:in` or `:out` for its two orders."""
    parts = [
        cells["account"],
        cells["instrument"].upper(),
        cells["date_local"],
        cells["entry_time_local"],
        cells["direction"].lower(),
        cells["contracts_traded"],
        *(_plain(cells[column]) for column in ("avg_entry_price", "avg_exit_price", "duration_seconds")),
    ]
    return "rtm:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _plain(text: str) -> str:
    try:
        return finance.plain(Decimal(text))
    except InvalidOperation:
        return text
