"""Reading the prop-firm rules CSV and checking a rule set (`prop_rules.py`)."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tradejournal import prop_rules as pr

SEED = Path(__file__).resolve().parents[2] / "docs" / "prop-rules" / "prop_firm_rules_v2.csv"


def csv_text(*rows: dict[str, str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=pr.CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in pr.CSV_COLUMNS})
    return out.getvalue()


ROW = {
    "firm": "Apex Trader Funding",
    "program": "EOD Trailing (Apex 4.0)",
    "account_size": "50K",
    "phase": "funded",
    "rule_version": "Apex 4.0 (new accounts)",
    "effective_from": "2026-03-01",
    "max_loss_usd": "2000",
    "drawdown_type": "eod_trailing_realtime_enforced",
    "max_contracts_minis": "4",
    "consistency_pct": "50",
    "consistency_basis": "best_day_vs_cycle_profit",
    "payout_buffer_usd": "2100",
    "activation_fee_usd": "",
    "price_usd": "79.99 (59.99 with DLL add-on)",
    "flat_by_time": "4:59 PM ET (third party)",
    "flagged_fields": "activation_fee_usd|drawdown_after_payout|flat_by_time",
    "flag_reasons": "conflict: ETF says $99; PTV says $129|unknown: not stated|third-party only (Tanto)",
}


def test_there_are_46_rule_fields_in_the_csv_order_then_the_two_flag_columns():
    assert len(pr.FIELDS) == 46 and len(pr.CSV_COLUMNS) == 48
    assert pr.FIELD_NAMES[:6] == ("firm", "program", "account_size", "phase", "rule_version", "effective_from")
    assert pr.CSV_COLUMNS[-2:] == ("flagged_fields", "flag_reasons")
    assert {f.group for f in pr.FIELDS} == set(pr.GROUPS)


def test_a_clean_cell_is_typed_and_anything_else_is_kept_as_text_never_guessed():
    parsed = pr.parse_csv(csv_text(ROW))
    assert parsed.problems == []
    [row] = parsed.rows
    v = row.values
    assert v["effective_from"] == date(2026, 3, 1)
    assert v["max_loss_usd"] == Decimal("2000.00") and v["max_contracts_minis"] == 4
    assert v["consistency_pct"] == Decimal("50") and v["consistency_basis"] == "best_day_vs_cycle_profit"
    assert v["price_usd"] is None and row.source_notes == {"price_usd": "79.99 (59.99 with DLL add-on)"}
    assert v["activation_fee_usd"] is None and v["drawdown_after_payout"] is None  # blanked by the research
    assert v["flat_by_time"] == "4:59 PM ET (third party)"  # a third-party value is kept
    assert row.flagged_fields == ["activation_fee_usd", "drawdown_after_payout", "flat_by_time"]
    assert [pr.flag_kind(r) for r in row.flag_reasons] == ["conflict", "unknown", "third-party only"]
    assert row.key() == ("Apex Trader Funding", "EOD Trailing (Apex 4.0)", "50K", "funded", "Apex 4.0 (new accounts)")


@pytest.mark.parametrize(
    ("field", "text", "value", "note"),
    [
        ("max_loss_usd", "up to 1500", None, "up to 1500"),
        ("max_loss_usd", "1500.5", Decimal("1500.50"), None),
        ("qualifying_days_required", "none", None, "none"),
        ("qualifying_days_required", "5", 5, None),
        ("min_trading_days", "5 (1 with One Day to Pass add-on)", None, "5 (1 with One Day to Pass add-on)"),
        ("effective_from", "before 2026-03-01", None, "before 2026-03-01"),
        ("effective_from", "unknown", None, "unknown"),
        ("consistency_basis", "something new", None, "something new"),
        ("consistency_basis", "none stated", "none stated", None),
        ("payout_cap_schedule", " 1:1500|2:2000 ", "1:1500|2:2000", None),
        ("price_usd", "", None, None),
    ],
)
def test_cells(field, text, value, note):
    assert pr.parse_cell(pr.BY_NAME[field], text) == (value, note)


def test_the_header_must_be_the_48_columns_in_order():
    wrong = csv_text(ROW).replace("firm,program", "program,firm", 1)
    parsed = pr.parse_csv(wrong)
    assert parsed.rows == [] and "48 columns in order" in parsed.problems[0].message
    assert pr.parse_csv("").problems[0].message == "The file is empty."


def test_rows_that_cannot_be_used_are_refused_with_their_line_and_the_rest_are_read():
    other = {**ROW, "account_size": "100K"}
    text = csv_text(
        ROW,
        {**ROW, "phase": "sim"},  # not a phase
        {**ROW, "flagged_fields": "price_usd", "flag_reasons": "unknown|unknown"},
        {**ROW, "flagged_fields": "prize_usd", "flag_reasons": "unknown"},
        ROW,  # the same rule set again
        other,
    )
    parsed = pr.parse_csv(text)
    assert [r.values["account_size"] for r in parsed.rows] == ["50K", "100K"]
    assert [(p.line, p.message) for p in parsed.problems] == [
        (3, "has no phase it can use ('sim')."),
        (4, "names 1 flagged fields but gives 2 reasons."),
        (5, "flags a field that does not exist: prize_usd."),
        (6, "repeats the rule set on line 2."),
    ]


def test_a_byte_order_mark_and_windows_line_endings_are_fine():
    parsed = pr.parse_csv("﻿" + csv_text(ROW).replace("\n", "\r\n"))
    assert len(parsed.rows) == 1 and parsed.problems == []


# ---- checking a person's rule set ---------------------------------------------------------------------------------


def typed(**values) -> dict:
    raw = {"firm": "X", "program": "Y", "account_size": "50K", "phase": "funded", "rule_version": "v1", **values}
    coerced, errors = pr.coerce_all(raw)
    assert errors == {}
    return coerced


@pytest.mark.parametrize(
    ("values", "errors"),
    [
        ({}, set()),
        ({"drawdown_type": "static"}, {"max_loss_usd"}),
        ({"daily_loss_type": "hard"}, {"daily_loss_limit_usd"}),
        ({"daily_loss_type": "optional"}, set()),  # optional with no amount: not bought
        ({"consistency_basis": "best_day_vs_total_profit"}, {"consistency_pct"}),
        ({"consistency_pct": "40"}, {"consistency_basis"}),
        ({"consistency_pct": "40", "consistency_basis": "none"}, {"consistency_basis"}),
        ({"payout_buffer_usd": "1000", "max_loss_usd": "2000"}, {"payout_buffer_usd"}),
        ({"payout_buffer_usd": "0", "max_loss_usd": "2000"}, set()),  # no buffer at all is a real rule (TradeDay)
        ({"payout_buffer_usd": "2100", "max_loss_usd": "2000"}, set()),
        ({"qualifying_day_min_profit_usd": "150"}, {"qualifying_days_required"}),
        ({"firm": ""}, {"firm"}),
    ],
)
def test_check_refuses_a_rule_that_is_on_without_what_it_needs(values, errors):
    raw = {"firm": "X", "program": "Y", "account_size": "50K", "phase": "funded", "rule_version": "v1", **values}
    coerced, problems = pr.coerce_all(raw)
    assert problems == {}
    assert set(pr.check(coerced)) == errors


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_loss_usd", "-5", "Max loss cannot be negative."),
        ("max_loss_usd", 2000, "Max loss must be a number."),  # a JSON number: money is always a string
        ("consistency_pct", "0", "Consistency % must be more than 0 and at most 100."),
        ("consistency_pct", "100.5", "Consistency % must be more than 0 and at most 100."),
        ("min_trading_days", "-1", "Minimum trading days must be a whole number, 0 or more."),
        ("min_trading_days", True, "Minimum trading days must be a whole number."),
        ("phase", "sim", "Phase must be one of: evaluation, funded, live."),
        ("effective_from", "2026-02-30", "Effective from must be a date (YYYY-MM-DD)."),
        ("notes", "x" * 4001, "Notes must be at most 4000 characters."),
    ],
)
def test_coerce_refuses_values_of_the_wrong_kind_in_words(field, value, message):
    _, errors = pr.coerce_all({field: value})
    assert errors[field] == message


def test_coerce_keeps_cents_and_two_decimals_of_a_percentage():
    values = typed(max_loss_usd="2000.005", consistency_pct="33.333", min_trading_days=5)
    assert values["max_loss_usd"] == Decimal("2000.01") and values["consistency_pct"] == Decimal("33.33")
    assert values["min_trading_days"] == 5


@pytest.mark.parametrize(("label", "dollars"), [("50K", Decimal(50000)), ("150k", Decimal(150000)), ("all", None)])
def test_size_in_dollars(label, dollars):
    assert pr.size_in_dollars(label) == dollars


# ---- the seed itself, when this checkout has it (docs/prop-rules is not committed) --------------------------------


@pytest.mark.skipif(not SEED.exists(), reason="the seed CSV is not in this checkout")
def test_the_seed_csv_reads_whole_and_its_blanked_flags_are_empty():
    parsed = pr.parse_csv(SEED.read_text(encoding="utf-8"))
    assert parsed.problems == [] and len(parsed.rows) == 371
    assert len({row.key() for row in parsed.rows}) == 371
    for row in parsed.rows:
        for name, reason in zip(row.flagged_fields, row.flag_reasons):
            if pr.flag_kind(reason) in ("unknown", "conflict"):
                assert row.values[name] is None and name not in row.source_notes, (row.key(), name)
