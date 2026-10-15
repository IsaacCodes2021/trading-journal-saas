"""The two shapes of plan code and how what a person typed is looked up (no database: `pg/plans.py`'s pure helpers)."""

from __future__ import annotations

import pytest

from tradejournal.pg.plans import generate_code, lookup_forms, typed_code


@pytest.mark.parametrize(
    ("typed", "forms"),
    [
        ("FREE30", ["FREE30"]),
        ("free30", ["FREE30"]),
        ("  Free30\t", ["FREE30"]),
        ("free 30", ["FREE-30", "FREE30"]),  # the dashed form is a random code's shape, so it is tried first
        ("free_30", ["FREE-30", "FREE30"]),
        ("f r e e 3 0", ["FREE30"]),
        ("gr 7kq2-m9xa", ["GR-7KQ2-M9XA", "GR7KQ2M9XA"]),
        ("GR-7KQ2-M9XA", ["GR-7KQ2-M9XA", "GR7KQ2M9XA"]),
    ],
)
def test_what_a_person_typed_is_looked_up_in_these_forms_in_this_order(typed, forms):
    assert lookup_forms(typed) == forms


@pytest.mark.parametrize(
    "typed", [None, "", "  ", "AB", "X" * 21, "FREE30!", "FRÉE30", "-" * 5, "A" * 41, "AB-" * 13 + "AB"]
)
def test_what_cannot_be_any_code_is_not_looked_up_at_all(typed):
    assert lookup_forms(typed) == []


@pytest.mark.parametrize(("text", "stored"), [("free30", "FREE30"), (" Launch7 ", "LAUNCH7"), ("abc", "ABC")])
def test_an_operator_typed_code_is_kept_in_capitals(text, stored):
    assert typed_code(text) == stored


@pytest.mark.parametrize("text", ["ab", "FREE 30", "FREE-30", "FREE_30", "X" * 21, "", "FRÉE30"])
def test_an_operator_typed_code_of_another_shape_is_refused_not_tidied(text):
    assert typed_code(text) is None


def test_a_random_code_is_never_mistaken_for_a_typed_one():
    code = generate_code()
    assert code.startswith("GR-") and typed_code(code) is None and lookup_forms(code)[0] == code
