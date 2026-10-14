"""Prop-firm rule sets: the 46 rule fields, reading them from the research CSV, and checking a rule set. Pure.

The fields and what each one means come from `docs/prop-rules/Futures prop firm account rules.md` (the research report).
The seed data is a CSV with one row per firm + program + account size + phase + rule version: the 46 rule columns in the
order of `FIELDS`, then `flagged_fields` and `flag_reasons` (pipe-separated and aligned). A flag reason starts with
`unknown`, `conflict:` or `third-party only`; the cells of the first two were blanked by the research, the third keeps its
value. An empty cell means "not applicable or not known".

**Nothing is ever guessed.** A cell is stored in its typed column only when it is a clean value of that type: "2000" in a
money column is 2000, but "up to 1500" leaves the column empty and keeps the text in `source_notes`, so the person who
checks the rules sees what the source said and decides. The engine (`compliance.py`) reads only the typed columns and the
descriptive text columns, never the notes.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .finance import to_money

Kind = Literal["text", "money", "int", "pct", "date", "choice"]

PHASES = ("evaluation", "funded", "live")
DRAWDOWN_TYPES = ("eod_trailing", "eod_trailing_realtime_enforced", "intraday_trailing", "static")
DAILY_LOSS_TYPES = ("none", "soft", "optional", "hard")
DAILY_LOSS_BASES = ("realized", "realized_plus_unrealized")
CONSISTENCY_BASES = (
    "none",
    "none stated",
    "best_day_vs_total_profit",
    "best_day_vs_cycle_profit",
    "best_day_vs_profit_target",
    "best_day_vs_profit_goal",
    "best_day_vs_positive_days_sum",
    "best_day_vs_gross_profit_incl_payouts",
    "all_time_best_day_vs_cycle_profit",
)
NO_CONSISTENCY = ("none", "none stated")
BILLINGS = ("one_time", "monthly")
CONFIDENCES = ("official", "conflicting", "third_party", "unknown")

# The groups of the rules form, in order. Every field belongs to one.
GROUPS = (
    "Rule set",
    "Cost",
    "Pass / target",
    "Drawdown",
    "Daily loss",
    "Contracts & scaling",
    "Consistency",
    "Qualifying days",
    "Payout",
    "Restrictions",
    "Inactivity",
    "Source",
)


@dataclass(frozen=True)
class RuleField:
    name: str
    kind: Kind
    group: str
    label: str
    choices: tuple[str, ...] = ()
    hint: str = ""


# The 46 rule columns, in the CSV's order (which is also the table's).
FIELDS: tuple[RuleField, ...] = (
    RuleField("firm", "text", "Rule set", "Firm"),
    RuleField("program", "text", "Rule set", "Program"),
    RuleField("account_size", "text", "Rule set", "Account size"),
    RuleField("phase", "choice", "Rule set", "Phase", PHASES),
    RuleField("rule_version", "text", "Rule set", "Rule version"),
    RuleField("effective_from", "date", "Rule set", "Effective from"),
    RuleField("platform_notes", "text", "Rule set", "Platform notes"),
    RuleField("price_usd", "money", "Cost", "Price"),
    RuleField("billing", "choice", "Cost", "Billing", BILLINGS),
    RuleField("activation_fee_usd", "money", "Cost", "Activation fee"),
    RuleField("reset_fee_usd", "money", "Cost", "Reset fee"),
    RuleField(
        "profit_target_usd",
        "money",
        "Pass / target",
        "Profit target",
        hint="On a funded account: the profit goal for each payout.",
    ),
    RuleField("max_loss_usd", "money", "Drawdown", "Max loss"),
    RuleField("drawdown_type", "choice", "Drawdown", "Drawdown type", DRAWDOWN_TYPES),
    RuleField(
        "drawdown_lock_level",
        "text",
        "Drawdown",
        "Stops trailing at",
        hint="start_balance, start_balance_plus_100, none, or a balance such as 50100.",
    ),
    RuleField(
        "drawdown_after_payout",
        "text",
        "Drawdown",
        "After a payout",
        hint="unchanged, locks_at_start, locks_at_start_plus_100, resets_to_zero, or becomes static.",
    ),
    RuleField("daily_loss_limit_usd", "money", "Daily loss", "Daily loss limit"),
    RuleField("daily_loss_type", "choice", "Daily loss", "Daily loss type", DAILY_LOSS_TYPES),
    RuleField(
        "daily_loss_basis",
        "choice",
        "Daily loss",
        "Counts",
        DAILY_LOSS_BASES,
        hint="Empty: not stated, so open losses may count.",
    ),
    RuleField("max_contracts_minis", "int", "Contracts & scaling", "Max contracts (minis)"),
    RuleField("max_contracts_micros", "int", "Contracts & scaling", "Max contracts (micros)"),
    RuleField("scaling_plan", "text", "Contracts & scaling", "Scaling plan"),
    RuleField("min_trading_days", "int", "Qualifying days", "Minimum trading days"),
    RuleField("consistency_pct", "pct", "Consistency", "Consistency %"),
    RuleField("consistency_basis", "choice", "Consistency", "Measured against", CONSISTENCY_BASES),
    RuleField("qualifying_day_min_profit_usd", "money", "Qualifying days", "A qualifying day makes at least"),
    RuleField("qualifying_days_required", "int", "Qualifying days", "Qualifying days required"),
    RuleField("payout_buffer_usd", "money", "Payout", "Buffer above start"),
    RuleField("min_balance_to_request_usd", "money", "Payout", "Minimum balance to request"),
    RuleField("min_payout_usd", "money", "Payout", "Minimum payout"),
    RuleField("max_payout_usd", "money", "Payout", "Maximum payout"),
    RuleField(
        "payout_cap_schedule",
        "text",
        "Payout",
        "Cap by payout number",
        hint="1:1500|2:2000|3+:3000, all:2000, or uncapped.",
    ),
    RuleField(
        "payout_pct_of_profit_cap",
        "text",
        "Payout",
        "Percentage cap",
        hint="50% of profit, 50% of balance or 200% of cycle profit.",
    ),
    RuleField(
        "payout_frequency",
        "text",
        "Payout",
        "Cadence",
        hint="daily, every 7 days, every 10 trading days, or every 5 qualifying days.",
    ),
    RuleField("payout_days_allowed", "text", "Payout", "Days a request is allowed", hint="any, or business days."),
    RuleField("max_payouts_before_live_or_close", "text", "Payout", "Payouts before live or close"),
    RuleField("profit_split", "text", "Payout", "Profit split", hint="Your share first: 90/10."),
    RuleField("news_trading", "text", "Restrictions", "News trading"),
    RuleField("overnight_holding", "text", "Restrictions", "Overnight holding"),
    RuleField("flat_by_time", "text", "Restrictions", "Flat by"),
    RuleField("other_restrictions", "text", "Restrictions", "Other restrictions"),
    RuleField(
        "inactivity_rule",
        "text",
        "Inactivity",
        "Inactivity rule",
        hint="For example: 2 days $50+ per rolling 30 days, or one trade every 7 calendar days.",
    ),
    RuleField("max_accounts", "text", "Restrictions", "Max accounts"),
    RuleField("data_confidence", "choice", "Source", "Data confidence", CONFIDENCES),
    RuleField("source_urls", "text", "Source", "Sources"),
    RuleField("notes", "text", "Source", "Notes"),
)
FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in FIELDS)
BY_NAME: dict[str, RuleField] = {f.name: f for f in FIELDS}
KEY_FIELDS = ("firm", "program", "account_size", "phase", "rule_version")
CSV_COLUMNS = (*FIELD_NAMES, "flagged_fields", "flag_reasons")
FLAG_KINDS = ("unknown", "conflict", "third-party only")

MAX_MONEY = Decimal("999999999999.99")
MAX_TEXT = 4000
MAX_INT = 1_000_000

_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_INTEGER = re.compile(r"^\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class RuleValues:
    """One rule set as data: a typed value (or None) for each field, the research's flags, and the original text of every
    cell that was not a clean value for its column."""

    values: dict[str, Any]
    flagged_fields: list[str] = field(default_factory=list)
    flag_reasons: list[str] = field(default_factory=list)
    source_notes: dict[str, str] = field(default_factory=dict)

    def key(self) -> tuple[str, str, str, str, str]:
        return tuple(str(self.values[name]) for name in KEY_FIELDS)  # type: ignore[return-value]


def flag_kind(reason: str) -> str:
    """Which of the three kinds a flag reason is: "unknown", "conflict" or "third-party only" ("unknown" when it is none
    of them, the conservative reading: treat the value as not known)."""
    text = reason.strip().lower()
    if text.startswith("conflict"):
        return "conflict"
    if text.startswith("third-party only"):
        return "third-party only"
    return "unknown"


# ---- reading one cell -------------------------------------------------------------------------------------------


def parse_cell(rule: RuleField, text: str) -> tuple[Any, str | None]:
    """A CSV cell as (typed value, note). An empty cell is (None, None). A cell that is not a clean value of the column's
    type is (None, the text): it is kept for the person to read and never turned into a number."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None, None
    if rule.kind == "text":
        return cleaned, None
    if rule.kind == "choice":
        return (cleaned, None) if cleaned in rule.choices else (None, cleaned)
    if rule.kind == "date":
        if _DATE.fullmatch(cleaned):
            try:
                return date.fromisoformat(cleaned), None
            except ValueError:
                return None, cleaned
        return None, cleaned
    if rule.kind == "int":
        return (int(cleaned), None) if _INTEGER.fullmatch(cleaned) else (None, cleaned)
    if not _NUMBER.fullmatch(cleaned):  # money and pct
        return None, cleaned
    number = Decimal(cleaned)
    if rule.kind == "money":
        return to_money(number), None
    return number, None


