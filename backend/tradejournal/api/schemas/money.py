"""Money crosses the API as a JSON *string* and is an exact Decimal on the server.

Pydantic documents a plain `Decimal` as `number | string` in *input* (validation) schemas, which would make the
generated TypeScript types accept a JavaScript float. `Money` pins the schema to a string in both directions and
rejects JSON numbers outright (spike S1, doc 07 section 2).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

MONEY_PATTERN = r"^-?\d+(\.\d+)?$"
_MONEY_RE = re.compile(MONEY_PATTERN)


def _parse_money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    # bool is an int subclass; ints and floats are rejected on purpose, since a JSON number has already been a float.
    if not isinstance(value, str) or not _MONEY_RE.fullmatch(value):
        raise ValueError("money must be a decimal string such as '-475.00', never a JSON number")
    return Decimal(value)


Money = Annotated[
    Decimal,
    BeforeValidator(_parse_money),
    PlainSerializer(lambda amount: format(amount, "f"), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": MONEY_PATTERN, "examples": ["-475.00"]}),
]
