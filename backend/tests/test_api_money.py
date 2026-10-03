from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradejournal.api.schemas import MONEY_PATTERN, ApiModel, Money


class Entry(ApiModel):
    amount: Money
    note: str = ""


@pytest.mark.parametrize("text", ["-475.00", "625.00", "0", "0.000001", "123456789012345678901.12345"])
def test_money_accepts_decimal_strings_and_keeps_them_exact(text):
    assert Entry.model_validate({"amount": text}).amount == Decimal(text)


@pytest.mark.parametrize(
    "bad", [475, 475.0, -1, True, None, "", "1e3", "1,000.00", "$5", " 5", "5 ", "NaN", ".5", "5."]
)
def test_money_rejects_everything_but_a_plain_decimal_string(bad):
    with pytest.raises(ValidationError):
        Entry.model_validate({"amount": bad})


def test_money_serializes_to_a_string_that_never_uses_exponent_notation():
    assert Entry(amount=Decimal("-475.00")).model_dump(mode="json")["amount"] == "-475.00"
    assert Entry(amount=Decimal("1E+3")).model_dump_json() == '{"amount":"1000","note":""}'


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_money_is_a_string_in_both_directions_of_the_schema(mode):
    # This is what keeps the generated TypeScript from accepting a JavaScript number (doc 07 section 2).
    schema = Entry.model_json_schema(mode=mode)["properties"]["amount"]
    assert schema["type"] == "string"
    assert schema["pattern"] == MONEY_PATTERN
    assert "anyOf" not in schema


def test_defaulted_fields_are_required_in_responses_but_optional_in_requests():
    # The pairing with `openapi-typescript --default-non-nullable false` depends on both of these.
    assert Entry.model_json_schema(mode="validation")["required"] == ["amount"]
    assert Entry.model_json_schema(mode="serialization")["required"] == ["amount", "note"]