def coerce(rule: RuleField, value: Any) -> Any:
    """A value from an API body or a database row, as the field's type. Raises ValueError with a sentence for a person."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if rule.kind in ("text", "choice"):
        if not isinstance(value, str):
            raise ValueError("must be text")
        text = value.strip()
        if len(text) > MAX_TEXT:
            raise ValueError(f"must be at most {MAX_TEXT} characters")
        if rule.kind == "choice" and text not in rule.choices:
            raise ValueError(f"must be one of: {', '.join(rule.choices)}")
        return text
    if rule.kind == "date":
        if isinstance(value, date):
            return value
        if isinstance(value, str) and _DATE.fullmatch(value.strip()):
            try:
                return date.fromisoformat(value.strip())
            except ValueError:
                pass
        raise ValueError("must be a date (YYYY-MM-DD)")
    if rule.kind == "int":
        if isinstance(value, bool):
            raise ValueError("must be a whole number")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
            number = int(value.strip())
        else:
            raise ValueError("must be a whole number, 0 or more")
        if number < 0 or number > MAX_INT:
            raise ValueError("must be a whole number, 0 or more")
        return number
    # money and pct: exact decimals, given as strings (or Decimals from the database)
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        try:
            number = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("must be a number") from None
    else:
        raise ValueError("must be a number")
    if rule.kind == "money":
        if number < 0:
            raise ValueError("cannot be negative")
        if number > MAX_MONEY:
            raise ValueError("is too large")
        return to_money(number)
    if number <= 0 or number > 100:
        raise ValueError("must be more than 0 and at most 100")
    return number.quantize(Decimal("0.01"))


# ---- reading the CSV ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CsvProblem:
    line: int
    message: str


@dataclass
class ParsedCsv:
    rows: list[RuleValues]
    problems: list[CsvProblem]


def parse_csv(text: str) -> ParsedCsv:
    """Every row of the rules CSV, or the reasons rows were refused. The header must be exactly `CSV_COLUMNS`."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿"), newline=""))
    try:
        header = next(reader)
    except StopIteration:
        return ParsedCsv([], [CsvProblem(1, "The file is empty.")])
    if tuple(h.strip() for h in header) != CSV_COLUMNS:
        missing = [c for c in CSV_COLUMNS if c not in header]
        extra = [h for h in header if h not in CSV_COLUMNS]
        detail = "; ".join(
            part
            for part in (
                f"missing {', '.join(missing)}" if missing else "",
                f"unexpected {', '.join(extra)}" if extra else "",
            )
            if part
        )
        return ParsedCsv(
            [],
            [CsvProblem(1, f"The header is not the rules CSV's 48 columns in order{': ' + detail if detail else ''}.")],
        )
    rows: list[RuleValues] = []
    problems: list[CsvProblem] = []
    seen: dict[tuple[str, ...], int] = {}
    for record in reader:
        line = reader.line_num
        if not any(cell.strip() for cell in record):
            continue
        if len(record) != len(CSV_COLUMNS):
            problems.append(CsvProblem(line, f"has {len(record)} cells, not {len(CSV_COLUMNS)}."))
            continue
        cells = dict(zip(CSV_COLUMNS, record))
        parsed = _row(cells)
        if isinstance(parsed, str):
            problems.append(CsvProblem(line, parsed))
            continue
        key = parsed.key()
        if key in seen:
            problems.append(CsvProblem(line, f"repeats the rule set on line {seen[key]}."))
            continue
        seen[key] = line
        rows.append(parsed)
    return ParsedCsv(rows, problems)


