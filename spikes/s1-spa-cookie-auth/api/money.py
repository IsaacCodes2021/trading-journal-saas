"""Money crosses the API as a JSON *string* and is exact Decimal on the server.

Pydantic documents a plain `Decimal` as `number | string` in *input* (validation) schemas, which would make
generated TypeScript types accept a JavaScript float. `Money` pins the schema to a string in both
directions and rejects JSON numbers outright.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

_PATTERN = r"^-?\d+(\.\d+)?$"
_RE = re.compile(_PATTERN)


def _parse_money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    # bool is an int subclass; ints and floats are rejected on purpose.
    if not isinstance(value, str) or not _RE.fullmatch(value):
        raise ValueError("money must be a decimal string such as '-475.00', never a JSON number")
    return Decimal(value)


Money = Annotated[
    Decimal,
    BeforeValidator(_parse_money),
    PlainSerializer(lambda d: format(d, "f"), return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": _PATTERN, "examples": ["-475.00"]}),
]
