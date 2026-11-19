"""Which platform wrote a file, told from its header row.

The Import page asks the trader where the files come from. This check catches the wrong answer ("these look like
Tradovate files, not TradeSea"), and reads files dropped onto the page without the question (`AUTO`). The Round Turn
Monitor's Export file and old log count as the Round Turn Monitor's, so its reader can say which file to choose instead.
Roundturn's own import file (the Other platform route) is told by its `roundturn_version` column, so its reader can say
what is wrong with the rest of its header.
"""

from __future__ import annotations

import csv

from ..parser import REQUIRED_COLUMNS as TRADOVATE_COLUMNS
from ..parser import _decode
from . import other, round_turn_monitor, tradesea

TRADOVATE, TRADESEA, ROUND_TURN_MONITOR = "tradovate", "tradesea", "round_turn_monitor"
OTHER = "other"  # Roundturn's own import file, written by an AI chatbot from another platform's export
PLATFORMS = (TRADOVATE, TRADESEA, ROUND_TURN_MONITOR, OTHER)
AUTO = "auto"  # tell from the files themselves
NAMES = {
    TRADOVATE: "Tradovate",
    TRADESEA: "TradeSea",
    ROUND_TURN_MONITOR: "the Round Turn Monitor (NinjaTrader)",
    OTHER: "Roundturn's import file (Other platform)",
}
SOURCES = {  # `imports.source` -> platform
    "tradovate_performance": TRADOVATE,
    tradesea.SOURCE: TRADESEA,
    round_turn_monitor.SOURCE: ROUND_TURN_MONITOR,
    other.SOURCE: OTHER,
}

NEITHER = (
    "Roundturn reads Tradovate's Performance report, TradeSea's Orders export (the Filled view), the Round Turn "
    "Monitor's log from NinjaTrader, and its own import file. This file is none of them. For any other platform, "
    "choose Other platform on the Import page: it gives you a prompt that turns your export into Roundturn's import "
    "file with an AI chatbot."
)
MIXED = "These files come from different platforms. Choose files from one platform at a time."

_HEAD_BYTES = 64 * 1024  # the header row is in the first few hundred bytes
# The log, and the two files its reader refuses in words: the Export button's file and the old 19-column log.
_ROUND_TURN_MONITOR_FILES = (
    round_turn_monitor.COLUMNS,
    round_turn_monitor.EXPORT_MARKERS,
    round_turn_monitor.OLD_COLUMNS,
)


def platform_of(data: bytes | str) -> str | None:
    """The platform whose reader the file's header is for (it has every column that reader needs), else None."""
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
    if any(header.issuperset(columns) for columns in _ROUND_TURN_MONITOR_FILES):
        return ROUND_TURN_MONITOR
    if "roundturn_version" in {column.lower() for column in header}:
        return OTHER
    return None


def platform_of_files(datas: list[bytes]) -> tuple[str | None, str | None]:
    """The one platform these files all come from, or None and the reason in words."""
    found = {platform_of(data) for data in datas}
    if None in found:
        return None, NEITHER
    if len(found) > 1:
        return None, MIXED
    return found.pop(), None