def _row(cells: Mapping[str, str]) -> RuleValues | str:
    values: dict[str, Any] = {}
    notes: dict[str, str] = {}
    for rule in FIELDS:
        value, note = parse_cell(rule, cells[rule.name])
        values[rule.name] = value
        if note is not None:
            notes[rule.name] = note
    for name in KEY_FIELDS:
        if values[name] is None:
            return f"has no {BY_NAME[name].label.lower()}" + (
                f" it can use ('{notes[name]}')." if name in notes else "."
            )
    flagged = [f.strip() for f in cells["flagged_fields"].split("|")] if cells["flagged_fields"].strip() else []
    reasons = [r.strip() for r in cells["flag_reasons"].split("|")] if cells["flag_reasons"].strip() else []
    if len(flagged) != len(reasons):
        return f"names {len(flagged)} flagged fields but gives {len(reasons)} reasons."
    unknown = [f for f in flagged if f not in BY_NAME]
    if unknown:
        return f"flags a field that does not exist: {', '.join(unknown)}."
    return RuleValues(values=values, flagged_fields=flagged, flag_reasons=reasons, source_notes=notes)


# ---- checking a rule set ------------------------------------------------------------------------------------------


def coerce_all(raw: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Every field of `raw` (missing ones are empty) as its type, and the fields that could not be read, with why."""
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for rule in FIELDS:
        try:
            values[rule.name] = coerce(rule, raw.get(rule.name))
        except ValueError as problem:
            values[rule.name] = None
            errors[rule.name] = f"{rule.label} {problem}."
    return values, errors


def check(values: Mapping[str, Any]) -> dict[str, str]:
    """What stops a person's rule set from being saved: a missing name, and a rule that is switched on without what it
    needs. These are real errors; a flagged or unclear value is only a warning and is never refused here. The fields are
    assumed to be typed already (`coerce_all`)."""
    errors: dict[str, str] = {}
    for name in KEY_FIELDS:
        if values.get(name) in (None, ""):
            errors[name] = f"{BY_NAME[name].label} is required."
    if values.get("drawdown_type") and values.get("max_loss_usd") is None:
        errors["max_loss_usd"] = "Give the max loss, or clear the drawdown type."
    if values.get("daily_loss_type") in ("soft", "hard") and values.get("daily_loss_limit_usd") is None:
        errors["daily_loss_limit_usd"] = "Give the daily loss limit, or set the type to none."
    basis = values.get("consistency_basis")
    if basis and basis not in NO_CONSISTENCY and values.get("consistency_pct") is None:
        errors["consistency_pct"] = "Give the consistency percentage, or set what it is measured against to none."
    if values.get("consistency_pct") is not None and (not basis or basis in NO_CONSISTENCY):
        errors["consistency_basis"] = "Choose what the consistency percentage is measured against, or clear it."
    buffer, max_loss = values.get("payout_buffer_usd"), values.get("max_loss_usd")
    if buffer is not None and max_loss is not None and 0 < buffer < max_loss:
        errors["payout_buffer_usd"] = (
            "The buffer is below the max loss. Check it: it is usually the max loss plus $100."
        )
    if values.get("qualifying_day_min_profit_usd") is not None and values.get("qualifying_days_required") is None:
        errors["qualifying_days_required"] = "Give how many qualifying days are needed, or clear the daily minimum."
    return errors


def size_in_dollars(label: str) -> Decimal | None:
    """An account size label as dollars: "50K" is 50000. Anything else ("all", "unknown") is None."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)K", label.strip(), flags=re.IGNORECASE)
    return Decimal(match[1]) * 1000 if match else None
