"""Which platform wrote a file, told from its header row.

The Import page asks the trader where the files come from. This check catches the wrong answer ("these look like
Tradovate files, not TradeSea"), and reads files dropped onto the page without the question (`AUTO`).
"""

from __future__ import annotations

import csv

from ..parser import REQUIRED_COLUMNS as TRADOVATE_COLUMNS
from ..parser import _decode
from . import tradesea

TRADOVATE, TRADESEA = "tradovate", "tradesea"
PLATFORMS = (TRADOVATE, TRADESEA)
AUTO = "auto"  # tell from the files themselves
NAMES = {TRADOVATE: "Tradovate", TRADESEA: "TradeSea"}
SOURCES = {"tradovate_performance": TRADOVATE, tradesea.SOURCE: TRADESEA}  # `imports.source` -> platform

NEITHER = (
    "Roundturn reads Tradovate's Performance report and TradeSea's Orders export (the Filled view). "
    "This file is neither."
)
MIXED = "These files come from different platforms. Choose files from one platform at a time."

_HEAD_BYTES = 64 * 1024  # the header row is in the first few hundred bytes


def platform_of(data: bytes | str) -> str | None:
    """`TRADOVATE` or `TRADESEA` when the file's header has every column that platform's reader needs, else None."""
    head = data[:_HEAD_BYTES]
    if len(head) < len(data):  # end on a line break, so no character is cut in half
        head = head[: max(head.rfind(b"\n"), 0)] if isinstance(head, bytes) else head[: max(head.rfind("\n"), 0)]
    text = _decode(head)
    first = next((line for line in text.splitlines() if line.strip()), "")
    if not first:
        return None
    header = {column.strip() for column in next(csv.reader([first]))}
    if header.issuperset(TRADOVATE_COLUMNS):
        return TRADOVATE
    if header.issuperset(tradesea.COLUMNS):
        return TRADESEA
    return None


def platform_of_files(datas: list[bytes]) -> tuple[str | None, str | None]:
    """The one platform these files all come from, or None and the reason in words."""
    found = {platform_of(data) for data in datas}
    if None in found:
        return None, NEITHER
    if len(found) > 1:
        return None, MIXED
    return found.pop(), None
